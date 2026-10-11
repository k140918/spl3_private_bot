"""募集の自動終了と、募集メッセージ削除時の後始末。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from bot.models import RecruitStatus

if TYPE_CHECKING:
    from bot.main import RecruitBot

log = logging.getLogger(__name__)


class Maintenance(commands.Cog):
    def __init__(self, bot: RecruitBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.expire_loop.start()

    async def cog_unload(self) -> None:
        self.expire_loop.cancel()

    @tasks.loop(minutes=1)  # 締切時刻の判定精度。1分ごとの軽いSQLだけなので負荷は小さい
    async def expire_loop(self) -> None:
        ttl = timedelta(hours=self.bot.settings.recruit_ttl_hours)
        try:
            expired = await self.bot.service.close_due(ttl)
        except Exception:
            log.exception("expire task failed")
            return
        for rec in expired:
            log.info("recruitment expired id=%s", rec.id)
            if self.bot.context_buffer:
                self.bot.context_buffer.clear(rec.channel_id)
            await self.bot.updater.refresh(rec.id)  # 【終了】表示にしてボタンを外す

    @expire_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """募集メッセージが削除されたら募集も取り消す（チャンネルの枠を空ける）。"""
        rec = await self.bot.service.get_by_message_id(payload.message_id)
        if rec is None or not rec.is_open:
            return
        await self.bot.service.set_status(rec.id, RecruitStatus.CANCELLED)
        if self.bot.context_buffer:
            self.bot.context_buffer.clear(rec.channel_id)
