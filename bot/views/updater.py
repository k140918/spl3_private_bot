"""募集メッセージの編集。常に DB の最新状態で描き直すので、同時更新でも表示が古くならない。"""

from __future__ import annotations

import asyncio
import logging

import discord

from bot.models import Participant, Recruitment, RecruitStatus
from bot.services.recruitment_service import RecruitmentService
from bot.views.member_names import MemberNameResolver
from bot.views.render import build_embed, build_view

log = logging.getLogger(__name__)


class RecruitMessageUpdater:
    def __init__(
        self,
        client: discord.Client,
        service: RecruitmentService,
        names: MemberNameResolver,
    ) -> None:
        self._client = client
        self._service = service
        self._names = names
        self._locks: dict[int, asyncio.Lock] = {}

    async def build_embed(
        self, rec: Recruitment, participants: list[Participant]
    ) -> discord.Embed:
        """参加者の表示名を取得して Embed を組み立てる（初回投稿・更新で共通）。"""
        names = await self._names.resolve(rec.guild_id, [p.user_id for p in participants])
        return build_embed(rec, participants, names)

    async def refresh(self, recruitment_id: int) -> None:
        lock = self._locks.setdefault(recruitment_id, asyncio.Lock())
        async with lock:
            rec = await self._service.get(recruitment_id)
            if rec is None or rec.message_id is None:
                return
            participants = await self._service.list_participants(recruitment_id)
            # PartialMessage なのでチャンネル・メッセージのキャッシュ不要
            message = self._client.get_partial_messageable(rec.channel_id).get_partial_message(
                rec.message_id
            )
            try:
                embed = await self.build_embed(rec, participants)
                await message.edit(embed=embed, view=build_view(rec))
            except discord.NotFound:
                log.warning("recruit message not found; cancelling id=%s", recruitment_id)
                if rec.is_open:
                    await self._service.set_status(recruitment_id, RecruitStatus.CANCELLED)
            except discord.HTTPException as e:
                log.warning(
                    "Discord API error on edit id=%s status=%s code=%s",
                    recruitment_id, e.status, e.code,
                )
        if not rec.is_open:
            self._locks.pop(recruitment_id, None)
