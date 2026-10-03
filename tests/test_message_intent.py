"""メッセージ方式の一連の処理（FakeClassifier 使用）。"""

from __future__ import annotations

import asyncio

from bot.models import Mode, Outcome, Source
from bot.services.classifier.base import ClassificationResult, IntentAction
from bot.services.classifier.fake_classifier import FakeClassifier
from bot.services.context_buffer import ContextBuffer
from bot.services.message_intent import IntentSettings, MessageIntentProcessor
from tests.conftest import CHANNEL, CREATOR, make_recruitment

JOIN = ClassificationResult(IntentAction.JOIN, 0.95)
LEAVE = ClassificationResult(IntentAction.LEAVE, 0.95)
NONE = ClassificationResult(IntentAction.NONE, 0.99)


def _processor(service, classifier, *, cooldown=0.0, clock=None):
    kwargs = {"clock": clock} if clock else {}
    return MessageIntentProcessor(
        service, classifier, ContextBuffer(5, 600),
        IntentSettings(confidence_threshold=0.85, max_message_chars=200, user_cooldown_sec=cooldown),
        **kwargs,
    )


_msg_id = iter(range(10_000, 99_999))


async def _say(p, user_id, text, channel_id=CHANNEL):
    return await p.process(
        channel_id=channel_id, user_id=user_id, message_id=next(_msg_id), text=text
    )


async def _ids(service, rid):
    return [p.user_id for p in await service.list_participants(rid)]


async def test_join_by_message(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    p = _processor(service, FakeClassifier(JOIN))
    result = await _say(p, 1, "ノ")
    assert result.outcome is Outcome.JOINED and result.reaction == "✅" and result.changed
    assert await _ids(service, rec.id) == [CREATOR, 1]
    sources = {x.user_id: x.source for x in await service.list_participants(rec.id)}
    assert sources[1] is Source.MESSAGE


async def test_leave_by_message(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    await service.join(rec.id, 1, Source.BUTTON)
    p = _processor(service, FakeClassifier(LEAVE))
    result = await _say(p, 1, "ごめんやっぱ無理")
    assert result.outcome is Outcome.LEFT and result.reaction == "👋"
    assert await _ids(service, rec.id) == [CREATOR]


async def test_leave_by_non_participant_does_nothing(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    p = _processor(service, FakeClassifier(LEAVE))
    result = await _say(p, 7, "無理")
    assert result.outcome is Outcome.NOT_JOINED and result.reaction is None and not result.changed
    assert await _ids(service, rec.id) == [CREATOR]


async def test_duplicate_join_by_message(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    p = _processor(service, FakeClassifier(JOIN))
    await _say(p, 1, "ノ")
    result = await _say(p, 1, "行く！")
    assert result.outcome is Outcome.ALREADY_JOINED and result.reaction is None
    assert await _ids(service, rec.id) == [CREATOR, 1]


async def test_full_by_message(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE, capacity=2)
    p = _processor(service, FakeClassifier(JOIN))
    assert (await _say(p, 1, "ノ")).outcome is Outcome.JOINED
    result = await _say(p, 2, "ノ")
    assert result.outcome is Outcome.FULL and result.reaction == "🈵" and not result.changed
    assert await _ids(service, rec.id) == [CREATOR, 1]


async def test_none_does_nothing(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    p = _processor(service, FakeClassifier(NONE))
    result = await _say(p, 1, "あと何人？")
    assert result.action is IntentAction.NONE and result.outcome is None and result.reaction is None
    assert await _ids(service, rec.id) == [CREATOR]


async def test_low_confidence_join_is_ignored(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    p = _processor(service, FakeClassifier(ClassificationResult(IntentAction.JOIN, 0.6)))
    result = await _say(p, 1, "行けたら行く")
    assert result.action is IntentAction.NONE and result.outcome is None
    assert await _ids(service, rec.id) == [CREATOR]


async def test_classifier_failure_does_not_change_participants(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    await service.join(rec.id, 1, Source.BUTTON)
    p = _processor(service, FakeClassifier(ClassificationResult.failed()))
    result = await _say(p, 1, "やっぱ無理")
    assert result.raw.error and result.outcome is None
    assert await _ids(service, rec.id) == [CREATOR, 1]


async def test_classifier_raising_is_treated_as_none(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)

    def boom(_req):
        raise RuntimeError("bug")

    p = _processor(service, FakeClassifier(boom))
    result = await _say(p, 1, "ノ")
    assert result.raw.error and result.outcome is None
    assert await _ids(service, rec.id) == [CREATOR]


async def test_ignored_when_no_message_mode_recruitment(service):
    await make_recruitment(service, mode=Mode.BUTTON)
    fake = FakeClassifier(JOIN)
    p = _processor(service, fake)
    assert await _say(p, 1, "ノ") is None
    assert fake.requests == []  # API を呼ばない


async def test_too_long_message_is_not_classified(service):
    await make_recruitment(service, mode=Mode.MESSAGE)
    fake = FakeClassifier(JOIN)
    p = _processor(service, fake)
    assert await _say(p, 1, "あ" * 201) is None
    assert fake.requests == []


async def test_cooldown_skips_rapid_messages(service):
    await make_recruitment(service, mode=Mode.MESSAGE)
    now = [0.0]
    fake = FakeClassifier(NONE)
    p = _processor(service, fake, cooldown=5.0, clock=lambda: now[0])
    assert await _say(p, 1, "えー") is not None
    assert await _say(p, 1, "どうしよ") is None
    now[0] = 6.0
    assert await _say(p, 1, "行く") is not None
    assert len(fake.requests) == 2


async def test_context_is_pseudonymized_and_limited(service):
    await make_recruitment(service, mode=Mode.MESSAGE)
    fake = FakeClassifier(NONE)
    p = _processor(service, fake)
    await _say(p, 10, "何時から？")
    await _say(p, 20, "21時")
    await _say(p, 10, "なら行く")
    req = fake.requests[-1]
    assert req.target_text == "なら行く"
    assert [(m.speaker, m.text) for m in req.context] == [("TARGET", "何時から？"), ("U1", "21時")]
    assert req.recruitment.start_time == "21:00"


async def test_concurrent_message_joins(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE, capacity=5)

    async def slow_join(_req):
        await asyncio.sleep(0.01)  # API の待ち時間を模擬して処理を交錯させる
        return JOIN

    p = _processor(service, FakeClassifier(slow_join))
    results = await asyncio.gather(*(_say(p, uid, "ノ") for uid in range(1, 11)))
    outcomes = [r.outcome for r in results]
    assert outcomes.count(Outcome.JOINED) == 4
    assert outcomes.count(Outcome.FULL) == 6
    assert len(await _ids(service, rec.id)) == 5
