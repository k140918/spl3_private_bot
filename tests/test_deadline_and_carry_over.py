from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.models import Mode, Outcome, RecruitStatus, Source
from bot.repositories.db import connect
from bot.repositories.recruitment_repo import RecruitmentRepository
from bot.services.deadline import JST, DeadlineError, format_deadline, parse_deadline
from bot.services.recruitment_service import RecruitmentService, to_iso
from tests.conftest import CHANNEL, CREATOR, GUILD

# 2026-10-11(日) 20:00 JST
NOW = datetime(2026, 10, 11, 20, 0, tzinfo=JST).astimezone(timezone.utc)


def jst(*args: int) -> datetime:
    return datetime(*args, tzinfo=JST).astimezone(timezone.utc)


# ---- parse_deadline ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("22:30", jst(2026, 10, 11, 22, 30)),
        ("22時", jst(2026, 10, 11, 22, 0)),
        ("22時30分", jst(2026, 10, 11, 22, 30)),
        ("２２：３０", jst(2026, 10, 11, 22, 30)),  # 全角
        ("0:30", jst(2026, 10, 12, 0, 30)),  # 過ぎた時刻は翌日
        ("19:59", jst(2026, 10, 12, 19, 59)),
        ("10/12 22:30", jst(2026, 10, 12, 22, 30)),
        ("2026/10/13 9:05", jst(2026, 10, 13, 9, 5)),
        ("2026-10-13 21:00", jst(2026, 10, 13, 21, 0)),
    ],
)
def test_parse_deadline(text, expected):
    assert parse_deadline(text, NOW) == expected


def test_parse_deadline_year_rollover():
    now = jst(2026, 12, 31, 23, 0)
    assert parse_deadline("1/1 0:30", now) == jst(2027, 1, 1, 0, 30)


@pytest.mark.parametrize(
    "text",
    ["あした", "25:00", "22:60", "2/30 10:00", "2026/10/11 19:00", "10/20 22:00", "2026/10/19 22:00"],
)
def test_parse_deadline_rejects(text):
    with pytest.raises(DeadlineError):
        parse_deadline(text, NOW)


def test_format_deadline():
    assert format_deadline(to_iso(jst(2026, 10, 12, 0, 30))) == "10/12(月) 00:30"


# ---- 締切つき募集 ----


@pytest.fixture
async def clocked():
    now = [NOW]
    conn = await connect(":memory:")
    try:
        yield RecruitmentService(RecruitmentRepository(conn), clock=lambda: now[0]), now
    finally:
        await conn.close()


async def _create(service, mode=Mode.BUTTON, capacity=8, deadline=None):
    return await service.create(
        guild_id=GUILD, channel_id=CHANNEL, creator_user_id=CREATOR, title="t",
        mode=mode, capacity=capacity, start_time="21:00", deadline=deadline,
    )


async def test_join_rejected_after_deadline_even_before_loop_closes(clocked):
    service, now = clocked
    rec = await _create(service, deadline=jst(2026, 10, 11, 21, 0))
    assert await service.join(rec.id, 1, Source.BUTTON) is Outcome.JOINED
    now[0] = jst(2026, 10, 11, 21, 0)
    assert await service.join(rec.id, 2, Source.BUTTON) is Outcome.NOT_OPEN
    assert await service.leave(rec.id, 1) is Outcome.NOT_OPEN


async def test_close_due_uses_deadline_instead_of_ttl(clocked):
    service, now = clocked
    long_rec = await _create(service, deadline=jst(2026, 10, 13, 20, 0))  # TTL(6h)より先
    short_rec = await _create(service, deadline=jst(2026, 10, 11, 20, 30))
    now[0] = jst(2026, 10, 11, 20, 30)
    assert [r.id for r in await service.close_due(timedelta(hours=6))] == [short_rec.id]
    now[0] = jst(2026, 10, 12, 8, 0)  # 作成から12時間たっても締切前なら終了しない
    assert await service.close_due(timedelta(hours=6)) == []
    assert (await service.get(long_rec.id)).is_open


async def test_set_deadline(clocked):
    service, now = clocked
    rec = await _create(service)
    await service.set_deadline(rec.id, jst(2026, 10, 11, 20, 10))
    assert (await service.get(rec.id)).deadline_at == to_iso(jst(2026, 10, 11, 20, 10))
    now[0] = jst(2026, 10, 11, 20, 10)
    assert [r.id for r in await service.close_due(timedelta(hours=6))] == [rec.id]


