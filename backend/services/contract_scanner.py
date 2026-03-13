"""Contract vulnerability scanner — polls NEW_PAIR events, checks source for vulns.

Pipeline: feed_events NEW_PAIR → filter factory → GoPlus check → source regex scan → Telegram alert.
Follows service pattern: _task, _poll_loop, start/stop.
"""

import asyncio
import hashlib
import json
import logging
import re
import time

import aiohttp

from config import settings
from db import get_db
from services import etherscan, goplus

log = logging.getLogger("contract_scanner")

_task: asyncio.Task | None = None

# --- Constants ---
SCAN_INTERVAL = 60          # poll every 60s
MIN_LIQUIDITY = 10_000      # $10k minimum to bother scanning
SCAN_COOLDOWN = 86400       # don't re-scan same token within 24h
INITIAL_DELAY = 45          # wait for feed_engine to populate events
FACTORY_THRESHOLD = 5       # bytecode seen N+ times → auto-add to factory_hashes

EVM_CHAINS: dict[str, int] = {
    "ethereum": 1,
    "base": 8453,
    "bsc": 56,
    "arbitrum": 42161,
}

EXPLORER_URLS: dict[str, str] = {
    "ethereum": "https://etherscan.io/address/",
    "base": "https://basescan.org/address/",
    "bsc": "https://bscscan.com/address/",
    "arbitrum": "https://arbiscan.io/address/",
}

# --- State ---
_scanned: dict[str, float] = {}    # "chain:address" → timestamp
_factory_hashes: set[str] = set()  # known factory bytecode hashes
_bytecode_counts: dict[str, list[tuple[str, str]]] = {}  # hash → [(chain, address), ...]


# --- Vulnerability Patterns ---

def _no_owner_check(src: str, match: str) -> bool:
    """FP filter: true if match region lacks owner/whitelist checks (= likely vuln)."""
    return "msg.sender ==" not in match and "onlyOwner" not in match


VULN_PATTERNS: list[dict] = [
    {
        "id": "self_report_auth",
        "name": "Self-Report Authentication",
        "severity": "CRITICAL",
        "description": "Modifier authenticates caller by calling msg.sender (attacker can fake response)",
        "regex": r"modifier\s+\w+[^}]{0,500}msg\.sender\.(staticcall|call)\(",
        "fp_check": lambda src, match: "msg.sender ==" not in match,
    },
    {
        "id": "unprotected_mint",
        "name": "Unprotected Mint Function",
        "severity": "CRITICAL",
        "description": "Public/external mint() without access control",
        "regex": r"function\s+mint\w*\s*\([^)]*\)\s*(external|public)[^}]{0,500}\}",
        "fp_check": lambda src, match: (
            # Must lack any access control
            not re.search(r"only\w+|require\s*\(\s*msg\.sender|_checkRole|hasRole|_onlyRole", match)
            # Must not be ERC4626 vault mint (takes shares+receiver, calls deposit/withdraw internally)
            and "ERC4626" not in src[:500]
            and "IERC4626" not in match
            and "override" not in match  # standard OZ overrides are not free mints
        ),
    },
    {
        "id": "hidden_fee_manipulation",
        "name": "Owner Can Set Extreme Fees",
        "severity": "HIGH",
        "description": "Owner can change tax/fee above 50%",
        "regex": r"function\s+set\w*(Fee|Tax)\w*\s*\([^)]*\)\s*(external|public)[^}]{0,500}\}",
        "fp_check": lambda src, match: not re.search(r"require\s*\([^)]*<=?\s*(5|10|25)\s*\)", match),
    },
    {
        "id": "owner_balance_change",
        "name": "Owner Can Modify Balances",
        "severity": "HIGH",
        "description": "onlyOwner function directly writes _balances mapping",
        "regex": r"function\s+\w+[^}]*onlyOwner[^}]*_balances\s*\[[^]]+\]\s*=[^}]+\}",
        "fp_check": None,
    },
    {
        "id": "hidden_blacklist",
        "name": "Hidden Blacklist in Transfer",
        "severity": "HIGH",
        "description": "Transfer function checks a mapping that owner can modify (hidden blacklist)",
        "regex": r"function\s+_transfer[^}]*require\s*\(\s*!\s*\w*(black|block|banned|excluded)\w*\s*\[",
        "fp_check": None,
    },
    {
        "id": "selfdestruct_present",
        "name": "Selfdestruct Present",
        "severity": "MEDIUM",
        "description": "Contract contains selfdestruct — owner can rug by destroying contract",
        "regex": r"selfdestruct\s*\(",
        "fp_check": lambda src, match: "onlyOwner" in src or "owner" in src,
    },
    {
        "id": "delegatecall_to_variable",
        "name": "Delegatecall to Variable Address",
        "severity": "HIGH",
        "description": "delegatecall to non-hardcoded address — potential upgrade backdoor",
        "regex": r"\.delegatecall\s*\(",
        "fp_check": lambda src, match: "implementation" not in match.lower(),
    },
]


