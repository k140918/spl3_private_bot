"""募集・参加者の永続化。SQL はここに閉じ込める。"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import aiosqlite

from bot.models import (
    Mode,
    Participant,
    ParticipantStatus,
    Recruitment,
    RecruitStatus,
    Source,
)

_RECRUIT_COLS = (
    "id, guild_id, channel_id, message_id, creator_user_id, title, mode, capacity, "
    "start_time, status, created_at, closed_at"
)


def _to_recruitment(row: Any) -> Recruitment:
    return Recruitment(
        id=row["id"],
        guild_id=row["guild_id"],
        channel_id=row["channel_id"],
        message_id=row["message_id"],
        creator_user_id=row["creator_user_id"],
        title=row["title"],
        mode=Mode(row["mode"]),
        capacity=row["capacity"],
        start_time=row["start_time"],
        status=RecruitStatus(row["status"]),
        created_at=row["created_at"],
        closed_at=row["closed_at"],
    )


def _to_participant(row: Any) -> Participant:
    return Participant(
        recruitment_id=row["recruitment_id"],
        user_id=row["user_id"],
        status=ParticipantStatus(row["status"]),
        source=Source(row["source"]),
        joined_at=row["joined_at"],
    )


class RecruitmentRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        # 共有コネクション上でトランザクションが混ざらないよう書き込みを直列化する
        self._write_lock = asyncio.Lock()

    # ---- 募集 ----

    async def create_with_creator(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        title: str,
        mode: Mode,
        capacity: int,
        start_time: str | None,
        created_at: str,
    ) -> int:
        """募集を作成し、作成者を参加者として登録する（1トランザクション）。

        同じチャンネルにメッセージ式の open 募集があれば sqlite3.IntegrityError。
        """
        async with self._write_lock:
            try:
                cur = await self._conn.execute(
                    "INSERT INTO recruitments (guild_id, channel_id, creator_user_id, title, mode, "
                    "capacity, start_time, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                    (guild_id, channel_id, creator_user_id, title, mode.value, capacity,
                     start_time, created_at),
                )
                rid = cur.lastrowid
                await self._conn.execute(
                    "INSERT INTO participants (recruitment_id, user_id, status, source, joined_at) "
                    "VALUES (?, ?, 'joined', 'creator', ?)",
                    (rid, creator_user_id, created_at),
                )
                await self._conn.commit()
            except BaseException:
                await self._conn.rollback()
                raise
        assert rid is not None
        return rid

    async def set_message_id(self, recruitment_id: int, message_id: int) -> None:
        await self._write(
            "UPDATE recruitments SET message_id = ? WHERE id = ?", (message_id, recruitment_id)
        )

    async def update_status(
        self, recruitment_id: int, status: RecruitStatus, closed_at: str | None
    ) -> None:
        await self._write(
            "UPDATE recruitments SET status = ?, closed_at = ? WHERE id = ?",
            (status.value, closed_at, recruitment_id),
        )

    async def get(self, recruitment_id: int) -> Recruitment | None:
        return await self._fetch_one(
            f"SELECT {_RECRUIT_COLS} FROM recruitments WHERE id = ?", (recruitment_id,)
        )

    async def get_by_message_id(self, message_id: int) -> Recruitment | None:
        return await self._fetch_one(
            f"SELECT {_RECRUIT_COLS} FROM recruitments WHERE message_id = ?", (message_id,)
        )

    async def find_open_message_mode(self, channel_id: int) -> Recruitment | None:
        return await self._fetch_one(
            f"SELECT {_RECRUIT_COLS} FROM recruitments "
            "WHERE channel_id = ? AND status = 'open' AND mode IN ('message', 'both')",
            (channel_id,),
        )

    async def list_open_in_channel(self, channel_id: int) -> list[Recruitment]:
        return await self._fetch_all(
            f"SELECT {_RECRUIT_COLS} FROM recruitments "
            "WHERE channel_id = ? AND status = 'open' ORDER BY id DESC",
            (channel_id,),
        )

    async def list_open_created_before(self, cutoff_iso: str) -> list[Recruitment]:
        return await self._fetch_all(
            f"SELECT {_RECRUIT_COLS} FROM recruitments "
            "WHERE status = 'open' AND created_at < ? ORDER BY id",
            (cutoff_iso,),
        )

    # ---- 参加者 ----

    async def list_participants(self, recruitment_id: int) -> list[Participant]:
        async with self._conn.execute(
            "SELECT recruitment_id, user_id, status, source, joined_at FROM participants "
            "WHERE recruitment_id = ? ORDER BY joined_at, id",
            (recruitment_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_participant(r) for r in rows]

    async def add_participant(
        self,
        recruitment_id: int,
        user_id: int,
        status: ParticipantStatus,
        source: Source,
        joined_at: str,
    ) -> bool:
        """追加できたら True。既に登録済み（UNIQUE違反）なら False。"""
        try:
            await self._write(
                "INSERT INTO participants (recruitment_id, user_id, status, source, joined_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (recruitment_id, user_id, status.value, source.value, joined_at),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    async def remove_participant(self, recruitment_id: int, user_id: int) -> bool:
        rowcount = await self._write(
            "DELETE FROM participants WHERE recruitment_id = ? AND user_id = ?",
            (recruitment_id, user_id),
        )
        return rowcount > 0

    # ---- AI判定ログ ----

    async def add_judgement(
        self,
        *,
        recruitment_id: int,
        message_id: int,
        user_id: int,
        action: str,
        confidence: float | None,
        applied: bool,
        created_at: str,
    ) -> None:
        await self._write(
            "INSERT INTO ai_judgements (recruitment_id, message_id, user_id, action, confidence, "
            "applied, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (recruitment_id, message_id, user_id, action, confidence, int(applied), created_at),
        )

    # ---- helpers ----

    async def _write(self, sql: str, params: tuple[Any, ...]) -> int:
        """1文を実行してコミットする。失敗時はロールバックして例外を再送出。"""
        async with self._write_lock:
            try:
                cur = await self._conn.execute(sql, params)
                await self._conn.commit()
            except BaseException:
                await self._conn.rollback()
                raise
            return cur.rowcount

    async def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> Recruitment | None:
        async with self._conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return _to_recruitment(row) if row else None

    async def _fetch_all(self, sql: str, params: tuple[Any, ...]) -> list[Recruitment]:
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_to_recruitment(r) for r in rows]
