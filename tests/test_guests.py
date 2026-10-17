"""ゲスト参加者（サーバーにいない人を名前だけで登録）。"""

from __future__ import annotations

from types import SimpleNamespace

import aiosqlite

from bot.models import Mode, Outcome, RecruitStatus, Source
from bot.repositories.db import SCHEMA_PATH, connect
from bot.repositories.recruitment_repo import RecruitmentRepository
from bot.views.render import build_embed
from tests.conftest import CREATOR, make_recruitment


async def _guests(service, rid):
    return [p.guest_name for p in await service.list_participants(rid) if p.is_guest]


async def test_add_guest_and_counts_toward_capacity(service):
    rec = await make_recruitment(service, capacity=3)
    assert await service.add_guest(rec.id, "フォロワーA") is Outcome.JOINED
    assert await service.add_guest(rec.id, "フォロワーB") is Outcome.JOINED
    assert await service.add_guest(rec.id, "フォロワーC") is Outcome.FULL
    assert await service.join(rec.id, 1, Source.BUTTON) is Outcome.FULL
    parts = await service.list_participants(rec.id)
    assert [p.guest_name for p in parts] == [None, "フォロワーA", "フォロワーB"]
    # ゲストの ID は負数で、Discord の ID（正数）と衝突しない
    assert all(p.user_id < 0 for p in parts if p.is_guest)
    assert len({p.user_id for p in parts}) == 3


async def test_duplicate_guest_name_is_rejected_ignoring_width_and_case(service):
    rec = await make_recruitment(service)
    assert await service.add_guest(rec.id, "Taro") is Outcome.JOINED
    assert await service.add_guest(rec.id, "ｔａｒｏ") is Outcome.ALREADY_JOINED
    assert await service.add_guest(rec.id, "  Taro  ") is Outcome.ALREADY_JOINED
    assert await _guests(service, rec.id) == ["Taro"]


async def test_guest_name_is_normalized(service):
    rec = await make_recruitment(service)
    assert await service.add_guest(rec.id, "改行\nあり   名前") is Outcome.JOINED
    assert await service.add_guest(rec.id, "あ" * 50) is Outcome.JOINED
    assert await _guests(service, rec.id) == ["改行 あり 名前", "あ" * 32]


async def test_remove_guest(service):
    rec = await make_recruitment(service)
    await service.add_guest(rec.id, "フォロワーA")
    await service.add_guest(rec.id, "フォロワーB")
    assert await service.remove_guest(rec.id, "フォロワーA") is Outcome.LEFT
    assert await service.remove_guest(rec.id, "フォロワーA") is Outcome.NOT_JOINED
    assert await _guests(service, rec.id) == ["フォロワーB"]
    # 新しいゲストの ID は既存と衝突しない
    assert await service.add_guest(rec.id, "フォロワーC") is Outcome.JOINED
    ids = [p.user_id for p in await service.list_participants(rec.id)]
    assert len(ids) == len(set(ids))


async def test_guest_rejected_when_closed(service):
    rec = await make_recruitment(service)
    await service.set_status(rec.id, RecruitStatus.CLOSED)
    assert await service.add_guest(rec.id, "A") is Outcome.NOT_OPEN
    assert await service.remove_guest(rec.id, "A") is Outcome.NOT_OPEN


async def test_carry_over_includes_guests_without_id_collision(service):
    old = await make_recruitment(service, mode=Mode.BUTTON)
    await service.join(old.id, 1, Source.BUTTON)
    await service.add_guest(old.id, "ゲスト旧")
    await service.add_guest(old.id, "共通ゲスト")
    await service.set_status(old.id, RecruitStatus.CLOSED)

    new = await make_recruitment(service, mode=Mode.BUTTON)
    await service.add_guest(new.id, "新ゲスト")  # 追加先にも負数 ID のゲストがいる
    await service.add_guest(new.id, "共通ゲスト")
    result = await service.carry_over(old.id, new.id)

    assert result.outcome is Outcome.JOINED
    assert len(result.added) == 2 and len(result.already_joined) == 2  # 作成者 + 共通ゲスト
    parts = await service.list_participants(new.id)
    assert [p.guest_name or p.user_id for p in parts] == [
        CREATOR, "新ゲスト", "共通ゲスト", 1, "ゲスト旧",
    ]
    ids = [p.user_id for p in parts]
    assert len(ids) == len(set(ids))


async def test_embed_shows_guest_label_and_escapes(service):
    rec = await make_recruitment(service)
    await service.add_guest(rec.id, "**太字**さん")
    parts = await service.list_participants(rec.id)
    embed = build_embed(rec, parts, {CREATOR: "募集主"})
    assert "・募集主" in embed.description
    assert r"・\*\*太字\*\*さん（ゲスト）" in embed.description
    assert "<@" not in embed.description


async def test_updater_does_not_look_up_guests(service):
    from bot.views.updater import RecruitMessageUpdater

    rec = await make_recruitment(service)
    await service.add_guest(rec.id, "フォロワーA")
    asked: list[list[int]] = []

    class FakeNames:
        async def resolve(self, guild_id, user_ids):
            asked.append(list(user_ids))
            return {u: "名前" for u in user_ids}

    updater = RecruitMessageUpdater(SimpleNamespace(), service, FakeNames())
    embed = await updater.build_embed(rec, await service.list_participants(rec.id))
    assert asked == [[CREATOR]]
    assert "フォロワーA（ゲスト）" in embed.description


async def test_migration_from_v2_adds_guest_column(tmp_path):
    db = tmp_path / "v2.db"
    async with aiosqlite.connect(db) as conn:  # 旧バージョンの DB を再現
        await conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await conn.execute("ALTER TABLE recruitments ADD COLUMN deadline_at TEXT")
        await conn.execute("PRAGMA user_version = 2")
        await conn.execute(
            "INSERT INTO recruitments (guild_id, channel_id, creator_user_id, title, mode, capacity, "
            "status, created_at) VALUES (1, 2, 3, 'old', 'button', 8, 'open', '2026-10-17T00:00:00+00:00')"
        )
        await conn.execute(
            "INSERT INTO participants (recruitment_id, user_id, status, source, joined_at) "
            "VALUES (1, 3, 'joined', 'creator', '2026-10-17T00:00:00+00:00')"
        )
        await conn.commit()
    conn = await connect(db)
    try:
        parts = await RecruitmentRepository(conn).list_participants(1)
        assert [(p.user_id, p.guest_name) for p in parts] == [(3, None)]
    finally:
        await conn.close()