async def test_migration_from_v1_adds_deadline_column(tmp_path):
    import aiosqlite

    from bot.repositories.db import SCHEMA_PATH

    db = tmp_path / "v1.db"
    async with aiosqlite.connect(db) as conn:  # 旧バージョンの DB を再現
        await conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await conn.execute("PRAGMA user_version = 1")
        await conn.execute(
            "INSERT INTO recruitments (guild_id, channel_id, creator_user_id, title, mode, capacity, "
            "status, created_at) VALUES (1, 2, 3, 'old', 'button', 8, 'open', '2026-10-11T00:00:00+00:00')"
        )
        await conn.commit()
    conn = await connect(db)
    try:
        rec = await RecruitmentRepository(conn).get(1)
        assert rec is not None and rec.title == "old" and rec.deadline_at is None
    finally:
        await conn.close()


# ---- /追加（手動追加は Source.COMMAND の join） ----


async def test_manual_add_respects_capacity_and_deadline(clocked):
    service, now = clocked
    rec = await _create(service, capacity=2, deadline=jst(2026, 10, 11, 21, 0))
    assert await service.join(rec.id, 1, Source.COMMAND) is Outcome.JOINED
    assert await service.join(rec.id, 1, Source.COMMAND) is Outcome.ALREADY_JOINED
    assert await service.join(rec.id, 2, Source.COMMAND) is Outcome.FULL
    await service.leave(rec.id, 1)
    now[0] = jst(2026, 10, 11, 21, 0)
    assert await service.join(rec.id, 2, Source.COMMAND) is Outcome.NOT_OPEN
    sources = {p.user_id: p.source for p in await service.list_participants(rec.id)}
    assert sources == {CREATOR: Source.CREATOR}


# ---- 引き継ぎ ----


async def test_carry_over_keeps_order_and_skips_existing(clocked):
    service, _ = clocked
    old = await _create(service)
    for uid in (1, 2, 3):
        await service.join(old.id, uid, Source.BUTTON)
    await service.set_status(old.id, RecruitStatus.CLOSED)

    new = await _create(service)
    await service.join(new.id, 2, Source.BUTTON)
    result = await service.carry_over(old.id, new.id)
    assert result.outcome is Outcome.JOINED
    assert result.added == [1, 3]
    assert result.already_joined == [CREATOR, 2]
    ids = [p.user_id for p in await service.list_participants(new.id)]
    assert ids == [CREATOR, 2, 1, 3]
    sources = {p.user_id: p.source for p in await service.list_participants(new.id)}
    assert sources[1] is Source.COMMAND


async def test_carry_over_respects_capacity(clocked):
    service, _ = clocked
    old = await _create(service, capacity=8)
    for uid in range(1, 6):
        await service.join(old.id, uid, Source.BUTTON)
    await service.set_status(old.id, RecruitStatus.CLOSED)
    new = await _create(service, capacity=4)  # 作成者 + 3人
    result = await service.carry_over(old.id, new.id)
    assert result.added == [1, 2, 3]
    assert result.full == [4, 5]
    assert len(await service.list_participants(new.id)) == 4


async def test_carry_over_rejects_closed_target_and_other_guild(clocked):
    service, _ = clocked
    old = await _create(service)
    await service.set_status(old.id, RecruitStatus.CLOSED)
    closed_target = await _create(service)
    await service.set_status(closed_target.id, RecruitStatus.CLOSED)
    assert (await service.carry_over(old.id, closed_target.id)).outcome is Outcome.NOT_OPEN
    assert (await service.carry_over(old.id, old.id)).outcome is Outcome.NOT_OPEN
    other = await service.create(
        guild_id=GUILD + 1, channel_id=999, creator_user_id=CREATOR, title="t",
        mode=Mode.BUTTON, capacity=8, start_time=None,
    )
    assert (await service.carry_over(old.id, other.id)).outcome is Outcome.NOT_FOUND


async def test_list_ended_in_channel(clocked):
    service, now = clocked
    old = await _create(service)
    await service.set_status(old.id, RecruitStatus.CLOSED)
    await _create(service)  # 受付中は候補に出ない
    assert [r.id for r in await service.list_ended_in_channel(CHANNEL)] == [old.id]
    now[0] = NOW + timedelta(days=8)
    assert await service.list_ended_in_channel(CHANNEL) == []
