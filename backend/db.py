import aiosqlite
from pathlib import Path
from config import settings

_db: aiosqlite.Connection | None = None


async def init_db():
    global _db
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(settings.db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")

    schema = Path(__file__).parent / "schema.sql"
    await _db.executescript(schema.read_text())
    await _db.commit()

    # Migration: add oi_binance_usd column
    for tbl in ["daily_derivatives", "derivatives_4h"]:
        try:
            await _db.execute(f"ALTER TABLE {tbl} ADD COLUMN oi_binance_usd REAL DEFAULT 0")
            await _db.commit()
        except Exception:
            pass  # column already exists


def get_db() -> aiosqlite.Connection:
    assert _db is not None, "DB not initialized"
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None
