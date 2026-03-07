"""Seed verified_contracts table from evm_automation skill JSON files."""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "radar.db"

# Chain name mapping: skill JSON → feed chain names
CHAIN_MAP = {
    "mainnet": "ethereum",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "polygon": "polygon",
    "zksync": "zksync",
    "bsc": "bsc",
    "ethereum": "ethereum",
}

# _all_chains expands to these
ALL_CHAINS = ["ethereum", "arbitrum", "optimism", "base", "polygon"]

SKIP_KEYS = {"_meta", "_note", "_polygon_note", "_bsc_note"}


def extract_from_protocol_addresses(data: dict) -> list[tuple]:
    """Extract (chain, address, symbol, name, category, protocol) tuples."""
    rows = []
    for category, entries in data.items():
        if category in SKIP_KEYS:
            continue
        for name, info in entries.items():
            if isinstance(info, str):
                # Direct address (e.g. base_defi.morpho_blue)
                rows.append(("base", info.lower(), name, name, category, category))
                continue
            if not isinstance(info, dict):
                continue

            # Check for _all_chains
            all_addr = info.get("_all_chains")
            if all_addr:
                for chain in ALL_CHAINS:
                    rows.append((chain, all_addr.lower(), name, name, category, category))
                continue

            # Check for chain-specific "chain" key (l2_native_dex style)
            if "chain" in info:
                chain = CHAIN_MAP.get(info["chain"], info["chain"])
                for key, addr in info.items():
                    if key in ("chain", "_note", "decimals"):
                        continue
                    if isinstance(addr, str) and addr.startswith("0x"):
                        label = f"{name}.{key}" if key != name else name
                        rows.append((chain, addr.lower(), label, name, category, name))
                continue

            # Standard: per-chain addresses
            for key, val in info.items():
                if key.startswith("_") or key == "decimals":
                    continue
                if isinstance(val, str) and val.startswith("0x"):
                    chain = CHAIN_MAP.get(key, key)
                    rows.append((chain, val.lower(), name, name, category, category))
    return rows


def extract_from_dex_routers(data: dict) -> list[tuple]:
    """Extract from dex_routers.json."""
    rows = []
    for dex, chains in data.items():
        if dex.startswith("_") or dex == "weth":
            continue
        if not isinstance(chains, dict):
            continue
        for chain_name, info in chains.items():
            chain = CHAIN_MAP.get(chain_name, chain_name)
            if isinstance(info, dict):
                for role, addr in info.items():
                    if isinstance(addr, str) and addr.startswith("0x"):
                        rows.append((chain, addr.lower(), f"{dex}.{role}", dex, "dex", dex))
            elif isinstance(info, str) and info.startswith("0x"):
                rows.append((chain, info.lower(), dex, dex, "dex", dex))
    return rows


def seed():
    skill_dir = Path.home() / ".claude" / "skills" / "evm_automation" / "resources"

    all_rows = []

    proto_file = skill_dir / "protocol-addresses.json"
    if proto_file.exists():
        data = json.loads(proto_file.read_text())
        all_rows.extend(extract_from_protocol_addresses(data))

    dex_file = skill_dir / "dex_routers.json"
    if dex_file.exists():
        data = json.loads(dex_file.read_text())
        all_rows.extend(extract_from_dex_routers(data))

    # Deduplicate by (chain, address)
    seen = set()
    unique = []
    for row in all_rows:
        key = (row[0], row[1])
        if key not in seen:
            seen.add(key)
            unique.append(row)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS verified_contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chain TEXT NOT NULL, address TEXT NOT NULL,
        symbol TEXT, name TEXT, category TEXT NOT NULL, protocol TEXT,
        added_at TEXT DEFAULT (datetime('now')),
        UNIQUE(chain, address)
    )""")

    inserted = 0
    for chain, address, symbol, name, category, protocol in unique:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO verified_contracts (chain, address, symbol, name, category, protocol) VALUES (?, ?, ?, ?, ?, ?)",
                (chain, address, symbol, name, category, protocol),
            )
            inserted += 1
        except Exception as e:
            print(f"  skip {chain}/{address}: {e}")

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM verified_contracts").fetchone()[0]
    conn.close()
    print(f"Seeded {inserted} contracts ({len(unique)} unique). Total in DB: {total}")


if __name__ == "__main__":
    seed()
