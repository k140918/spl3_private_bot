"""募集文（/募集文）。"""

from __future__ import annotations

import aiosqlite

from bot.models import Outcome, RecruitStatus
from bot.repositories.db import SCHEMA_PATH, connect
from bot.repositories.recruitment_repo import RecruitmentRepository
from bot.services.recruitment_service import DESCRIPTION_MAX, normalize_description
from bot.views.description_modal import DescriptionModal
from bot.views.render import build_embed
from tests.conftest import CREATOR, make_recruitment


async def test_set_and_clear_description(service):
    rec = await make_recruitment(service)
    assert rec.description is None
    text = "ルール：エリア / ヤグラ\n初心者歓迎、VCなしOK"
    assert await service.set_description(rec.id, text) is Outcome.JOINED
    assert (await service.get(rec.id)).description == text
    assert await service.set_description(rec.id, "  \n ") is Outcome.JOINED  # 空なら削除
    assert (await service.get(rec.id)).description is None


async def test_description_rejected_when_closed_or_missing(service):
    rec = await make_recruitment(service)
    await service.set_status(rec.id, RecruitStatus.CLOSED)
    assert await service.set_description(rec.id, "x") is Outcome.NOT_OPEN
    assert await service.set_description(99999, "x") is Outcome.NOT_FOUND


def test_normalize_description():
    assert normalize_description("a  \r\nb\t") == "a\nb"
    assert normalize_description("a\n\n\n\n\nb") == "a\n\n\nb"  # 空行は2行まで
    assert normalize_description("\n\n  本文  \n\n") == "本文"
    assert normalize_description("   ") is None
    assert len(normalize_description("あ" * 2000)) == DESCRIPTION_MAX


async def test_embed_shows_description_above_details(service):
    rec = await make_recruitment(service, start_time="21:00")
    await service.set_description(rec.id, "初心者歓迎\nVCなしOK")
    rec = await service.get(rec.id)
    embed = build_embed(rec, await service.list_participants(rec.id), {CREATOR: "募集主"})
    assert embed.description.startswith("初心者歓迎\nVCなしOK\n\n⏰ 開始予定：21:00～")
    assert "・募集主" in embed.description


async def test_embed_without_description_is_unchanged(service):
    rec = await make_recruitment(service, start_time="21:00")
    embed = build_embed(rec, await service.list_participants(rec.id), {CREATOR: "募集主"})
    assert embed.description.startswith("⏰ 開始予定：21:00～")


async def test_modal_prefills_current_description():
    modal = DescriptionModal(1, "前回の募集文")
    assert modal.text.default == "前回の募集文"
    assert modal.text.max_length == DESCRIPTION_MAX
    assert modal.text.required is False


async def test_migration_from_v3_adds_description_column(tmp_path):
    db = tmp_path / "v3.db"
    async with aiosqlite.connect(db) as conn:  # 旧バージョンの DB を再現
        await conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await conn.execute("ALTER TABLE recruitments ADD COLUMN deadline_at TEXT")
        await conn.execute("ALTER TABLE participants ADD COLUMN guest_name TEXT")
        await conn.execute("PRAGMA user_version = 3")
        await conn.execute(
            "INSERT INTO recruitments (guild_id, channel_id, creator_user_id, title, mode, capacity, "
            "status, created_at) VALUES (1, 2, 3, 'old', 'button', 8, 'open', '2026-10-18T00:00:00+00:00')"
        )
        await conn.commit()
    conn = await connect(db)
    try:
        rec = await RecruitmentRepository(conn).get(1)
        assert rec is not None and rec.title == "old" and rec.description is None
    finally:
        await conn.close()
