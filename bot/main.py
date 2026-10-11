"""Bot 本体の組み立て。"""

from __future__ import annotations

import logging

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.maintenance import Maintenance
from bot.cogs.message_listener import MessageListener
from bot.cogs.recruit_commands import RecruitCommands
from bot.config import Settings
from bot.repositories.db import connect
from bot.repositories.recruitment_repo import RecruitmentRepository
from bot.services.classifier.gemini_classifier import GeminiClassifier
from bot.services.context_buffer import ContextBuffer
from bot.services.message_intent import IntentSettings, MessageIntentProcessor
from bot.services.recruitment_service import RecruitmentService
from bot.views.buttons import RecruitButton
from bot.views.member_names import MemberNameResolver
from bot.views.updater import RecruitMessageUpdater

log = logging.getLogger(__name__)

# 音声機能は使わないので、関連ライブラリ未導入の警告を出さない
discord.VoiceClient.warn_nacl = False
discord.VoiceClient.warn_dave = False


def build_intents(settings: Settings) -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True  # スラッシュコマンド・チャンネル情報
    intents.guild_messages = True  # on_message / メッセージ削除イベント
    # 特権 Intent。メッセージ方式を使うときだけ要求する（Developer Portal での有効化が必要）
    intents.message_content = settings.message_mode_enabled
    return intents


class RecruitBot(commands.Bot):
    service: RecruitmentService
    updater: RecruitMessageUpdater
    member_names: MemberNameResolver
    context_buffer: ContextBuffer | None = None

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=build_intents(settings),
            help_command=None,
            # 低メモリ化: メッセージ・メンバーのキャッシュを持たない
            max_messages=None,
            member_cache_flags=discord.MemberCacheFlags.none(),
            chunk_guilds_at_startup=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self._db: aiosqlite.Connection | None = None

    async def setup_hook(self) -> None:
        s = self.settings
        self._db = await connect(s.db_path)
        self.service = RecruitmentService(RecruitmentRepository(self._db))
        self.member_names = MemberNameResolver(self)
        self.updater = RecruitMessageUpdater(self, self.service, self.member_names)

        self.add_dynamic_items(RecruitButton)
        await self.add_cog(RecruitCommands(self))
        await self.add_cog(Maintenance(self))

        if s.message_mode_enabled:
            classifier = GeminiClassifier.from_prompt_file(
                s.prompt_path,
                api_key=s.gemini_api_key,
                model=s.gemini_model,
                timeout_sec=s.gemini_timeout_sec,
                max_concurrency=s.gemini_max_concurrency,
                thinking_level=s.gemini_thinking_level,
            )
            self.context_buffer = ContextBuffer(s.ai_context_messages, s.ai_context_max_age_sec)
            processor = MessageIntentProcessor(
                self.service,
                classifier,
                self.context_buffer,
                IntentSettings(
                    confidence_threshold=s.ai_confidence_threshold,
                    max_message_chars=s.ai_max_message_chars,
                    user_cooldown_sec=s.ai_user_cooldown_sec,
                ),
            )
            await self.add_cog(MessageListener(self, processor))

        self.tree.on_error = self._on_app_command_error
        await self._sync_commands()

    async def _sync_commands(self) -> None:
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
            except discord.Forbidden:
                log.error(
                    "DEV_GUILD_ID のサーバーにコマンドを登録できません (Missing Access)。"
                    "DEV_GUILD_ID がサーバーIDで合っているか、Bot が applications.commands "
                    "スコープ付きでそのサーバーに招待されているか確認してください"
                )
                raise
            log.info("synced %d commands to dev guild %s", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            log.info("synced %d global commands", len(synced))

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error(
            "app command error command=%s type=%s",
            interaction.command.name if interaction.command else "-",
            type(error).__name__,
            exc_info=error,
        )
        text = "エラーが発生しました。時間をおいて再度お試しください。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_ready(self) -> None:
        log.info(
            "bot ready user_id=%s guilds=%d message_mode=%s",
            self.user.id if self.user else "-", len(self.guilds), self.settings.message_mode_enabled,
        )

    async def close(self) -> None:
        await super().close()
        if self._db is not None:
            await self._db.close()
            self._db = None
