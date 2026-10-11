from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from bot.models import Mode, Outcome, RecruitStatus, Source
from bot.repositories.db import connect
from bot.repositories.recruitment_repo import RecruitmentRepository
from bot.services.recruitment_service import ChannelBusyError, RecruitmentService
from tests.conftest import CHANNEL, CREATOR, make_recruitment


async def test_creator_is_auto_joined(service):
    rec = await make_recruitment(service)
    participants = await service.list_participants(rec.id)
    assert [(p.user_id, p.source) for p in participants] == [(CREATOR, Source.CREATOR)]


async def test_join(service):
    rec = await make_recruitment(service)
    assert await service.join(rec.id, 1, Source.BUTTON) is Outcome.JOINED
    ids = [p.user_id for p in await service.list_participants(rec.id)]
    assert ids == [CREATOR, 1]


async def test_duplicate_join_is_not_registered_twice(service):
    rec = await make_recruitment(service)
    assert await service.join(rec.id, 1, Source.BUTTON) is Outcome.JOINED
    assert await service.join(rec.id, 1, Source.MESSAGE) is Outcome.ALREADY_JOINED
    assert len(await service.list_participants(rec.id)) == 2


async def test_leave(service):
    rec = await make_recruitment(service)
    await service.join(rec.id, 1, Source.BUTTON)
    assert await service.leave(rec.id, 1) is Outcome.LEFT
    assert [p.user_id for p in await service.list_participants(rec.id)] == [CREATOR]


async def test_leave_when_not_joined(service):
    rec = await make_recruitment(service)
    assert await service.leave(rec.id, 999) is Outcome.NOT_JOINED
    assert len(await service.list_participants(rec.id)) == 1


async def test_capacity_full(service):
    rec = await make_recruitment(service, capacity=3)  # 作成者 + 2人で満員
    assert await service.join(rec.id, 1, Source.BUTTON) is Outcome.JOINED
    assert await service.join(rec.id, 2, Source.BUTTON) is Outcome.JOINED
    assert await service.join(rec.id, 3, Source.BUTTON) is Outcome.FULL
    # 1人抜ければ入れる
    assert await service.leave(rec.id, 1) is Outcome.LEFT
    assert await service.join(rec.id, 3, Source.BUTTON) is Outcome.JOINED


async def test_concurrent_joins_respect_capacity(service):
    rec = await make_recruitment(service, capacity=8)
    outcomes = await asyncio.gather(
        *(service.join(rec.id, uid, Source.BUTTON) for uid in range(1, 21))
    )
    assert outcomes.count(Outcome.JOINED) == 7  # 作成者を含めて8人
    assert outcomes.count(Outcome.FULL) == 13
    assert len(await service.list_participants(rec.id)) == 8


async def test_concurrent_same_user_join_registers_once(service):
    rec = await make_recruitment(service)
    outcomes = await asyncio.gather(*(service.join(rec.id, 5, Source.BUTTON) for _ in range(10)))
    assert outcomes.count(Outcome.JOINED) == 1
    assert outcomes.count(Outcome.ALREADY_JOINED) == 9


async def test_closed_recruitment_rejects_changes(service):
    rec = await make_recruitment(service)
    await service.set_status(rec.id, RecruitStatus.CLOSED)
    assert await service.join(rec.id, 1, Source.BUTTON) is Outcome.NOT_OPEN
    assert await service.leave(rec.id, CREATOR) is Outcome.NOT_OPEN


async def test_unknown_recruitment(service):
    assert await service.join(12345, 1, Source.BUTTON) is Outcome.NOT_FOUND


async def test_one_message_mode_recruitment_per_channel(service):
    await make_recruitment(service, mode=Mode.MESSAGE)
    with pytest.raises(ChannelBusyError):
        await make_recruitment(service, mode=Mode.BOTH)
    # ボタン方式は同じチャンネルでも作れる
    await make_recruitment(service, mode=Mode.BUTTON)
    await make_recruitment(service, mode=Mode.BUTTON)
    # 別チャンネルならメッセージ方式も作れる
    await make_recruitment(service, mode=Mode.MESSAGE, channel_id=CHANNEL + 1)


async def test_message_mode_slot_frees_after_close(service):
    rec = await make_recruitment(service, mode=Mode.MESSAGE)
    await service.set_status(rec.id, RecruitStatus.CANCELLED)
    await make_recruitment(service, mode=Mode.MESSAGE)


async def test_close_due_without_deadline_uses_ttl():
    now = [datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)]
    conn = await connect(":memory:")
    try:
        service = RecruitmentService(RecruitmentRepository(conn), clock=lambda: now[0])
        old = await make_recruitment(service, mode=Mode.MESSAGE)
        now[0] += timedelta(hours=5)
        new = await make_recruitment(service, mode=Mode.BUTTON)
        now[0] += timedelta(hours=2)
        expired = await service.close_due(timedelta(hours=6))
        assert [r.id for r in expired] == [old.id]
        assert (await service.get(old.id)).status is RecruitStatus.CLOSED
        assert (await service.get(new.id)).is_open
    finally:
        await conn.close()


async def test_persists_across_reconnect(tmp_path):
    """Bot 再起動（DB再接続）後も募集と参加者が残る。"""
    db = tmp_path / "test.db"
    conn = await connect(db)
    service = RecruitmentService(RecruitmentRepository(conn))
    rec = await make_recruitment(service)
    await service.attach_message(rec.id, 555)
    await service.join(rec.id, 1, Source.BUTTON)
    await conn.close()

    conn = await connect(db)
    try:
        service = RecruitmentService(RecruitmentRepository(conn))
        again = await service.get_by_message_id(555)
        assert again is not None and again.id == rec.id
        assert [p.user_id for p in await service.list_participants(rec.id)] == [CREATOR, 1]
    finally:
        await conn.close()
