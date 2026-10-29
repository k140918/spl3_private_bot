from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import discord

from bot.models import Mode, Outcome, Recruitment, RecruitStatus, Source
from bot.repositories.db import connect
from bot.repositories.recruitment_repo import RecruitmentRepository
from bot.services.recruitment_service import RecruitmentService
from bot.views.render import build_embed
from bot.views.bulk_add import BulkAddView
from bot.views.updater import RecruitMessageUpdater
from tests.conftest import CREATOR, make_recruitment


async def test_bulk_add_duplicates_capacity_and_concurrent_join(service):
    rec = await make_recruitment(service, capacity=4)
    results, single = await asyncio.gather(
        service.join_many(rec.id, [CREATOR, 1, 1, 2, 3, 4]),
        service.join(rec.id, 5, Source.BUTTON),
    )
    assert results[CREATOR] is Outcome.ALREADY_JOINED
    assert len(results) == 5
    assert list(results.values()).count(Outcome.JOINED) + (single is Outcome.JOINED) == 3
    assert len(await service.list_participants(rec.id)) == 4


async def test_bulk_add_rejects_closed(service):
    rec = await make_recruitment(service)
    await service.set_status(rec.id, RecruitStatus.CLOSED)
    assert await service.join_many(rec.id, [1, 2]) == {1: Outcome.NOT_OPEN, 2: Outcome.NOT_OPEN}


async def test_bulk_add_rejects_expired_deadline(service):
    rec = await make_recruitment(service)
    await service.set_deadline(rec.id, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert await service.join_many(rec.id, [1, 2]) == {1: Outcome.NOT_OPEN, 2: Outcome.NOT_OPEN}


async def test_bulk_view_rechecks_permission(service):
    rec = await make_recruitment(service)
    bot = Mock(service=service)
    view = BulkAddView(bot, rec.id, 42)
    interaction = Mock(user=Mock(id=42), guild_id=rec.guild_id)
    interaction.permissions.manage_messages = False
    interaction.response.send_message = AsyncMock()
    await view.select_members.callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    assert len(await service.list_participants(rec.id)) == 1
    view.stop()


async def test_bulk_view_reports_added_full_and_bot(service):
    rec = await make_recruitment(service, capacity=2)
    bot = Mock(service=service, updater=Mock(refresh=AsyncMock()))
    view = BulkAddView(bot, rec.id, CREATOR)
    members = [Mock(spec=discord.Member, id=uid, bot=is_bot) for uid, is_bot in [(1, False), (2, False), (3, True)]]
    view.select_members._values = members
    interaction = Mock(user=Mock(id=CREATOR), guild_id=rec.guild_id)
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    await view.select_members.callback(interaction)
    result = interaction.edit_original_response.call_args.kwargs
    assert "追加しました：<@1>" in result["content"]
    assert "定員のため追加できませんでした：<@2>" in result["content"]
    assert "Bot" in result["content"]
    assert result["view"] is None
    bot.updater.refresh.assert_awaited_once_with(rec.id)


async def test_notification_claim_only_when_full_once_and_persistent(tmp_path):
    path = tmp_path / "notifications.db"
    conn = await connect(path)
    service = RecruitmentService(RecruitmentRepository(conn))
    try:
        rec = await make_recruitment(service, capacity=2)
        await service.attach_message(rec.id, 123)
        assert not await service.claim_full_notification(rec.id)
        await service.add_guest(rec.id, "ゲスト")
        claims = await asyncio.gather(*(service.claim_full_notification(rec.id) for _ in range(5)))
        assert claims.count(True) == 1
    finally:
        await conn.close()
    conn = await connect(path)
    try:
        service = RecruitmentService(RecruitmentRepository(conn))
        assert not await service.claim_full_notification(rec.id)
        await service.remove_guest(rec.id, "ゲスト")
        await service.join(rec.id, 3, Source.MESSAGE)
        assert not await service.claim_full_notification(rec.id)
        await service.release_full_notification(rec.id)
        assert await service.claim_full_notification(rec.id)
    finally:
        await conn.close()


async def test_updater_notifies_only_creator_and_does_not_repeat(service):
    rec = await make_recruitment(service, capacity=2)
    await service.attach_message(rec.id, 123)
    channel = Mock()
    channel.send = AsyncMock()
    channel.get_partial_message.return_value.edit = AsyncMock()
    client = Mock()
    client.get_partial_messageable.return_value = channel
    names = Mock(resolve=AsyncMock(return_value={}))
    updater = RecruitMessageUpdater(client, service, names)
    await updater.refresh(rec.id)
    channel.send.assert_not_awaited()
    await service.join(rec.id, 1, Source.BUTTON)
    await asyncio.gather(updater.refresh(rec.id), updater.refresh(rec.id))
    channel.send.assert_awaited_once()
    args, kwargs = channel.send.call_args
    assert f"<@{CREATOR}>" in args[0]
    assert "/123" in args[0]
    assert kwargs["allowed_mentions"].to_dict() == {"users": [CREATOR], "parse": []}


async def test_notification_send_failure_allows_retry(service):
    rec = await make_recruitment(service, capacity=2)
    await service.attach_message(rec.id, 123)
    await service.join(rec.id, 1, Source.BUTTON)
    channel = Mock()
    channel.get_partial_message.return_value.edit = AsyncMock()
    channel.send = AsyncMock(side_effect=discord.Forbidden(Mock(status=403, reason="Forbidden"), "Denied"))
    client = Mock()
    client.get_partial_messageable.return_value = channel
    updater = RecruitMessageUpdater(client, service, Mock(resolve=AsyncMock(return_value={})))
    await updater.refresh(rec.id)
    channel.send.side_effect = None
    await updater.refresh(rec.id)
    assert channel.send.await_count == 2


def test_time_labels_without_deadline():
    rec = Recruitment(1, 1, 100, 123, CREATOR, "募集", Mode.BUTTON, 8, None,
                      RecruitStatus.OPEN, "2026-10-29T00:00:00+00:00")
    embed = build_embed(rec, [], {})
    assert "開始予定：未定" in embed.description
    assert "参加受付終了" not in embed.description
    scheduled = replace(rec, start_time="20:00", deadline_at="2026-10-29T12:00:00+00:00")
    description = build_embed(scheduled, [], {}).description
    assert "開始予定：20:00～" in description
    assert "参加受付終了：10/29(木) 21:00（開始時刻ではありません）" in description
