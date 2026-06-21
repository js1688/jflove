import aiosqlite
from src.repositories import config_repository


async def get_all(db: aiosqlite.Connection) -> dict:
    rows = await config_repository.get_all(db)
    return {r["key"]: r["value"] for r in rows}


async def update(db: aiosqlite.Connection, key: str, value: str) -> None:
    await config_repository.set(db, key, value)
