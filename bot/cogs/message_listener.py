"""メッセージ方式: 発言を AI 判定して参加者に反映する。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from bot.main import RecruitBot
    from bot.services.message_intent import MessageIntentProcessor

log = logging.getLogger(__name__)

_TARGET_TYPES = (discord.MessageType.default, discord.MessageType.reply)


class MessageListener(commands.Cog):
    def __init__(self, bot: RecruitBot, processor: MessageIntentProcessor) -> None:
        self.bot = bot
        self.processor = processor

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Bot 自身・他のBot・Webhook・DM・システムメッセージは対象外
        if message.author.bot or message.webhook_id or message.guild is None:
            return
        if message.type not in _TARGET_TYPES:
            return

        # discord.py はイベントごとに別タスクで処理するので、ここで await しても他の処理は止まらない
        try:
            result = await self.processor.process(
                channel_id=message.channel.id,
                user_id=message.author.id,
                message_id=message.id,
                text=message.content,
            )
        except Exception:
            log.exception("message processing failed message=%s", message.id)
            return
        if result is None:
            return

        log.info(
            "AI judgement recruitment=%s user=%s message=%s action=%s confidence=%.2f "
            "decided=%s outcome=%s error=%s",
            result.recruitment_id, message.author.id, message.id, result.raw.action.value,
            result.raw.confidence, result.action.value,
            result.outcome.value if result.outcome else "-", result.raw.error,
        )

        if result.reaction:
            try:
                await message.add_reaction(result.reaction)
            except discord.HTTPException as e:
                log.warning("Discord API error on reaction status=%s code=%s", e.status, e.code)
        if result.changed:
            await self.bot.updater.refresh(result.recruitment_id)
