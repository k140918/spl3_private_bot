"""締切時刻（絶対時刻）の解析と表示。

日本時間（UTC+9、サマータイムなし）で解釈する。固定オフセットなので tzdata 不要。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")
MAX_AHEAD = timedelta(days=7)
_WEEKDAYS = "月火水木金土日"

# 「22:30」「22時」「22時30分」「10/12 22:30」「2026/10/12 22:30」「2026-10-12 22:30」
_PATTERN = re.compile(
    r"^(?:(?:(?P<year>\d{4})[/-])?(?P<month>\d{1,2})[/-](?P<day>\d{1,2})\s*)?"
    r"(?P<hour>\d{1,2})(?:[:：時]\s*(?P<minute>\d{1,2})?\s*分?)?$"
)


class DeadlineError(ValueError):
    """ユーザーに見せるメッセージを持つ。"""


def parse_deadline(text: str, now: datetime) -> datetime:
    """入力を UTC の datetime にする。

    - 時刻だけ: 今日のその時刻。既に過ぎていれば翌日（深夜の「0:30」などを想定）
    - 日付あり: その日時。年を省略して過去になる場合は翌年（年末年始）
    - 過去・7日より先は DeadlineError
    """
    normalized = unicodedata.normalize("NFKC", text).strip()
    m = _PATTERN.match(normalized)
    if not m:
        raise DeadlineError("締切は「22:30」や「10/12 22:30」の形式で入力してください。")

    hour, minute = int(m["hour"]), int(m["minute"] or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise DeadlineError("時刻は 0:00〜23:59 の範囲で入力してください。")

    now_jst = now.astimezone(JST)
    try:
        if m["month"]:
            year = int(m["year"]) if m["year"] else now_jst.year
            dt = datetime(year, int(m["month"]), int(m["day"]), hour, minute, tzinfo=JST)
            if not m["year"] and dt <= now_jst:
                dt = dt.replace(year=year + 1)
        else:
            dt = now_jst.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now_jst:
                dt += timedelta(days=1)
    except ValueError:
        raise DeadlineError("存在しない日付です。") from None

    if dt <= now_jst:
        raise DeadlineError("締切に過去の日時は指定できません。")
    if dt - now_jst > MAX_AHEAD:
        raise DeadlineError("締切は7日以内で指定してください。")
    return dt.astimezone(timezone.utc)


def format_deadline(deadline_iso: str) -> str:
    """表示用: 「10/12(日) 22:30」"""
    dt = datetime.fromisoformat(deadline_iso).astimezone(JST)
    return f"{dt.month}/{dt.day}({_WEEKDAYS[dt.weekday()]}) {dt:%H:%M}"
