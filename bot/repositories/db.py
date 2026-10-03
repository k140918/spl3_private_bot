"""SQLite 接続とスキーマ適用。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1


async def connect(path: Path | str) -> aiosqlite.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA busy_timeout = 5000")
    await migrate(conn)
    return conn


async def migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    version = row[0] if row else 0
    if version < 1:
        await conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await conn.commit()
    # 将来スキーマを変える場合は if version < 2: ... を追加する
