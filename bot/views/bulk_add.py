"""募集管理者がサーバーメンバーをまとめて選択する画面。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.models import Outcome

if TYPE_CHECKING:
    from bot.main import RecruitBot


class BulkAddView(discord.ui.View):
    def __init__(self, bot: RecruitBot, recruitment_id: int, operator_id: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.recruitment_id = recruitment_id
        self.operator_id = operator_id

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="追加するメンバーをまとめて選択（最大10人）",
        min_values=1,
        max_values=10,
    )
    async def select_members(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        rec = await self.bot.service.get(self.recruitment_id)
        if (
            interaction.user.id != self.operator_id
            or rec is None
            or rec.guild_id != interaction.guild_id
            or not (interaction.user.id == rec.creator_user_id or interaction.permissions.manage_messages)
        ):
            await interaction.response.send_message("この募集に追加する権限がありません。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        members = [u for u in select.values if isinstance(u, discord.Member) and not u.bot]
        results = await self.bot.service.join_many(rec.id, [u.id for u in members])
        labels = {
            Outcome.JOINED: "追加しました",
            Outcome.ALREADY_JOINED: "参加済みのためスキップ",
            Outcome.FULL: "定員のため追加できませんでした",
            Outcome.NOT_OPEN: "受付終了のため追加できませんでした",
            Outcome.NOT_FOUND: "募集が見つかりません",
        }
        lines = []
        for outcome, label in labels.items():
            ids = [uid for uid, result in results.items() if result is outcome]
            if ids:
                lines.append(f"{label}：" + "、".join(f"<@{uid}>" for uid in ids))
        if len(members) != len(select.values):
            lines.append("Bot・サーバー外のユーザーは追加できません。")
        await interaction.edit_original_response(content="\n".join(lines), view=None)
        self.stop()
        if Outcome.JOINED in results.values():
            for member in members:
                self.bot.member_names.remember(interaction.guild_id, member)
            await self.bot.updater.refresh(rec.id)
