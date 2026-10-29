"""募集メッセージの「⚙️ 管理」ボタンから開く、募集管理者用の操作パネル。

スマホでコマンドを打たなくても、タップだけで追加・取消・募集文・締切・引き継ぎができる。
ボタンが押された募集を対象にするので、募集を選ぶ手間もない。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from bot.models import Outcome, Recruitment, RecruitStatus
from bot.services.deadline import DeadlineError, format_deadline, parse_deadline
from bot.services.recruitment_service import GUEST_NAME_MAX, normalize_guest_name, to_iso, utcnow
from bot.views.bulk_add import BulkAddView
from bot.views.description_modal import DescriptionModal
from bot.views.member_names import sanitize_name
from bot.views.permissions import can_manage
from bot.views.render import GUEST_LABEL

if TYPE_CHECKING:
    from bot.main import RecruitBot

log = logging.getLogger(__name__)

PANEL_TEXT = "⚙️ 募集の管理（あなたにだけ表示されています）"


async def load_manageable(
    bot: RecruitBot, interaction: discord.Interaction, recruitment_id: int
) -> Recruitment | None:
    """受付中で、操作する人に権限があれば募集を返す。なければ本人にだけ理由を返信する。"""
    rec = await bot.service.get(recruitment_id)
    if rec is None or rec.guild_id != interaction.guild_id:
        message = "募集が見つかりません。"
    elif not rec.is_open:
        message = "この募集は終了しています。"
    elif not can_manage(interaction, rec):
        message = "管理メニューは募集作成者か「メッセージの管理」権限を持つ人だけが使えます。"
    else:
        return rec
    await interaction.response.send_message(message, ephemeral=True)
    return None


def _plain(name: str) -> str:
    """選択肢のラベル用。Embed 向けのエスケープ（\\ とゼロ幅スペース）を外す。"""
    return name.replace("\\", "").replace("​", "")


async def open_panel(bot: RecruitBot, interaction: discord.Interaction, recruitment_id: int) -> None:
    rec = await load_manageable(bot, interaction, recruitment_id)
    if rec is None:
        return
    await interaction.response.send_message(
        PANEL_TEXT, view=ManagePanel(bot, rec.id), ephemeral=True
    )


class ManagePanel(discord.ui.View):
    def __init__(self, bot: RecruitBot, recruitment_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.recruitment_id = recruitment_id

    @discord.ui.button(label="メンバー追加", emoji="👤", style=discord.ButtonStyle.primary, row=0)
    async def add_members(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is None:
            return
        await interaction.response.edit_message(
            content="追加するメンバーを選んでください（最大10人・3分以内）。\n"
            "参加済みの人はスキップし、空き枠まで追加します。",
            view=BulkAddView(self.bot, rec.id, interaction.user.id),
        )

    @discord.ui.button(label="新規追加", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def add_guest(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is not None:
            await interaction.response.send_modal(GuestModal(self.bot, rec.id))

    @discord.ui.button(label="参加者を外す", emoji="➖", style=discord.ButtonStyle.secondary, row=0)
    async def remove(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is None:
            return
        participants = await self.bot.service.list_participants(rec.id)
        if not participants:
            await interaction.response.send_message("参加者がいません。", ephemeral=True)
            return
        names = await self.bot.member_names.resolve(
            rec.guild_id, [p.user_id for p in participants if not p.is_guest]
        )
        options = []
        for p in participants:
            if p.guest_name is not None:
                label = p.guest_name + GUEST_LABEL
            else:
                label = _plain(names.get(p.user_id, str(p.user_id)))
                if p.user_id == rec.creator_user_id:
                    label += "（募集者）"
            options.append(discord.SelectOption(label=label[:100], value=str(p.user_id)))
        await interaction.response.edit_message(
            content="外す参加者を選んでください（複数選択できます）。",
            view=RemoveView(self.bot, rec.id, options),
        )

    @discord.ui.button(label="募集文", emoji="📝", style=discord.ButtonStyle.secondary, row=1)
    async def description(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is not None:
            await interaction.response.send_modal(DescriptionModal(rec.id, rec.description))

    @discord.ui.button(label="締切時刻", emoji="⏳", style=discord.ButtonStyle.secondary, row=1)
    async def deadline(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is not None:
            await interaction.response.send_modal(DeadlineModal(self.bot, rec.id))

    @discord.ui.button(label="今すぐ締切", emoji="🛑", style=discord.ButtonStyle.danger, row=1)
    async def close_now(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is None:
            return
        await interaction.response.edit_message(
            content="参加受付を今すぐ終了しますか？", view=ConfirmCloseView(self.bot, rec.id)
        )

    @discord.ui.button(label="引き継ぎ", emoji="🔁", style=discord.ButtonStyle.secondary, row=2)
    async def carry_over(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is None:
            return
        ended = await self.bot.service.list_ended_in_channel(rec.channel_id)
        if not ended:
            await interaction.response.send_message(
                "このチャンネルに、引き継げる終了済みの募集がありません（最近7日間）。", ephemeral=True
            )
            return
        options = [
            discord.SelectOption(
                label=(f"#{r.id} {r.title}" + (f" {r.start_time}" if r.start_time else ""))[:100],
                value=str(r.id),
            )
            for r in ended[:25]
        ]
        await interaction.response.edit_message(
            content="参加者を引き継ぐ、終了した募集を選んでください。",
            view=CarryOverView(self.bot, rec.id, options),
        )


class RemoveView(discord.ui.View):
    def __init__(
        self, bot: RecruitBot, recruitment_id: int, options: list[discord.SelectOption]
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.recruitment_id = recruitment_id
        select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="外す参加者を選択", options=options, min_values=1, max_values=len(options)
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is None:
            return
        removed = 0
        for value in self.select.values:  # ゲスト（負数 ID）も同じ leave で外せる
            if await self.bot.service.leave(rec.id, int(value)) is Outcome.LEFT:
                removed += 1
        await interaction.response.edit_message(
            content=f"{removed}人を参加者から外しました。", view=None
        )
        self.stop()
        if removed:
            log.info("panel removal recruitment=%s count=%d by=%s", rec.id, removed, interaction.user.id)
            await self.bot.updater.refresh(rec.id)


class CarryOverView(discord.ui.View):
    def __init__(
        self, bot: RecruitBot, recruitment_id: int, options: list[discord.SelectOption]
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.recruitment_id = recruitment_id
        select: discord.ui.Select[Any] = discord.ui.Select(placeholder="元の募集を選択", options=options)
        select.callback = self._on_select  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is None:
            return
        source_id = int(self.select.values[0])
        result = await self.bot.service.carry_over(source_id, rec.id)
        if result.outcome is not Outcome.JOINED:
            content = "引き継げませんでした（募集が終了しているか、見つかりません）。"
        else:
            guests = {
                p.user_id: sanitize_name(p.guest_name) + GUEST_LABEL
                for p in await self.bot.service.list_participants(source_id)
                if p.guest_name is not None
            }

            def labels(user_ids: list[int]) -> str:
                return " ".join(guests.get(u, f"<@{u}>") for u in user_ids)

            lines = [f"募集 #{source_id} から {len(result.added)}人を追加しました。"]
            if result.added:
                lines.append("追加: " + labels(result.added))
            if result.already_joined:
                lines.append(f"参加済みのためスキップ: {len(result.already_joined)}人")
            if result.full:
                lines.append("定員のため追加できなかった人: " + labels(result.full))
            content = "\n".join(lines)
        await interaction.response.edit_message(content=content, view=None)
        self.stop()
        if result.added:
            await self.bot.updater.refresh(rec.id)


class ConfirmCloseView(discord.ui.View):
    def __init__(self, bot: RecruitBot, recruitment_id: int) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.recruitment_id = recruitment_id

    @discord.ui.button(label="締め切る", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        rec = await load_manageable(self.bot, interaction, self.recruitment_id)
        if rec is None:
            return
        await self.bot.service.set_status(rec.id, RecruitStatus.CLOSED)
        if self.bot.context_buffer:
            self.bot.context_buffer.clear(rec.channel_id)
        log.info("recruitment closed from panel id=%s by=%s", rec.id, interaction.user.id)
        await interaction.response.edit_message(
            content="参加受付を終了しました。\n"
            "誤って締め切った場合は、新しく募集を作り、その募集の「⚙️ 管理」→「引き継ぎ」で参加者を戻せます。",
            view=None,
        )
        self.stop()
        await self.bot.updater.refresh(rec.id)

    @discord.ui.button(label="やめる", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button[Any]) -> None:
        await interaction.response.edit_message(
            content=PANEL_TEXT, view=ManagePanel(self.bot, self.recruitment_id)
        )
        self.stop()


class GuestModal(discord.ui.Modal, title="新規の参加者を追加"):
    def __init__(self, bot: RecruitBot, recruitment_id: int) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.recruitment_id = recruitment_id
        self.guest_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="名前（サーバーにいない人）", max_length=GUEST_NAME_MAX, placeholder="例）フォロワーA"
        )
        self.add_item(self.guest_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = normalize_guest_name(self.guest_input.value)
        if not name:
            await interaction.response.send_message("名前を入力してください。", ephemeral=True)
            return
        label = sanitize_name(name) + GUEST_LABEL
        outcome = await self.bot.service.add_guest(self.recruitment_id, name)
        replies = {
            Outcome.JOINED: f"{label}を参加者に追加しました。",
            Outcome.ALREADY_JOINED: f"{label}はすでに参加しています。",
            Outcome.FULL: "満員のため追加できません。",
            Outcome.NOT_OPEN: "この募集は終了しています。",
            Outcome.NOT_FOUND: "募集が見つかりません。",
        }
        await interaction.response.send_message(replies[outcome], ephemeral=True)
        if outcome is Outcome.JOINED:
            log.info("panel guest add recruitment=%s by=%s", self.recruitment_id, interaction.user.id)
            await self.bot.updater.refresh(self.recruitment_id)


class DeadlineModal(discord.ui.Modal, title="締切時刻を設定"):
    def __init__(self, bot: RecruitBot, recruitment_id: int) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.recruitment_id = recruitment_id
        self.when: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="参加受付の終了時刻", max_length=20, placeholder="例）22:30 / 10/12 22:30"
        )
        self.add_item(self.when)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        rec = await self.bot.service.get(self.recruitment_id)
        if rec is None or not rec.is_open:
            await interaction.response.send_message("この募集は終了しています。", ephemeral=True)
            return
        try:
            deadline = parse_deadline(self.when.value, utcnow())
        except DeadlineError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await self.bot.service.set_deadline(rec.id, deadline)
        await interaction.response.send_message(
            f"締切を {format_deadline(to_iso(deadline))} に設定しました。", ephemeral=True
        )
        await self.bot.updater.refresh(rec.id)
