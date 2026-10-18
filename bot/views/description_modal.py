"""/募集文 で開く入力フォーム。改行を含む募集文を入力・修正できる。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import discord

from bot.models import Outcome
from bot.services.recruitment_service import DESCRIPTION_MAX

if TYPE_CHECKING:
    from bot.main import RecruitBot

log = logging.getLogger(__name__)


class DescriptionModal(discord.ui.Modal, title="募集文を入力"):
    def __init__(self, recruitment_id: int, current: str | None) -> None:
        super().__init__(timeout=600)
        self.recruitment_id = recruitment_id
        self.text: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="募集文（空にして送信すると削除）",
            style=discord.TextStyle.paragraph,
            default=current or "",
            placeholder="例）ルール：エリア / ヤグラ\n初心者歓迎、VCなしOK",
            required=False,
            max_length=DESCRIPTION_MAX,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = cast("RecruitBot", interaction.client)
        outcome = await bot.service.set_description(self.recruitment_id, self.text.value)
        replies = {
            Outcome.JOINED: "募集文を更新しました。",
            Outcome.NOT_OPEN: "この募集は終了しています。",
            Outcome.NOT_FOUND: "募集が見つかりません。",
        }
        await interaction.response.send_message(replies[outcome], ephemeral=True)
        if outcome is Outcome.JOINED:
            log.info("description updated recruitment=%s by=%s", self.recruitment_id, interaction.user.id)
            await bot.updater.refresh(self.recruitment_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("description modal error type=%s", type(error).__name__, exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("エラーが発生しました。", ephemeral=True)
            else:
                await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
        except discord.HTTPException:
            pass
