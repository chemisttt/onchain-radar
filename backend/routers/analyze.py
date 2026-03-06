import asyncio
import json
from datetime import datetime, timezone
from fastapi import APIRouter
import aiohttp

from db import get_db
from services import goplus, honeypot, rugcheck, dexscreener

router = APIRouter()

CACHE_TTL_SECONDS = 600  # 10 min


def _compute_risk_score(gp: dict, hp: dict, rc: dict, token_data: dict) -> dict:
    """
    Compute risk score 0-100 across 4 categories of 25 points each.
    Higher score = safer.
    """
    categories = {}
    red_flags = []

    # ── Contract (0-25) ──
    contract_score = 25
    contract_flags = []

    if gp.get("is_open_source"):
        contract_flags.append({"ok": True, "msg": "Contract verified / open source"})
    else:
        contract_score -= 8
        contract_flags.append({"ok": False, "msg": "Contract not verified"})
        red_flags.append({"severity": "high", "msg": "Contract source code not verified"})

    if gp.get("is_mintable"):
        contract_score -= 7
        contract_flags.append({"ok": False, "msg": "Mint function: owner can mint new tokens"})
        red_flags.append({"severity": "high", "msg": "Mint function enabled"})
    else:
        contract_flags.append({"ok": True, "msg": "No mint function"})

    if gp.get("is_proxy"):
        contract_score -= 5
        contract_flags.append({"ok": False, "msg": "Proxy contract (upgradeable)"})
        red_flags.append({"severity": "medium", "msg": "Proxy contract — code can be changed"})
    else:
        contract_flags.append({"ok": True, "msg": "No proxy pattern"})

    if gp.get("can_take_back_ownership"):
        contract_score -= 3
        contract_flags.append({"ok": False, "msg": "Owner can reclaim ownership"})
    if gp.get("hidden_owner"):
        contract_score -= 3
        contract_flags.append({"ok": False, "msg": "Hidden owner detected"})
        red_flags.append({"severity": "medium", "msg": "Hidden owner detected"})

    if gp.get("is_blacklisted"):
        contract_score -= 3
        contract_flags.append({"ok": False, "msg": "Blacklist function exists"})

    contract_score = max(0, contract_score)
    categories["contract"] = {"score": contract_score, "max": 25, "flags": contract_flags}

    # ── Liquidity (0-25) ──
    liq_score = 25
    liq_flags = []

    pair_data = token_data.get("data", {})
    liq_usd = float(pair_data.get("liquidity", {}).get("usd", 0) or 0)

    if liq_usd >= 100_000:
        liq_flags.append({"ok": True, "msg": f"Liquidity ${liq_usd:,.0f}"})
    elif liq_usd >= 10_000:
        liq_score -= 5
        liq_flags.append({"ok": True, "msg": f"Moderate liquidity ${liq_usd:,.0f}"})
    elif liq_usd > 0:
        liq_score -= 15
        liq_flags.append({"ok": False, "msg": f"Low liquidity ${liq_usd:,.0f}"})
        red_flags.append({"severity": "high", "msg": f"Very low liquidity: ${liq_usd:,.0f}"})
    else:
        liq_score -= 20
        liq_flags.append({"ok": False, "msg": "No liquidity data"})

    # Pool age
    created = pair_data.get("pair_created_at")
    if created:
        try:
            age_hours = (datetime.now(timezone.utc).timestamp() * 1000 - float(created)) / 3_600_000
            if age_hours < 24:
                liq_score -= 5
                liq_flags.append({"ok": False, "msg": f"Pool age: {age_hours:.0f}h (very new)"})
                red_flags.append({"severity": "medium", "msg": "Pool less than 24h old"})
            elif age_hours < 168:
                liq_score -= 2
                liq_flags.append({"ok": True, "msg": f"Pool age: {age_hours / 24:.0f}d"})
            else:
                liq_flags.append({"ok": True, "msg": f"Pool age: {age_hours / 24:.0f}d"})
        except (ValueError, TypeError):
            pass

    lp_holders = gp.get("lp_holder_count", 0)
    if lp_holders and lp_holders > 5:
        liq_flags.append({"ok": True, "msg": f"LP holders: {lp_holders}"})
    elif lp_holders:
        liq_score -= 3
        liq_flags.append({"ok": False, "msg": f"Few LP holders: {lp_holders}"})

    liq_score = max(0, liq_score)
    categories["liquidity"] = {"score": liq_score, "max": 25, "flags": liq_flags}

    # ── Holders (0-25) ──
    holder_score = 25
    holder_flags = []

    holder_count = gp.get("holder_count", 0)
    if holder_count and holder_count >= 1000:
        holder_flags.append({"ok": True, "msg": f"Holders: {holder_count:,}"})
    elif holder_count and holder_count >= 100:
        holder_score -= 5
        holder_flags.append({"ok": True, "msg": f"Holders: {holder_count:,}"})
    elif holder_count:
        holder_score -= 12
        holder_flags.append({"ok": False, "msg": f"Few holders: {holder_count}"})
        red_flags.append({"severity": "medium", "msg": f"Only {holder_count} holders"})
    else:
        holder_score -= 5
        holder_flags.append({"ok": False, "msg": "Holder count unknown"})

    # RugCheck top holders (Solana)
    rc_holders = rc.get("top_holders", [])
    if rc_holders:
        top10_pct = sum(float(h.get("pct", h.get("percentage", 0)) or 0) for h in rc_holders[:10])
        if top10_pct > 0:
            if top10_pct > 50:
                holder_score -= 10
                holder_flags.append({"ok": False, "msg": f"Top 10 holders own {top10_pct:.0f}%"})
                red_flags.append({"severity": "high", "msg": f"Top 10 holders own {top10_pct:.0f}%"})
            elif top10_pct > 30:
                holder_score -= 5
                holder_flags.append({"ok": False, "msg": f"Top 10 holders own {top10_pct:.0f}%"})
            else:
                holder_flags.append({"ok": True, "msg": f"Top 10 holders own {top10_pct:.0f}%"})

    owner = gp.get("owner_address", "")
    if owner and owner != "0x0000000000000000000000000000000000000000":
        holder_flags.append({"ok": False, "msg": f"Owner: {owner[:10]}..."})
        holder_score -= 3
    elif owner == "0x0000000000000000000000000000000000000000":
        holder_flags.append({"ok": True, "msg": "Ownership renounced"})

    holder_score = max(0, holder_score)
    categories["holders"] = {"score": holder_score, "max": 25, "flags": holder_flags}

    # ── Trading (0-25) ──
    trade_score = 25
    trade_flags = []

    is_hp = gp.get("is_honeypot") or hp.get("is_honeypot")
    if is_hp:
        trade_score -= 25
        trade_flags.append({"ok": False, "msg": "HONEYPOT DETECTED"})
        red_flags.append({"severity": "critical", "msg": "Honeypot detected — cannot sell"})
    else:
        trade_flags.append({"ok": True, "msg": "No honeypot detected"})

    buy_tax = max(gp.get("buy_tax", 0) or 0, hp.get("buy_tax", 0) or 0)
    sell_tax = max(gp.get("sell_tax", 0) or 0, hp.get("sell_tax", 0) or 0)

    if sell_tax > 0.1:
        trade_score -= 10
        trade_flags.append({"ok": False, "msg": f"Sell tax {sell_tax * 100:.1f}%"})
        red_flags.append({"severity": "high", "msg": f"High sell tax: {sell_tax * 100:.1f}%"})
    elif sell_tax > 0.05:
        trade_score -= 5
        trade_flags.append({"ok": False, "msg": f"Sell tax {sell_tax * 100:.1f}%"})
    elif sell_tax > 0:
        trade_flags.append({"ok": True, "msg": f"Sell tax {sell_tax * 100:.1f}%"})
    else:
        trade_flags.append({"ok": True, "msg": "No sell tax"})

    if buy_tax > 0.05:
        trade_score -= 5
        trade_flags.append({"ok": False, "msg": f"Buy tax {buy_tax * 100:.1f}%"})
    elif buy_tax > 0:
        trade_flags.append({"ok": True, "msg": f"Buy tax {buy_tax * 100:.1f}%"})
    else:
        trade_flags.append({"ok": True, "msg": "No buy tax"})

    if gp.get("cannot_sell_all"):
        trade_score -= 8
        trade_flags.append({"ok": False, "msg": "Cannot sell all tokens"})
        red_flags.append({"severity": "high", "msg": "Cannot sell all tokens at once"})

    # Volume check
    vol_24h = float(pair_data.get("volume", {}).get("h24", 0) or 0)
    if vol_24h >= 50_000:
        trade_flags.append({"ok": True, "msg": f"24h volume ${vol_24h:,.0f}"})
    elif vol_24h > 0:
        trade_score -= 3
        trade_flags.append({"ok": False, "msg": f"Low 24h volume ${vol_24h:,.0f}"})
    else:
        trade_score -= 5
        trade_flags.append({"ok": False, "msg": "No volume data"})

    trade_score = max(0, trade_score)
    categories["trading"] = {"score": trade_score, "max": 25, "flags": trade_flags}

    # ── Total ──
    total = sum(c["score"] for c in categories.values())

    if total >= 80:
        verdict = "LOW_RISK"
    elif total >= 50:
        verdict = "MEDIUM_RISK"
    elif total >= 25:
        verdict = "HIGH_RISK"
    else:
        verdict = "CRITICAL_RISK"

    return {
        "score": total,
        "verdict": verdict,
        "categories": categories,
        "red_flags": sorted(red_flags, key=lambda f: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(f["severity"], 4)),
    }


