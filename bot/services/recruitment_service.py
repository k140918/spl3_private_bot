"""募集の業務ロジック。Discord にも Gemini にも依存しない（テストの中心）。"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
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


@dataclass
class CarryOverResult:
    outcome: Outcome  # JOINED: 実行できた / NOT_FOUND / NOT_OPEN
    added: list[int] = field(default_factory=list)
    already_joined: list[int] = field(default_factory=list)
    full: list[int] = field(default_factory=list)  # 定員超過で追加できなかった


GUEST_NAME_MAX = 32
DESCRIPTION_MAX = 1000


def normalize_description(text: str) -> str | None:
    """募集文を保存用に整える。行末の空白と3行以上続く空行を詰め、空なら None。"""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and len(out) >= 1 and not out[-1] and (len(out) < 2 or not out[-2]):
            continue  # 空行は2行まで
        out.append(line)
    return "\n".join(out).strip()[:DESCRIPTION_MAX] or None


def normalize_guest_name(name: str) -> str | None:
    """ゲスト名を保存用に整える（改行・連続空白を潰し、最大32文字）。"""
    return " ".join(name.split())[:GUEST_NAME_MAX] or None


def _guest_key(name: str | None) -> str:
    """重複判定用。全角半角・大文字小文字の違いを無視する。"""
    return unicodedata.normalize("NFKC", name or "").casefold()


def _next_guest_id(participants: list[Participant]) -> int:
    """ゲスト用の負数 ID。Discord のユーザー ID（正数）とは衝突しない。"""
    return min([p.user_id for p in participants] + [0]) - 1


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
        deadline: datetime | None = None,
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
                deadline_at=to_iso(deadline) if deadline else None,
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

    async def set_description(self, recruitment_id: int, text: str) -> Outcome:
        """募集文を設定する。空文字なら削除。受付中の募集のみ。"""
        description = normalize_description(text)
        async with self.lock(recruitment_id):
            rec = await self._repo.get(recruitment_id)
            if rec is None:
                return Outcome.NOT_FOUND
            if not rec.is_open:
                return Outcome.NOT_OPEN
            await self._repo.set_description(recruitment_id, description)
        log.info("description set id=%s length=%d", recruitment_id, len(description or ""))
        return Outcome.JOINED  # 「成功」の意味で使う

    async def set_deadline(self, recruitment_id: int, deadline: datetime) -> None:
        async with self.lock(recruitment_id):
            await self._repo.set_deadline(recruitment_id, to_iso(deadline))
        log.info("deadline set id=%s deadline=%s", recruitment_id, to_iso(deadline))

    async def close_due(self, ttl: timedelta) -> list[Recruitment]:
        """締切を過ぎた募集と、締切なしで作成から ttl たった募集を終了にする。"""
        now = self._clock()
        due = await self._repo.list_open_due(to_iso(now), to_iso(now - ttl))
        for rec in due:
            await self.set_status(rec.id, RecruitStatus.CLOSED)
        return due

    # ---- 参照 ----

    async def get(self, recruitment_id: int) -> Recruitment | None:
        return await self._repo.get(recruitment_id)

    async def get_by_message_id(self, message_id: int) -> Recruitment | None:
        return await self._repo.get_by_message_id(message_id)

    async def find_open_message_mode(self, channel_id: int) -> Recruitment | None:
        return await self._repo.find_open_message_mode(channel_id)

    async def list_open_in_channel(self, channel_id: int) -> list[Recruitment]:
        return await self._repo.list_open_in_channel(channel_id)

    async def list_ended_in_channel(
        self, channel_id: int, within: timedelta = timedelta(days=7)
    ) -> list[Recruitment]:
        return await self._repo.list_ended_in_channel(channel_id, to_iso(self._clock() - within))

    async def list_participants(self, recruitment_id: int) -> list[Participant]:
        return await self._repo.list_participants(recruitment_id)

    # ---- 参加・取消 ----

    async def join_many(self, recruitment_id: int, user_ids: list[int]) -> dict[int, Outcome]:
        """選択順に追加。個々の参加処理で同時操作・重複・定員を検証する。"""
        results = {}
        for user_id in dict.fromkeys(user_ids):
            results[user_id] = await self.join(recruitment_id, user_id, Source.COMMAND)
        return results

    async def claim_full_notification(self, recruitment_id: int) -> bool:
        """募集につき一度だけ通知する。再起動後も送信済みを保持する。"""
        async with self.lock(recruitment_id):
            rec = await self._repo.get(recruitment_id)
            if rec is None or not rec.accepts_at(self._now()) or rec.message_id is None:
                return False
            participants = await self._repo.list_participants(recruitment_id)
            if sum(p.status is ParticipantStatus.JOINED for p in participants) < rec.capacity:
                return False
            return await self._repo.claim_full_notification(recruitment_id)

    async def release_full_notification(self, recruitment_id: int) -> None:
        await self._repo.release_full_notification(recruitment_id)

    async def join(self, recruitment_id: int, user_id: int, source: Source) -> Outcome:
        async with self.lock(recruitment_id):
            rec = await self._repo.get(recruitment_id)
            if rec is None:
                return Outcome.NOT_FOUND
            if not rec.accepts_at(self._now()):  # 締切時刻を過ぎていれば受け付けない
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
            if not rec.accepts_at(self._now()):  # 締切時刻を過ぎていれば受け付けない
                return Outcome.NOT_OPEN
            removed = await self._repo.remove_participant(recruitment_id, user_id)
            # 補欠を導入する場合はここで先頭の WAITLIST を JOINED に繰り上げる
        if not removed:
            return Outcome.NOT_JOINED
        log.info("LEAVE recruitment=%s user=%s", recruitment_id, user_id)
        return Outcome.LEFT

    async def add_guest(self, recruitment_id: int, name: str) -> Outcome:
        """サーバーにいない人を名前だけで追加する。同じ名前のゲストは重複させない。"""
        name = normalize_guest_name(name)
        if not name:
            return Outcome.NOT_FOUND
        async with self.lock(recruitment_id):
            rec = await self._repo.get(recruitment_id)
            if rec is None:
                return Outcome.NOT_FOUND
            if not rec.accepts_at(self._now()):
                return Outcome.NOT_OPEN
            participants = await self._repo.list_participants(recruitment_id)
            if any(p.is_guest and _guest_key(p.guest_name) == _guest_key(name) for p in participants):
                return Outcome.ALREADY_JOINED
            joined = sum(1 for p in participants if p.status is ParticipantStatus.JOINED)
            if joined >= rec.capacity:
                return Outcome.FULL
            guest_id = _next_guest_id(participants)
            await self._repo.add_participant(
                recruitment_id, guest_id, ParticipantStatus.JOINED, Source.COMMAND, self._now(),
                guest_name=name,
            )
        log.info("JOIN guest recruitment=%s guest_id=%s", recruitment_id, guest_id)
        return Outcome.JOINED

    async def remove_guest(self, recruitment_id: int, name: str) -> Outcome:
        key = _guest_key(normalize_guest_name(name) or "")
        async with self.lock(recruitment_id):
            rec = await self._repo.get(recruitment_id)
            if rec is None:
                return Outcome.NOT_FOUND
            if not rec.accepts_at(self._now()):
                return Outcome.NOT_OPEN
            participants = await self._repo.list_participants(recruitment_id)
            guest = next(
                (p for p in participants if p.is_guest and _guest_key(p.guest_name) == key), None
            )
            if guest is None:
                return Outcome.NOT_JOINED
            await self._repo.remove_participant(recruitment_id, guest.user_id)
        log.info("LEAVE guest recruitment=%s guest_id=%s", recruitment_id, guest.user_id)
        return Outcome.LEFT

    async def carry_over(self, source_id: int, target_id: int) -> CarryOverResult:
        """終了した募集の参加者を、受付中の募集へ参加順のまま追加する（定員まで）。

        結果のリストには元の募集での user_id が入る（ゲストは負数）。
        """
        source = await self._repo.get(source_id)
        async with self.lock(target_id):
            target = await self._repo.get(target_id)
            if source is None or target is None or source.guild_id != target.guild_id:
                return CarryOverResult(Outcome.NOT_FOUND)
            if source.id == target.id or not target.accepts_at(self._now()):
                return CarryOverResult(Outcome.NOT_OPEN)
            current = await self._repo.list_participants(target_id)
            existing_users = {p.user_id for p in current if not p.is_guest}
            existing_guests = {_guest_key(p.guest_name) for p in current if p.is_guest}
            joined = sum(1 for p in current if p.status is ParticipantStatus.JOINED)
            result = CarryOverResult(Outcome.JOINED)
            for p in await self._repo.list_participants(source_id):
                if p.status is not ParticipantStatus.JOINED:
                    continue
                if p.is_guest:
                    already = _guest_key(p.guest_name) in existing_guests
                else:
                    already = p.user_id in existing_users
                if already:
                    result.already_joined.append(p.user_id)
                    continue
                if joined >= target.capacity:
                    result.full.append(p.user_id)
                    continue
                if p.is_guest:
                    # ゲストの負数 ID は募集ごとに振り直す（追加先のゲストと衝突しないように）
                    added = await self._repo.add_participant(
                        target_id, _next_guest_id(current), ParticipantStatus.JOINED,
                        Source.COMMAND, self._now(), guest_name=p.guest_name,
                    )
                    existing_guests.add(_guest_key(p.guest_name))
                else:
                    added = await self._repo.add_participant(
                        target_id, p.user_id, ParticipantStatus.JOINED, Source.COMMAND, self._now()
                    )
                if added:
                    result.added.append(p.user_id)
                    joined += 1
                    current = await self._repo.list_participants(target_id)
        log.info(
            "carry over source=%s target=%s added=%d already=%d full=%d",
            source_id, target_id, len(result.added), len(result.already_joined), len(result.full),
        )
        return result

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
