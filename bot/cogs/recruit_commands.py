"""/プラベ（募集作成）、/取消（参加取消）、/追加、/募集文、/締切、/引き継ぎ。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.models import Mode, Outcome, Recruitment, RecruitStatus, Source
from bot.services.deadline import DeadlineError, format_deadline, parse_deadline
from bot.services.recruitment_service import (
    GUEST_NAME_MAX,
    ChannelBusyError,
    normalize_guest_name,
    to_iso,
    utcnow,
)
from bot.views.member_names import sanitize_name
from bot.views.description_modal import DescriptionModal
from bot.views.bulk_add import BulkAddView
from bot.views.render import GUEST_LABEL, build_view

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


def _can_manage(interaction: discord.Interaction, rec: Recruitment) -> bool:
    """募集作成者か「メッセージの管理」権限を持つ人。"""
    return interaction.user.id == rec.creator_user_id or interaction.permissions.manage_messages


def _choices(
    recs: list[Recruitment], current: str, suffix: str = ""
) -> list[app_commands.Choice[int]]:
    choices = []
    for r in recs:
        label = f"#{r.id} {r.title}" + (f" {r.start_time}" if r.start_time else "") + suffix
        if current and current not in label:
            continue
        choices.append(app_commands.Choice(name=label[:100], value=r.id))
    return choices[:25]


class RecruitCommands(commands.Cog):
    def __init__(self, bot: RecruitBot) -> None:
        self.bot = bot

    # ---------------- /プラベ ----------------

    @app_commands.command(name="プラベ", description="スプラトゥーン3のプライベートマッチ参加者を募集します")
    @app_commands.guild_only()
    @app_commands.rename(
        mode="方式", capacity="定員", start_time="開始時間", title="タイトル", deadline="締切"
    )
    @app_commands.describe(
        mode="参加方法（省略時: ボタン）",
        capacity="定員（2〜10、省略時: 8）",
        start_time="プラベの開始予定時刻（例: 21:00）。省略すると開始予定は未定と表示",
        title="タイトル（省略時: プラベ募集）",
        deadline="参加受付の終了時刻（開始・プレイ終了時刻ではありません）。省略可",
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
        deadline: app_commands.Range[str, 1, 20] | None = None,
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
        try:
            deadline_dt = parse_deadline(deadline, utcnow()) if deadline else None
        except DeadlineError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
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
                deadline=deadline_dt,
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

        self.bot.member_names.remember(interaction.guild_id, interaction.user)
        participants = await self.bot.service.list_participants(rec.id)
        embed = await self.bot.updater.build_embed(rec, participants)
        try:
            message = await channel.send(embed=embed, view=build_view(rec))
        except discord.HTTPException as e:
            log.warning(
                "Discord API error on post id=%s status=%s code=%s", rec.id, e.status, e.code
            )
            await self.bot.service.set_status(rec.id, RecruitStatus.CANCELLED)
            await interaction.followup.send("募集メッセージを投稿できませんでした。", ephemeral=True)
            return

        await self.bot.service.attach_message(rec.id, message.id)
        note = f"、締切: {format_deadline(rec.deadline_at)}" if rec.deadline_at else ""
        await interaction.followup.send(f"募集を作成しました（募集ID: {rec.id}{note}）", ephemeral=True)

    # ---------------- /取消 ----------------

    @app_commands.command(
        name="取消", description="プラベ募集への参加を取り消します（募集作成者は他の参加者も取り消せます）"
    )
    @app_commands.guild_only()
    @app_commands.rename(user="ユーザー", guest="ゲスト", recruitment_id="募集")
    @app_commands.describe(
        user="取り消す参加者（省略時: 自分）",
        guest="取り消すゲスト（サーバーにいない人）の名前",
        recruitment_id="対象の募集（このチャンネルの受付中の募集が1件だけなら省略可）",
    )
    async def cancel_participation(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        guest: str | None = None,
        recruitment_id: int | None = None,
    ) -> None:
        rec = await self._resolve_recruitment(interaction, recruitment_id)
        if isinstance(rec, str):
            await interaction.response.send_message(rec, ephemeral=True)
            return
        if guest is not None:
            if user is not None:
                await interaction.response.send_message(
                    "「ユーザー」か「ゲスト」のどちらか一方を指定してください。", ephemeral=True
                )
                return
            await self._remove_guest(interaction, rec, guest)
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

    async def _remove_guest(
        self, interaction: discord.Interaction, rec: Recruitment, guest: str
    ) -> None:
        if not _can_manage(interaction, rec):
            await interaction.response.send_message(
                "ゲストを取り消せるのは募集作成者か「メッセージの管理」権限を持つ人だけです。",
                ephemeral=True,
            )
            return
        label = sanitize_name(normalize_guest_name(guest) or "")
        outcome = await self.bot.service.remove_guest(rec.id, guest)
        replies = {
            Outcome.LEFT: f"{label}{GUEST_LABEL}の参加を取り消しました。",
            Outcome.NOT_JOINED: f"{label}{GUEST_LABEL}はこの募集に参加していません。",
            Outcome.NOT_OPEN: "この募集は終了しています。",
            Outcome.NOT_FOUND: "募集が見つかりません。",
        }
        await interaction.response.send_message(replies[outcome], ephemeral=True)
        if outcome is Outcome.LEFT:
            log.info("manual guest removal recruitment=%s by=%s", rec.id, interaction.user.id)
            await self.bot.updater.refresh(rec.id)

    @cancel_participation.autocomplete("guest")
    async def _guest_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """このチャンネルの受付中の募集にいるゲスト名を候補に出す。"""
        if interaction.channel_id is None:
            return []
        names: list[str] = []
        for r in await self.bot.service.list_open_in_channel(interaction.channel_id):
            for p in await self.bot.service.list_participants(r.id):
                if p.guest_name and p.guest_name not in names and current in p.guest_name:
                    names.append(p.guest_name)
        return [app_commands.Choice(name=n, value=n) for n in names[:25]]

    @cancel_participation.autocomplete("recruitment_id")
    async def _recruitment_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.channel_id is None:
            return []
        recs = await self.bot.service.list_open_in_channel(interaction.channel_id)
        return _choices(recs, current)

    # ---------------- /追加 ----------------

    @app_commands.command(
        name="追加", description="プラベ募集に参加者を手動で追加します（募集作成者のみ）"
    )
    @app_commands.guild_only()
    @app_commands.rename(user="ユーザー", guest="ゲスト", recruitment_id="募集")
    @app_commands.describe(
        user="追加する人（サーバーのメンバー）",
        guest="サーバーにいない人の名前（ユーザーの代わりに指定）",
        recruitment_id="対象の募集（このチャンネルの受付中の募集が1件だけなら省略可）",
    )
    async def add_participant(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        guest: app_commands.Range[str, 1, GUEST_NAME_MAX] | None = None,
        recruitment_id: int | None = None,
    ) -> None:
        if (user is None) == (guest is None):
            await interaction.response.send_message(
                "「ユーザー」か「ゲスト」のどちらか一方を指定してください。\n"
                "サーバーにいない人は「ゲスト」に名前を入れると追加できます。",
                ephemeral=True,
            )
            return
        rec = await self._resolve_recruitment(interaction, recruitment_id)
        if isinstance(rec, str):
            await interaction.response.send_message(rec, ephemeral=True)
            return
        if not _can_manage(interaction, rec):
            await interaction.response.send_message(
                "参加者を追加できるのは募集作成者か「メッセージの管理」権限を持つ人だけです。",
                ephemeral=True,
            )
            return
        if guest is not None:
            await self._add_guest(interaction, rec, guest)
            return
        assert user is not None
        if user.bot:
            await interaction.response.send_message("Botは追加できません。", ephemeral=True)
            return

        outcome = await self.bot.service.join(rec.id, user.id, Source.COMMAND)
        replies = {
            Outcome.JOINED: f"<@{user.id}> を参加者に追加しました。",
            Outcome.ALREADY_JOINED: f"<@{user.id}> はすでに参加しています。",
            Outcome.FULL: "満員のため追加できません。",
            Outcome.NOT_OPEN: "この募集は終了しています。",
            Outcome.NOT_FOUND: "募集が見つかりません。",
        }
        await interaction.response.send_message(replies[outcome], ephemeral=True)
        if outcome is Outcome.JOINED:
            log.info("manual add recruitment=%s target=%s by=%s", rec.id, user.id, interaction.user.id)
            self.bot.member_names.remember(interaction.guild_id, user)
            await self.bot.updater.refresh(rec.id)

    async def _add_guest(
        self, interaction: discord.Interaction, rec: Recruitment, guest: str
    ) -> None:
        name = normalize_guest_name(guest)
        if not name:
            await interaction.response.send_message("ゲストの名前を入力してください。", ephemeral=True)
            return
        label = sanitize_name(name)
        outcome = await self.bot.service.add_guest(rec.id, name)
        replies = {
            Outcome.JOINED: f"{label}{GUEST_LABEL}を参加者に追加しました。",
            Outcome.ALREADY_JOINED: f"{label}{GUEST_LABEL}はすでに参加しています。",
            Outcome.FULL: "満員のため追加できません。",
            Outcome.NOT_OPEN: "この募集は終了しています。",
            Outcome.NOT_FOUND: "募集が見つかりません。",
        }
        await interaction.response.send_message(replies[outcome], ephemeral=True)
        if outcome is Outcome.JOINED:
            log.info("manual guest add recruitment=%s by=%s", rec.id, interaction.user.id)
            await self.bot.updater.refresh(rec.id)

    @add_participant.autocomplete("recruitment_id")
    async def _add_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._recruitment_autocomplete(interaction, current)

    # ---------------- /募集文 ----------------

    @app_commands.command(name="一括追加", description="複数のサーバーメンバーを選んで募集に追加します")
    @app_commands.guild_only()
    @app_commands.rename(recruitment_id="募集")
    @app_commands.describe(recruitment_id="対象の募集（このチャンネルに1件だけなら省略可）")
    async def bulk_add(
        self, interaction: discord.Interaction, recruitment_id: int | None = None
    ) -> None:
        rec = await self._resolve_recruitment(interaction, recruitment_id)
        if isinstance(rec, str):
            await interaction.response.send_message(rec, ephemeral=True)
            return
        if not _can_manage(interaction, rec):
            await interaction.response.send_message(
                "参加者を追加できるのは募集作成者か「メッセージの管理」権限を持つ人だけです。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "追加するメンバーをまとめて選択してください（3分以内）。選択を確定すると追加します。\n"
            "参加済みの人はスキップし、空き枠まで追加します。",
            view=BulkAddView(self.bot, rec.id, interaction.user.id), ephemeral=True,
        )

    @bulk_add.autocomplete("recruitment_id")
    async def _bulk_add_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._recruitment_autocomplete(interaction, current)

    @app_commands.command(
        name="募集文", description="募集メッセージに載せる募集文を入力・修正します（募集作成者のみ）"
    )
    @app_commands.guild_only()
    @app_commands.rename(recruitment_id="募集")
    @app_commands.describe(
        recruitment_id="対象の募集（このチャンネルの受付中の募集が1件だけなら省略可）",
    )
    async def edit_description(
        self, interaction: discord.Interaction, recruitment_id: int | None = None
    ) -> None:
        rec = await self._resolve_recruitment(interaction, recruitment_id)
        if isinstance(rec, str):
            await interaction.response.send_message(rec, ephemeral=True)
            return
        if not _can_manage(interaction, rec):
            await interaction.response.send_message(
                "募集文を編集できるのは募集作成者か「メッセージの管理」権限を持つ人だけです。",
                ephemeral=True,
            )
            return
        # 今の募集文を入れた状態でフォームを開く（あとから修正できる）
        await interaction.response.send_modal(DescriptionModal(rec.id, rec.description))

    @edit_description.autocomplete("recruitment_id")
    async def _description_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._recruitment_autocomplete(interaction, current)

    # ---------------- /締切 ----------------

    @app_commands.command(
        name="締切", description="募集を今すぐ締め切るか、締切時刻を設定・変更します（募集作成者のみ）"
    )
    @app_commands.guild_only()
    @app_commands.rename(when="時刻", recruitment_id="募集")
    @app_commands.describe(
        when="締切時刻（例: 22:30 / 10/12 22:30）。省略すると今すぐ締め切ります",
        recruitment_id="対象の募集（このチャンネルの受付中の募集が1件だけなら省略可）",
    )
    async def close_recruitment(
        self,
        interaction: discord.Interaction,
        when: app_commands.Range[str, 1, 20] | None = None,
        recruitment_id: int | None = None,
    ) -> None:
        rec = await self._resolve_recruitment(interaction, recruitment_id)
        if isinstance(rec, str):
            await interaction.response.send_message(rec, ephemeral=True)
            return
        if not _can_manage(interaction, rec):
            await interaction.response.send_message(
                "締切を操作できるのは募集作成者か「メッセージの管理」権限を持つ人だけです。",
                ephemeral=True,
            )
            return

        if when is None:
            await self.bot.service.set_status(rec.id, RecruitStatus.CLOSED)
            if self.bot.context_buffer:
                self.bot.context_buffer.clear(rec.channel_id)
            log.info("recruitment closed manually id=%s by=%s", rec.id, interaction.user.id)
            await interaction.response.send_message(
                f"募集（ID: {rec.id}）を締め切りました。\n"
                f"誤って締め切った場合は、新しく `/プラベ` で募集を作り、"
                f"`/引き継ぎ 元の募集:#{rec.id}` で参加者を戻せます。",
                ephemeral=True,
            )
        else:
            try:
                deadline = parse_deadline(when, utcnow())
            except DeadlineError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            await self.bot.service.set_deadline(rec.id, deadline)
            await interaction.response.send_message(
                f"締切を {format_deadline(to_iso(deadline))} に設定しました。", ephemeral=True
            )
        await self.bot.updater.refresh(rec.id)

    @close_recruitment.autocomplete("recruitment_id")
    async def _close_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._recruitment_autocomplete(interaction, current)

    # ---------------- /引き継ぎ ----------------

    @app_commands.command(
        name="引き継ぎ", description="終了した募集の参加者を、受付中の募集に追加します（募集作成者のみ）"
    )
    @app_commands.guild_only()
    @app_commands.rename(source_id="元の募集", target_id="先の募集")
    @app_commands.describe(
        source_id="参加者を引き継ぐ、終了した募集",
        target_id="追加先の受付中の募集（このチャンネルに1件だけなら省略可）",
    )
    async def carry_over(
        self,
        interaction: discord.Interaction,
        source_id: int,
        target_id: int | None = None,
    ) -> None:
        target = await self._resolve_recruitment(interaction, target_id)
        if isinstance(target, str):
            await interaction.response.send_message(target, ephemeral=True)
            return
        if not _can_manage(interaction, target):
            await interaction.response.send_message(
                "引き継ぎができるのは、追加先の募集作成者か「メッセージの管理」権限を持つ人だけです。",
                ephemeral=True,
            )
            return
        source = await self.bot.service.get(source_id)
        if source is None or source.guild_id != interaction.guild_id:
            await interaction.response.send_message("引き継ぎ元の募集が見つかりません。", ephemeral=True)
            return
        if source.is_open:
            await interaction.response.send_message(
                "引き継ぎ元には、終了した募集を選んでください。", ephemeral=True
            )
            return

        result = await self.bot.service.carry_over(source.id, target.id)
        if result.outcome is not Outcome.JOINED:
            await interaction.response.send_message(
                "追加先の募集は終了しています。" if result.outcome is Outcome.NOT_OPEN
                else "募集が見つかりません。",
                ephemeral=True,
            )
            return

        guests = {
            p.user_id: f"{sanitize_name(p.guest_name)}{GUEST_LABEL}"
            for p in await self.bot.service.list_participants(source.id)
            if p.guest_name is not None
        }

        def labels(user_ids: list[int]) -> str:
            return " ".join(guests.get(u, f"<@{u}>") for u in user_ids)

        lines = [f"募集 #{source.id} から {len(result.added)}人を追加しました。"]
        if result.added:
            lines.append("追加: " + labels(result.added))
        if result.already_joined:
            lines.append(f"参加済みのためスキップ: {len(result.already_joined)}人")
        if result.full:
            lines.append("定員のため追加できなかった人: " + labels(result.full))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        if result.added:
            await self.bot.updater.refresh(target.id)

    @carry_over.autocomplete("source_id")
    async def _carry_source_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.channel_id is None:
            return []
        recs = await self.bot.service.list_ended_in_channel(interaction.channel_id)
        return _choices(recs, current, suffix="（終了）")

    @carry_over.autocomplete("target_id")
    async def _carry_target_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._recruitment_autocomplete(interaction, current)

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
