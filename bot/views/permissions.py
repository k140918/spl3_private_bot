"""募集を管理できる人の判定（コマンド・管理パネルで共通）。"""

from __future__ import annotations

import discord

from bot.models import Recruitment


def can_manage(interaction: discord.Interaction, rec: Recruitment) -> bool:
    """募集作成者か「メッセージの管理」権限を持つ人。"""
    return interaction.user.id == rec.creator_user_id or interaction.permissions.manage_messages
