"""募集メッセージ（Embed + ボタン）の組み立て。"""

from __future__ import annotations

from collections.abc import Mapping

import discord

from bot.models import Mode, Participant, ParticipantStatus, Recruitment
from bot.services.deadline import format_deadline
from bot.views.buttons import RecruitButton
from bot.views.member_names import UNKNOWN_NAME

_HOW_TO_JOIN = {
    Mode.BUTTON: "「参加する」ボタンで参加\n取り消し: 「参加取消」ボタン / `/取消`",
    Mode.MESSAGE: (
        "チャットで「ノ」「行く」などと発言すると参加（AI判定・✅が付きます）\n"
        "取り消し: 「やっぱ無理」などと発言 / `/取消`"
    ),
    Mode.BOTH: (
        "ボタン、またはチャットで「ノ」「行く」などと発言（AI判定・✅が付きます）\n"
        "取り消し: 「参加取消」ボタン / 発言 / `/取消`"
    ),
}

COLOR_OPEN = discord.Colour.from_rgb(25, 215, 25)
COLOR_CLOSED = discord.Colour.light_grey()


def _format_start(start_time: str) -> str:
    return start_time if start_time.endswith(("～", "〜", "~")) else f"{start_time}～"


def build_embed(
    rec: Recruitment, participants: list[Participant], names: Mapping[int, str]
) -> discord.Embed:
    """names: user_id -> エスケープ済みの表示名（MemberNameResolver.resolve の結果）。"""
    joined = [p for p in participants if p.status is ParticipantStatus.JOINED]
    waitlist = [p for p in participants if p.status is ParticipantStatus.WAITLIST]

    lines: list[str] = []
    if rec.start_time:
        lines.append(f"⏰ {_format_start(rec.start_time)}")
    if rec.deadline_at:
        lines.append(f"⏳ 締切 {format_deadline(rec.deadline_at)}")
    lines.append(f"👥 参加者 {len(joined)}/{rec.capacity}")
    # Embed 内の <@ID> は閲覧者のクライアント次第で ID のまま表示されるため、表示名を直接書く
    lines.extend(f"・{names.get(p.user_id, UNKNOWN_NAME)}" for p in joined)
    if waitlist:
        lines.append(f"\n📝 補欠 {len(waitlist)}")
        lines.extend(f"・{names.get(p.user_id, UNKNOWN_NAME)}" for p in waitlist)

    title = f"🎮 {rec.title}"
    if not rec.is_open:
        title += "【終了】"
    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        colour=COLOR_OPEN if rec.is_open else COLOR_CLOSED,
    )
    if rec.is_open:
        embed.add_field(name="参加方法", value=_HOW_TO_JOIN[rec.mode], inline=False)
    embed.set_footer(text=f"募集ID: {rec.id}")
    return embed


def build_view(rec: Recruitment) -> discord.ui.View | None:
    if not (rec.is_open and rec.mode.uses_buttons):
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(RecruitButton("join", rec.id))
    view.add_item(RecruitButton("leave", rec.id))
    return view
