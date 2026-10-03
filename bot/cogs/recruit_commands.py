"""/プラベ（募集作成）と /取消（参加取消）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.models import Mode, Outcome, Recruitment, RecruitStatus
from bot.services.recruitment_service import ChannelBusyError
from bot.views.render import build_embed, build_view

if TYPE_CHECKING:
    from bot.main import RecruitBot

log = logging.getLogger(__name__)

DEFAULT_TITLE = "プラベ募集"
DEFAULT_CAPACITY = 8


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())  # 改行や連続空白を潰す
    return text or None


def _missing_permissions(
    channel: discord.abc.MessageableChannel | None,
    perms: discord.Permissions,
    mode: Mode,
) -> list[str]:
    missing: list[str] = []
    can_send = perms.send_messages_in_threads if isinstance(channel, discord.Thread) else perms.send_messages
    if not can_send:
        missing.append("メッセージを送信")
    if not perms.embed_links:
        missing.append("埋め込みリンク")
    if mode.uses_message:
        if not perms.add_reactions:
            missing.append("リアクションの追加")
        if not perms.read_message_history:
            missing.append("メッセージ履歴を読む")
    return missing


class RecruitCommands(commands.Cog):
    def __init__(self, bot: RecruitBot) -> None:
        self.bot = bot

    # ---------------- /プラベ ----------------

    @app_commands.command(name="プラベ", description="スプラトゥーン3のプライベートマッチ参加者を募集します")
    @app_commands.guild_only()
    @app_commands.rename(mode="方式", capacity="定員", start_time="開始時間", title="タイトル")
    @app_commands.describe(
        mode="参加方法（省略時: ボタン）",
        capacity="定員（2〜10、省略時: 8）",
        start_time="開始時間（例: 21:00）",
        title="タイトル（省略時: プラベ募集）",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="ボタン", value=Mode.BUTTON.value),
            app_commands.Choice(name="メッセージ（AI判定）", value=Mode.MESSAGE.value),
            app_commands.Choice(name="両方", value=Mode.BOTH.value),
        ]
    )
    async def create_recruitment(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str] | None = None,
        capacity: app_commands.Range[int, 2, 10] = DEFAULT_CAPACITY,
        start_time: app_commands.Range[str, 1, 20] | None = None,
        title: app_commands.Range[str, 1, 50] | None = None,
    ) -> None:
        assert interaction.guild_id is not None and interaction.channel_id is not None
        selected = Mode(mode.value) if mode else Mode.BUTTON
        channel = interaction.channel

        if selected.uses_message and not self.bot.settings.message_mode_enabled:
            await interaction.response.send_message(
                "メッセージ方式は現在使えません（AI判定が無効です）。ボタン方式を使ってください。",
                ephemeral=True,
            )
            return
        missing = _missing_permissions(channel, interaction.app_permissions, selected)
        if missing or channel is None:
            await interaction.response.send_message(
                f"Botにこのチャンネルの権限が足りません: {', '.join(missing) or '不明'}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            rec = await self.bot.service.create(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                creator_user_id=interaction.user.id,
                title=_clean(title) or DEFAULT_TITLE,
                mode=selected,
                capacity=capacity,
                start_time=_clean(start_time),
            )
        except ChannelBusyError as e:
            existing = f"（募集ID: {e.existing.id}）" if e.existing else ""
            await interaction.followup.send(
                f"このチャンネルには受付中のメッセージ方式の募集が既にあります{existing}。\n"
                "「行く」がどの募集への発言か曖昧になるため、メッセージ方式は1チャンネル1件までです。\n"
                "ボタン方式にするか、別のチャンネルで作成してください。"
                f"（募集は作成から {self.bot.settings.recruit_ttl_hours:g} 時間で自動終了します）",
                ephemeral=True,
            )
            return

        participants = await self.bot.service.list_participants(rec.id)
        try:
            message = await channel.send(embed=build_embed(rec, participants), view=build_view(rec))
        except discord.HTTPException as e:
            log.warning(
                "Discord API error on post id=%s status=%s code=%s", rec.id, e.status, e.code
            )
            await self.bot.service.set_status(rec.id, RecruitStatus.CANCELLED)
            await interaction.followup.send("募集メッセージを投稿できませんでした。", ephemeral=True)
            return

        await self.bot.service.attach_message(rec.id, message.id)
        await interaction.followup.send(f"募集を作成しました（募集ID: {rec.id}）", ephemeral=True)

    # ---------------- /取消 ----------------

    @app_commands.command(
        name="取消", description="プラベ募集への参加を取り消します（募集作成者は他の参加者も取り消せます）"
    )
    @app_commands.guild_only()
    @app_commands.rename(user="ユーザー", recruitment_id="募集")
    @app_commands.describe(
        user="取り消す参加者（省略時: 自分）",
        recruitment_id="対象の募集（このチャンネルの受付中の募集が1件だけなら省略可）",
    )
    async def cancel_participation(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        recruitment_id: int | None = None,
    ) -> None:
        rec = await self._resolve_recruitment(interaction, recruitment_id)
        if isinstance(rec, str):
            await interaction.response.send_message(rec, ephemeral=True)
            return

        target = user or interaction.user
        if target.id != interaction.user.id:
            is_creator = interaction.user.id == rec.creator_user_id
            if not (is_creator or interaction.permissions.manage_messages):
                await interaction.response.send_message(
                    "他の人の参加を取り消せるのは募集作成者か「メッセージの管理」権限を持つ人だけです。",
                    ephemeral=True,
                )
                return

        outcome = await self.bot.service.leave(rec.id, target.id)
        replies = {
            Outcome.LEFT: f"<@{target.id}> の参加を取り消しました。",
            Outcome.NOT_JOINED: f"<@{target.id}> はこの募集に参加していません。",
            Outcome.NOT_OPEN: "この募集は終了しています。",
            Outcome.NOT_FOUND: "募集が見つかりません。",
        }
        await interaction.response.send_message(replies[outcome], ephemeral=True)
        if outcome is Outcome.LEFT:
            if target.id != interaction.user.id:
                log.info(
                    "manual removal recruitment=%s target=%s by=%s",
                    rec.id, target.id, interaction.user.id,
                )
            await self.bot.updater.refresh(rec.id)

    @cancel_participation.autocomplete("recruitment_id")
    async def _recruitment_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.channel_id is None:
            return []
        recs = await self.bot.service.list_open_in_channel(interaction.channel_id)
        choices = []
        for r in recs:
            label = f"#{r.id} {r.title}" + (f" {r.start_time}" if r.start_time else "")
            if current and current not in label:
                continue
            choices.append(app_commands.Choice(name=label[:100], value=r.id))
        return choices[:25]

    async def _resolve_recruitment(
        self, interaction: discord.Interaction, recruitment_id: int | None
    ) -> Recruitment | str:
        """対象募集を返す。特定できなければエラーメッセージ文字列。"""
        if recruitment_id is not None:
            rec = await self.bot.service.get(recruitment_id)
            if rec is None or rec.guild_id != interaction.guild_id:
                return "募集が見つかりません。"
            if not rec.is_open:
                return "この募集は終了しています。"
            return rec
        assert interaction.channel_id is not None
        recs = await self.bot.service.list_open_in_channel(interaction.channel_id)
        if not recs:
            return "このチャンネルに受付中の募集がありません。"
        if len(recs) > 1:
            return "このチャンネルには受付中の募集が複数あります。「募集」を選んでください。"
        return recs[0]