# --- Core Logic ---

def _is_eip1167(bytecode: str) -> bool:
    """Check if bytecode is an EIP-1167 minimal proxy clone."""
    code = bytecode.lower().removeprefix("0x")
    return code.startswith("363d3d373d3d3d363d73")


def _hash_bytecode(bytecode: str) -> str:
    """SHA256 hash of normalized bytecode for factory detection."""
    code = bytecode.lower().removeprefix("0x")
    return hashlib.sha256(code.encode()).hexdigest()[:16]


async def _load_factory_hashes() -> None:
    """Load known factory hashes from DB on startup."""
    try:
        db = get_db()
        rows = await db.execute_fetchall("SELECT bytecode_hash FROM factory_hashes")
        _factory_hashes.clear()
        _factory_hashes.update(row["bytecode_hash"] for row in rows)
        log.info(f"Loaded {len(_factory_hashes)} factory hashes")
    except Exception as e:
        log.warning(f"Failed to load factory hashes: {e}")


async def _maybe_learn_factory(bcode_hash: str, chain: str, address: str) -> bool:
    """Track bytecode hash frequency. If seen FACTORY_THRESHOLD+ times, mark as factory."""
    entries = _bytecode_counts.setdefault(bcode_hash, [])

    # Avoid duplicate entries for same address
    if any(a == address for _, a in entries):
        return bcode_hash in _factory_hashes
    entries.append((chain, address))

    if len(entries) >= FACTORY_THRESHOLD and bcode_hash not in _factory_hashes:
        _factory_hashes.add(bcode_hash)
        try:
            db = get_db()
            await db.execute(
                "INSERT OR IGNORE INTO factory_hashes (bytecode_hash, label, example_address, chain) "
                "VALUES (?, ?, ?, ?)",
                (bcode_hash, f"auto-learned ({len(entries)} instances)", address, chain),
            )
            await db.commit()
            log.info(f"Auto-learned factory hash {bcode_hash} ({len(entries)} instances, e.g. {chain}/{address[:10]})")
        except Exception as e:
            log.warning(f"Failed to save factory hash: {e}")
        return True

    return bcode_hash in _factory_hashes


async def _is_factory_token(session: aiohttp.ClientSession, chain_id: int, chain: str, address: str) -> bool:
    """Check if token is a factory clone (EIP-1167 or repeated bytecode)."""
    bytecode = await etherscan.get_bytecode(session, chain_id, address)
    if not bytecode or bytecode == "0x":
        return True  # no code = EOA or self-destructed → skip

    if _is_eip1167(bytecode):
        return True

    bcode_hash = _hash_bytecode(bytecode)
    return await _maybe_learn_factory(bcode_hash, chain, address)


def _scan_source(source: str) -> list[dict]:
    """Run regex vulnerability patterns against verified source code."""
    findings = []
    for pat in VULN_PATTERNS:
        matches = list(re.finditer(pat["regex"], source, re.DOTALL))
        for m in matches:
            matched_text = m.group(0)
            # Apply false-positive filter if defined
            fp_check = pat.get("fp_check")
            if fp_check and not fp_check(source, matched_text):
                continue
            # Extract snippet (max 200 chars centered on match)
            start = max(0, m.start() - 50)
            end = min(len(source), m.end() + 100)
            snippet = source[start:end].strip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            findings.append({
                "type": pat["id"],
                "name": pat["name"],
                "severity": pat["severity"],
                "description": pat["description"],
                "code_snippet": snippet,
            })
            break  # one match per pattern is enough
    return findings


