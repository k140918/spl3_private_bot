"""募集メッセージ（Embed + ボタン）の組み立て。"""

from __future__ import annotations

from collections.abc import Mapping

import discord

from bot.models import Mode, Participant, ParticipantStatus, Recruitment
from bot.services.deadline import format_deadline
from bot.views.buttons import RecruitButton
from bot.views.member_names import UNKNOWN_NAME, sanitize_name

# スマホで縦に長くならないよう短く。ボタン方式はボタンを見れば分かるので載せない
_HOW_TO_JOIN = {
    Mode.MESSAGE: "「ノ」「行く」などと発言で参加（✅が付きます）\n取り消しは「やっぱ無理」などと発言",
    Mode.BOTH: "ボタン、または「ノ」「行く」などと発言で参加（✅が付きます）",
}

# サーバーにいない人（名前だけで登録）の名前の後ろに付ける表示
GUEST_LABEL = "（新規）"

COLOR_OPEN = discord.Colour.from_rgb(25, 215, 25)
COLOR_CLOSED = discord.Colour.light_grey()


def _format_start(start_time: str) -> str:
    return start_time if start_time.endswith(("～", "〜", "~")) else f"{start_time}～"


def _display_name(p: Participant, names: Mapping[int, str]) -> str:
    if p.guest_name is not None:  # サーバーにいない人（名前だけで登録）
        return f"{sanitize_name(p.guest_name)}{GUEST_LABEL}"
    return names.get(p.user_id, UNKNOWN_NAME)


def build_embed(
    rec: Recruitment, participants: list[Participant], names: Mapping[int, str]
) -> discord.Embed:
    """names: user_id -> エスケープ済みの表示名（MemberNameResolver.resolve の結果）。"""
    joined = [p for p in participants if p.status is ParticipantStatus.JOINED]
    waitlist = [p for p in participants if p.status is ParticipantStatus.WAITLIST]

    lines: list[str] = []
    if rec.description:  # 募集文（Embed 内なので @everyone 等を書いても通知されない）
        lines.extend([rec.description, ""])
    if rec.start_time:  # 未定のときは行ごと省く
        lines.append(f"⏰ 開始予定：{_format_start(rec.start_time)}")
    if rec.deadline_at:
        lines.append(f"⏳ 受付締切：{format_deadline(rec.deadline_at)}")
    lines.append(f"👥 参加者 {len(joined)}/{rec.capacity}")
    # Embed 内の <@ID> は閲覧者のクライアント次第で ID のまま表示されるため、表示名を直接書く
    lines.extend(f"・{_display_name(p, names)}" for p in joined)
    if waitlist:
        lines.append(f"\n📝 補欠 {len(waitlist)}")
        lines.extend(f"・{_display_name(p, names)}" for p in waitlist)

    title = f"🎮 {rec.title}"
    if not rec.is_open:
        title += "【終了】"
    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        colour=COLOR_OPEN if rec.is_open else COLOR_CLOSED,
    )
    if rec.is_open and rec.mode in _HOW_TO_JOIN:
        embed.add_field(name="参加方法", value=_HOW_TO_JOIN[rec.mode], inline=False)
    embed.set_footer(text=f"募集ID: {rec.id} ｜ 使い方：/プラベ ヘルプ")
    return embed


def build_view(rec: Recruitment) -> discord.ui.View | None:
    """受付中は「⚙️ 管理」を常に付ける。参加/取消ボタンはボタン方式・両方のときだけ。"""
    if not rec.is_open:
        return None
    view = discord.ui.View(timeout=None)
    if rec.mode.uses_buttons:
        view.add_item(RecruitButton("join", rec.id))
        view.add_item(RecruitButton("leave", rec.id))
    view.add_item(RecruitButton("manage", rec.id))
    return view
