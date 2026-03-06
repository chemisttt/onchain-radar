import json
from fastapi import APIRouter
from pydantic import BaseModel

from db import get_db

router = APIRouter()


class SettingUpdate(BaseModel):
    key: str
    value: dict | str | int | float | bool | list


@router.get("/settings")
async def get_settings():
    db = get_db()
    rows = await db.execute_fetchall("SELECT key, value FROM settings")
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            result[row["key"]] = row["value"]
    return result


@router.get("/settings/{key}")
async def get_setting(key: str):
    db = get_db()
    rows = await db.execute_fetchall("SELECT value FROM settings WHERE key = ?", (key,))
    if not rows:
        return None
    try:
        return json.loads(rows[0]["value"])
    except (json.JSONDecodeError, TypeError):
        return rows[0]["value"]


@router.put("/settings")
async def update_setting(item: SettingUpdate):
    db = get_db()
    value_str = json.dumps(item.value) if not isinstance(item.value, str) else item.value
    await db.execute(
        """INSERT OR REPLACE INTO settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))""",
        (item.key, value_str),
    )
    await db.commit()
    return {"key": item.key, "value": item.value}
