"""SQLite 接続とスキーマ適用。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 5


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
        await conn.execute("PRAGMA user_version = 1")
        await conn.commit()
    if version < 2:
        # v2: 締切時刻（UTC ISO8601）。NULL なら作成から RECRUIT_TTL_HOURS で自動終了
        await conn.execute("ALTER TABLE recruitments ADD COLUMN deadline_at TEXT")
        await conn.execute("PRAGMA user_version = 2")
        await conn.commit()
    if version < 3:
        # v3: ゲスト参加者（サーバーにいない人）の名前。Discord ユーザーは NULL
        await conn.execute("ALTER TABLE participants ADD COLUMN guest_name TEXT")
        await conn.execute("PRAGMA user_version = 3")
        await conn.commit()
    if version < 4:
        # v4: 募集文（/プラベ 募集文 で設定する自由記述）
        await conn.execute("ALTER TABLE recruitments ADD COLUMN description TEXT")
        await conn.execute("PRAGMA user_version = 4")
        await conn.commit()
    if version < 5:
        await conn.execute("ALTER TABLE recruitments ADD COLUMN full_notified INTEGER NOT NULL DEFAULT 0")
        await conn.execute("PRAGMA user_version = 5")
        await conn.commit()