def _severity_icon(severity: str) -> str:
    icons = {"CRITICAL": "\U0001f534", "HIGH": "\U0001f7e0", "MEDIUM": "\U0001f7e1"}
    return icons.get(severity, "\u26aa")


def _format_alert(token_symbol: str, address: str, chain: str,
                   vulns: list[dict], liquidity: float, goplus_flags: dict,
                   is_verified: bool) -> str:
    """Format HTML alert for Telegram."""
    short_addr = f"{address[:6]}...{address[-4:]}"
    explorer_url = EXPLORER_URLS.get(chain, "https://etherscan.io/address/") + address
    dex_url = f"https://dexscreener.com/{chain}/{address}"

    lines = [
        "\U0001f50d <b>VULNERABILITY FOUND</b>",
        "",
        f"<b>Chain:</b> {chain.capitalize()}",
        f"<b>Token:</b> ${token_symbol} ({short_addr})",
        f"<b>Liquidity:</b> ${liquidity:,.0f}",
        f"<b>Verified:</b> {'✅' if is_verified else '❌'}",
        "",
        "<b>Findings:</b>",
    ]
    for v in vulns:
        icon = _severity_icon(v["severity"])
        lines.append(f"{icon} {v['severity']}: {v['name']}")
        # One-line snippet
        snippet = v.get("code_snippet", "")
        if snippet:
            # Collapse whitespace for readability
            snippet_line = re.sub(r'\s+', ' ', snippet)[:120]
            lines.append(f"  └ <code>{snippet_line}</code>")

    # GoPlus summary
    if goplus_flags:
        gp_parts = []
        if goplus_flags.get("is_honeypot"):
            gp_parts.append("honeypot \U0001f6a8")
        if goplus_flags.get("is_mintable"):
            gp_parts.append("is_mintable ⚠️")
        if goplus_flags.get("hidden_owner"):
            gp_parts.append("hidden_owner ⚠️")
        if goplus_flags.get("can_take_back_ownership"):
            gp_parts.append("takeback_owner ⚠️")
        sell_tax = goplus_flags.get("sell_tax", 0)
        if sell_tax:
            gp_parts.append(f"sell_tax: {sell_tax:.0%}")
        buy_tax = goplus_flags.get("buy_tax", 0)
        if buy_tax:
            gp_parts.append(f"buy_tax: {buy_tax:.0%}")
        if gp_parts:
            lines.append("")
            lines.append(f"<b>GoPlus:</b> {' | '.join(gp_parts)}")

    lines.append("")
    lines.append(f'<a href="{explorer_url}">Explorer</a> | <a href="{dex_url}">DexScreener</a>')
    return "\n".join(lines)


async def _send_alert(text: str, session: aiohttp.ClientSession) -> bool:
    """Send HTML alert to Telegram (separate thread for scanner alerts if configured)."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    thread_id = settings.scanner_telegram_thread_id or settings.telegram_thread_id

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 429:
                body = await resp.json()
                retry = body.get("parameters", {}).get("retry_after", 30)
                log.warning(f"Telegram 429 — backing off {retry}s")
                await asyncio.sleep(retry)
                return False
            if resp.status != 200:
                body = await resp.text()
                log.warning(f"Telegram send failed ({resp.status}): {body[:200]}")
                return False
            return True
    except Exception as e:
        log.warning(f"Telegram send error: {e}")
        return False


async def _save_scan(chain: str, address: str, liquidity: float,
                     is_factory: bool, is_verified: bool,
                     vulns: list[dict], goplus_flags: dict) -> None:
    """Persist scan result to contract_scans table."""
    try:
        db = get_db()
        await db.execute(
            """INSERT OR REPLACE INTO contract_scans
               (chain, address, liquidity_usd, is_factory, is_verified, vulnerabilities, goplus_flags)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (chain, address.lower(), liquidity, int(is_factory), int(is_verified),
             json.dumps(vulns), json.dumps(goplus_flags)),
        )
        await db.commit()
    except Exception as e:
        log.warning(f"Failed to save scan: {e}")


