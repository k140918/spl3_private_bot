"""ドメインモデル。Discord / Gemini に依存しない。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    MESSAGE = "message"
    BUTTON = "button"
    BOTH = "both"

    @property
    def uses_message(self) -> bool:
        return self in (Mode.MESSAGE, Mode.BOTH)

    @property
    def uses_buttons(self) -> bool:
        return self in (Mode.BUTTON, Mode.BOTH)


class RecruitStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ParticipantStatus(str, Enum):
    JOINED = "joined"
    WAITLIST = "waitlist"  # 将来の補欠用（現状は未使用）


class Source(str, Enum):
    BUTTON = "button"
    MESSAGE = "message"
    COMMAND = "command"
    CREATOR = "creator"


class Outcome(str, Enum):
    JOINED = "joined"
    LEFT = "left"
    ALREADY_JOINED = "already_joined"
    NOT_JOINED = "not_joined"
    FULL = "full"
    NOT_OPEN = "not_open"
    NOT_FOUND = "not_found"

    @property
    def changed(self) -> bool:
        return self in (Outcome.JOINED, Outcome.LEFT)


@dataclass(frozen=True)
class Recruitment:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    creator_user_id: int
    title: str
    mode: Mode
    capacity: int
    start_time: str | None
    status: RecruitStatus
    created_at: str
    closed_at: str | None = None
    deadline_at: str | None = None  # UTC ISO8601。None なら作成からの経過時間で自動終了

    @property
    def is_open(self) -> bool:
        return self.status is RecruitStatus.OPEN

    def accepts_at(self, now_iso: str) -> bool:
        """受付中で、締切を過ぎていない。"""
        return self.is_open and (self.deadline_at is None or now_iso < self.deadline_at)


@dataclass(frozen=True)
class Participant:
    recruitment_id: int
    user_id: int
    status: ParticipantStatus
    source: Source
    joined_at: str
