"""メッセージ方式: 発言 → 分類 → 参加者反映 の一連の処理。

Discord に依存しない（cog はこの結果を見てリアクションと表示更新をするだけ）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from bot.models import Outcome, Participant, ParticipantStatus, Recruitment, Source
from bot.services.classifier.base import (
    ClassificationRequest,
    ClassificationResult,
    ContextMessage,
    IntentAction,
    IntentClassifier,
    RecruitmentInfo,
    decide_action,
)
from bot.services.context_buffer import BufferedMessage, ContextBuffer
from bot.services.recruitment_service import RecruitmentService

log = logging.getLogger(__name__)

REACTIONS: dict[Outcome, str] = {
    Outcome.JOINED: "✅",
    Outcome.LEFT: "👋",
    Outcome.FULL: "🈵",
}


@dataclass(frozen=True)
class IntentSettings:
    confidence_threshold: float = 0.85
    max_message_chars: int = 200
    user_cooldown_sec: float = 5.0


@dataclass(frozen=True)
class ProcessResult:
    recruitment_id: int
    raw: ClassificationResult
    action: IntentAction  # しきい値適用後
    outcome: Outcome | None  # JOIN / LEAVE を反映しようとした結果

    @property
    def reaction(self) -> str | None:
        return REACTIONS.get(self.outcome) if self.outcome else None

    @property
    def changed(self) -> bool:
        return self.outcome is not None and self.outcome.changed


def build_request(
    recruitment: Recruitment,
    participants: list[Participant],
    target_user_id: int,
    target_text: str,
    history: list[BufferedMessage],
) -> ClassificationRequest:
    """発言者を TARGET / U1, U2... に仮名化して分類リクエストを組み立てる。"""
    aliases: dict[int, str] = {target_user_id: "TARGET"}
    context: list[ContextMessage] = []
    for m in history:
        if m.user_id not in aliases:
            aliases[m.user_id] = f"U{len(aliases)}"
        context.append(ContextMessage(aliases[m.user_id], m.text))
    joined = [p for p in participants if p.status is ParticipantStatus.JOINED]
    return ClassificationRequest(
        target_text=target_text,
        recruitment=RecruitmentInfo(
            title=recruitment.title,
            start_time=recruitment.start_time,
            capacity=recruitment.capacity,
            joined_count=len(joined),
            target_already_joined=any(p.user_id == target_user_id for p in participants),
        ),
        context=tuple(context),
    )


class MessageIntentProcessor:
    def __init__(
        self,
        service: RecruitmentService,
        classifier: IntentClassifier,
        buffer: ContextBuffer,
        settings: IntentSettings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._classifier = classifier
        self._buffer = buffer
        self._settings = settings
        self._clock = clock
        self._last_call: dict[int, float] = {}

    def _on_cooldown(self, user_id: int) -> bool:
        now = self._clock()
        last = self._last_call.get(user_id)
        if last is not None and now - last < self._settings.user_cooldown_sec:
            return True
        self._last_call[user_id] = now
        if len(self._last_call) > 1000:  # 古いエントリを掃除してメモリを抑える
            limit = now - self._settings.user_cooldown_sec
            self._last_call = {u: t for u, t in self._last_call.items() if t >= limit}
        return False

    async def process(
        self, *, channel_id: int, user_id: int, message_id: int, text: str
    ) -> ProcessResult | None:
        """判定対象外なら None。判定した場合は ProcessResult。"""
        rec = await self._service.find_open_message_mode(channel_id)
        if rec is None:
            return None
        text = text.strip()
        if not text:
            return None

        max_chars = self._settings.max_message_chars
        history = self._buffer.recent(channel_id)
        self._buffer.add(channel_id, user_id, message_id, text[:max_chars])

        if len(text) > max_chars or self._on_cooldown(user_id):
            return None

        participants = await self._service.list_participants(rec.id)
        request = build_request(rec, participants, user_id, text, history)
        try:
            raw = await self._classifier.classify(request)
        except Exception as e:  # 実装が契約を破っても安全側に倒す
            log.warning("classifier raised type=%s", type(e).__name__)
            raw = ClassificationResult.failed()

        action = decide_action(raw, self._settings.confidence_threshold)
        outcome: Outcome | None = None
        if action is IntentAction.JOIN:
            outcome = await self._service.join(rec.id, user_id, Source.MESSAGE)
        elif action is IntentAction.LEAVE:
            outcome = await self._service.leave(rec.id, user_id)

        await self._service.record_judgement(
            recruitment_id=rec.id,
            message_id=message_id,
            user_id=user_id,
            action="ERROR" if raw.error else raw.action.value,
            confidence=None if raw.error else raw.confidence,
            applied=outcome is not None and outcome.changed,
        )
        return ProcessResult(rec.id, raw, action, outcome)