async def _get_pending_tokens() -> list[dict]:
    """Fetch recent NEW_PAIR events from EVM chains, not yet scanned."""
    db = get_db()
    now = time.time()

    # Get NEW_PAIR events from last 24h
    rows = await db.execute_fetchall(
        """SELECT id, chain, token_address, token_symbol, details
           FROM feed_events
           WHERE event_type = 'NEW_PAIR'
             AND created_at >= datetime('now', '-1 day')
           ORDER BY id DESC
           LIMIT 100""",
    )

    pending = []
    for row in rows:
        chain = row["chain"]
        address = row["token_address"]
        if not address or chain not in EVM_CHAINS:
            continue

        scan_key = f"{chain}:{address.lower()}"
        if scan_key in _scanned and now - _scanned[scan_key] < SCAN_COOLDOWN:
            continue

        details = json.loads(row["details"]) if row["details"] else {}
        liquidity = float(details.get("liquidity_usd", 0) or 0)
        if liquidity < MIN_LIQUIDITY:
            continue

        pending.append({
            "chain": chain,
            "chain_id": EVM_CHAINS[chain],
            "address": address,
            "symbol": row["token_symbol"] or "???",
            "liquidity": liquidity,
        })

    return pending


async def _scan_token(session: aiohttp.ClientSession, token: dict) -> None:
    """Full scan pipeline for a single token."""
    chain = token["chain"]
    chain_id = token["chain_id"]
    address = token["address"]
    symbol = token["symbol"]
    liquidity = token["liquidity"]
    scan_key = f"{chain}:{address.lower()}"

    # Step 1: Factory check
    is_factory = await _is_factory_token(session, chain_id, chain, address)
    if is_factory:
        log.debug(f"SKIP factory: {chain}/{symbol} ({address[:10]}...)")
        _scanned[scan_key] = time.time()
        await _save_scan(chain, address, liquidity, is_factory=True,
                         is_verified=False, vulns=[], goplus_flags={})
        return

    # Step 2: GoPlus check
    goplus_flags = await goplus.check_evm(session, chain, address)

    # Step 3: Source code analysis
    vulns = []
    is_verified = False
    source_data = await etherscan.get_contract_source(session, chain_id, address)
    source_code = source_data.get("SourceCode", "")
    if source_code:
        is_verified = True
        vulns = _scan_source(source_code)

    _scanned[scan_key] = time.time()
    await _save_scan(chain, address, liquidity, is_factory=False,
                     is_verified=is_verified, vulns=vulns, goplus_flags=goplus_flags)

    # Step 4: Alert if vulnerabilities found
    if vulns:
        log.info(f"VULNS FOUND: {chain}/{symbol} — {[v['type'] for v in vulns]}")
        alert_text = _format_alert(symbol, address, chain, vulns, liquidity,
                                   goplus_flags, is_verified)
        await _send_alert(alert_text, session)
    else:
        log.info(f"CLEAN: {chain}/{symbol} (verified={is_verified}, goplus={'yes' if goplus_flags else 'no'})")


async def _poll_loop() -> None:
    """Main loop: fetch pending tokens, scan each."""
    log.info("Contract scanner started")
    await asyncio.sleep(INITIAL_DELAY)
    await _load_factory_hashes()

    # Load already-scanned addresses from DB to avoid re-scanning on restart
    try:
        db = get_db()
        rows = await db.execute_fetchall(
            "SELECT chain, address, scanned_at FROM contract_scans "
            "WHERE scanned_at >= datetime('now', '-1 day')"
        )
        for row in rows:
            _scanned[f"{row['chain']}:{row['address']}"] = time.time()
        log.info(f"Restored {len(rows)} recent scan entries")
    except Exception as e:
        log.warning(f"Failed to restore scans: {e}")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                pending = await _get_pending_tokens()
                if pending:
                    log.info(f"Scanning {len(pending)} new tokens")

                for token in pending:
                    try:
                        await _scan_token(session, token)
                    except Exception as e:
                        log.error(f"Scan error for {token['chain']}/{token['symbol']}: {e}")
                    # Pace API calls
                    await asyncio.sleep(2)

            except Exception as e:
                log.error(f"Scanner poll error: {e}")

            await asyncio.sleep(SCAN_INTERVAL)


def start() -> None:
    global _task
    if not settings.contract_scanner_enabled:
        log.info("Contract scanner disabled (CONTRACT_SCANNER_ENABLED=false)")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
