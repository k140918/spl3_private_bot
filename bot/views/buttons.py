"""参加/取消/管理ボタン。

DynamicItem を使い custom_id（recruit:join:<募集ID>）から復元するので、
Bot を再起動しても既存の募集メッセージのボタンがそのまま動く。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, cast

import discord

from bot.models import Outcome, Source

if TYPE_CHECKING:
    from bot.main import RecruitBot

log = logging.getLogger(__name__)

BUTTON_REPLIES: dict[Outcome, str] = {
    Outcome.JOINED: "✅ 参加登録しました！",
    Outcome.LEFT: "👋 参加を取り消しました。",
    Outcome.ALREADY_JOINED: "既に参加しています。",
    Outcome.NOT_JOINED: "この募集には参加していません。",
    Outcome.FULL: "🈵 満員のため参加できません。",
    Outcome.NOT_OPEN: "この募集は終了しています。",
    Outcome.NOT_FOUND: "募集が見つかりません。",
}


class RecruitButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"recruit:(?P<action>join|leave|manage):(?P<rid>[0-9]+)",
):
    def __init__(self, action: str, recruitment_id: int) -> None:
        self.action = action
        self.recruitment_id = recruitment_id
        if action == "join":
            button: discord.ui.Button[Any] = discord.ui.Button(
                label="参加する", style=discord.ButtonStyle.success, emoji="🙋",
                custom_id=f"recruit:join:{recruitment_id}",
            )
        elif action == "leave":
            button = discord.ui.Button(
                label="参加取消", style=discord.ButtonStyle.secondary,
                custom_id=f"recruit:leave:{recruitment_id}",
            )
        else:  # 募集管理者用の操作パネルを開く（スマホでコマンドを打たずに済むように）
            button = discord.ui.Button(
                label="管理", style=discord.ButtonStyle.secondary, emoji="⚙️",
                custom_id=f"recruit:manage:{recruitment_id}",
            )
        super().__init__(button)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
    ) -> RecruitButton:
        return cls(match["action"], int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("RecruitBot", interaction.client)
        if self.action == "manage":
            from bot.views.manage_panel import open_panel  # render → buttons の循環 import を避ける

            await open_panel(bot, interaction, self.recruitment_id)
            return
        bot.member_names.remember(interaction.guild_id, interaction.user)
        if self.action == "join":
            outcome = await bot.service.join(self.recruitment_id, interaction.user.id, Source.BUTTON)
        else:
            outcome = await bot.service.leave(self.recruitment_id, interaction.user.id)
        await interaction.response.send_message(BUTTON_REPLIES[outcome], ephemeral=True)
        if outcome.changed:
            await bot.updater.refresh(self.recruitment_id)
