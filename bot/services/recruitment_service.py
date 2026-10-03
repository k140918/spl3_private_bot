"""募集の業務ロジック。Discord にも Gemini にも依存しない（テストの中心）。"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from bot.models import (
    Mode,
    Outcome,
    Participant,
    ParticipantStatus,
    Recruitment,
    RecruitStatus,
    Source,
)
from bot.repositories.recruitment_repo import RecruitmentRepository

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class ChannelBusyError(Exception):
    """同じチャンネルに既にメッセージ式の募集がある。"""

    def __init__(self, existing: Recruitment | None) -> None:
        super().__init__("channel already has an open message-mode recruitment")
        self.existing = existing


class RecruitmentService:
    def __init__(
        self, repo: RecruitmentRepository, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._locks: dict[int, asyncio.Lock] = {}

    def lock(self, recruitment_id: int) -> asyncio.Lock:
        """募集単位のロック。参加者の読み取り→判定→書き込みを直列化する。"""
        return self._locks.setdefault(recruitment_id, asyncio.Lock())

    def _now(self) -> str:
        return to_iso(self._clock())

    # ---- 作成・状態変更 ----

    async def create(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        title: str,
        mode: Mode,
        capacity: int,
        start_time: str | None,
    ) -> Recruitment:
        if mode.uses_message:
            existing = await self._repo.find_open_message_mode(channel_id)
            if existing is not None:
                raise ChannelBusyError(existing)
        try:
            rid = await self._repo.create_with_creator(
                guild_id=guild_id,
                channel_id=channel_id,
                creator_user_id=creator_user_id,
                title=title,
                mode=mode,
                capacity=capacity,
                start_time=start_time,
                created_at=self._now(),
            )
        except sqlite3.IntegrityError:
            # 同時に作成された場合は DB の部分 UNIQUE インデックスで弾かれる
            raise ChannelBusyError(await self._repo.find_open_message_mode(channel_id)) from None
        rec = await self._repo.get(rid)
        assert rec is not None
        log.info(
            "recruitment created id=%s guild=%s channel=%s creator=%s mode=%s capacity=%s",
            rec.id, guild_id, channel_id, creator_user_id, mode.value, capacity,
        )
        return rec

    async def attach_message(self, recruitment_id: int, message_id: int) -> None:
        await self._repo.set_message_id(recruitment_id, message_id)

    async def set_status(self, recruitment_id: int, status: RecruitStatus) -> None:
        async with self.lock(recruitment_id):
            await self._repo.update_status(recruitment_id, status, self._now())
        self._locks.pop(recruitment_id, None)
        log.info("recruitment status changed id=%s status=%s", recruitment_id, status.value)

    async def expire_older_than(self, ttl: timedelta) -> list[Recruitment]:
        cutoff = to_iso(self._clock() - ttl)
        expired = await self._repo.list_open_created_before(cutoff)
        for rec in expired:
            await self.set_status(rec.id, RecruitStatus.CLOSED)
        return expired

    # ---- 参照 ----

    async def get(self, recruitment_id: int) -> Recruitment | None:
        return await self._repo.get(recruitment_id)

    async def get_by_message_id(self, message_id: int) -> Recruitment | None:
        return await self._repo.get_by_message_id(message_id)

    async def find_open_message_mode(self, channel_id: int) -> Recruitment | None:
        return await self._repo.find_open_message_mode(channel_id)

    async def list_open_in_channel(self, channel_id: int) -> list[Recruitment]:
        return await self._repo.list_open_in_channel(channel_id)

    async def list_participants(self, recruitment_id: int) -> list[Participant]:
        return await self._repo.list_participants(recruitment_id)

    # ---- 参加・取消 ----

    async def join(self, recruitment_id: int, user_id: int, source: Source) -> Outcome:
        async with self.lock(recruitment_id):
            rec = await self._repo.get(recruitment_id)
            if rec is None:
                return Outcome.NOT_FOUND
            if not rec.is_open:
                return Outcome.NOT_OPEN
            participants = await self._repo.list_participants(recruitment_id)
            if any(p.user_id == user_id for p in participants):
                return Outcome.ALREADY_JOINED
            joined = sum(1 for p in participants if p.status is ParticipantStatus.JOINED)
            if joined >= rec.capacity:
                # 補欠を導入する場合はここで ParticipantStatus.WAITLIST として登録する
                return Outcome.FULL
            added = await self._repo.add_participant(
                recruitment_id, user_id, ParticipantStatus.JOINED, source, self._now()
            )
            if not added:
                return Outcome.ALREADY_JOINED
        log.info("JOIN recruitment=%s user=%s source=%s", recruitment_id, user_id, source.value)
        return Outcome.JOINED

    async def leave(self, recruitment_id: int, user_id: int) -> Outcome:
        async with self.lock(recruitment_id):
            rec = await self._repo.get(recruitment_id)
            if rec is None:
                return Outcome.NOT_FOUND
            if not rec.is_open:
                return Outcome.NOT_OPEN
            removed = await self._repo.remove_participant(recruitment_id, user_id)
            # 補欠を導入する場合はここで先頭の WAITLIST を JOINED に繰り上げる
        if not removed:
            return Outcome.NOT_JOINED
        log.info("LEAVE recruitment=%s user=%s", recruitment_id, user_id)
        return Outcome.LEFT

    # ---- AI判定ログ ----

    async def record_judgement(
        self,
        *,
        recruitment_id: int,
        message_id: int,
        user_id: int,
        action: str,
        confidence: float | None,
        applied: bool,
    ) -> None:
        await self._repo.add_judgement(
            recruitment_id=recruitment_id,
            message_id=message_id,
            user_id=user_id,
            action=action,
            confidence=confidence,
            applied=applied,
            created_at=self._now(),
        )