@router.get("/analyze/{chain}/{address}")
async def analyze_token(chain: str, address: str):
    """
    Unified token analysis: runs all security checks in parallel,
    computes risk score 0-100, returns categorized flags.
    """
    db = get_db()
    addr_lower = address.lower() if chain != "solana" else address

    # Check cache
    row = await db.execute_fetchall(
        "SELECT data, fetched_at FROM analysis_cache WHERE chain = ? AND address = ?",
        (chain, addr_lower),
    )
    if row:
        fetched = datetime.fromisoformat(row[0]["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched.replace(tzinfo=timezone.utc)).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return {**json.loads(row[0]["data"]), "cached": True}

    # Fetch all data in parallel
    async with aiohttp.ClientSession() as session:
        tasks = {}

        # Security checks
        if chain == "solana":
            tasks["goplus"] = goplus.check_solana(session, address)
            tasks["rugcheck"] = rugcheck.check(session, address)
        else:
            tasks["goplus"] = goplus.check_evm(session, chain, address)
            tasks["honeypot"] = honeypot.check(session, chain, address)

        # Token data from DexScreener
        tasks["dexscreener"] = dexscreener.get_token_pairs(session, address)

        results = {}
        gathered = await asyncio.gather(
            *[coro for coro in tasks.values()],
            return_exceptions=True,
        )
        for key, val in zip(tasks.keys(), gathered):
            results[key] = val if not isinstance(val, Exception) else {}

    gp = results.get("goplus", {}) or {}
    hp = results.get("honeypot", {}) or {}
    rc = results.get("rugcheck", {}) or {}

    # Process DexScreener pairs
    pairs = results.get("dexscreener", []) or []
    token_data = {}
    if pairs and isinstance(pairs, list):
        chain_pairs = [p for p in pairs if p.get("chainId") == chain]
        if not chain_pairs:
            chain_pairs = pairs
        best = max(chain_pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        token_data = {
            "data": {
                "pair_address": best.get("pairAddress"),
                "dex": best.get("dexId"),
                "base_token": best.get("baseToken", {}),
                "quote_token": best.get("quoteToken", {}),
                "price_usd": best.get("priceUsd"),
                "volume": best.get("volume", {}),
                "price_change": best.get("priceChange", {}),
                "liquidity": best.get("liquidity", {}),
                "fdv": best.get("fdv"),
                "market_cap": best.get("marketCap"),
                "txns": best.get("txns", {}),
                "pair_created_at": best.get("pairCreatedAt"),
                "url": best.get("url"),
            }
        }

    # Compute risk score
    risk = _compute_risk_score(gp, hp, rc, token_data)

    response = {
        "chain": chain,
        "address": address,
        **risk,
        "token_data": token_data.get("data", {}),
        "raw": {
            "goplus": gp,
            "honeypot": hp,
            "rugcheck": rc,
        },
    }

    # Save to cache
    await db.execute(
        """INSERT OR REPLACE INTO analysis_cache (chain, address, data, fetched_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (chain, addr_lower, json.dumps(response)),
    )
    await db.commit()

    return {**response, "cached": False}
