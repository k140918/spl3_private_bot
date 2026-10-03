"""logging 設定。秘密情報はフォーマット後の文字列から必ず伏せ字にする。"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable

# Discord Bot Token の形式（base64.base64.base64）をパターンでも伏せる
_DISCORD_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}")
# Google API Key の形式
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{35}")


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: Iterable[str]) -> None:
        super().__init__(fmt)
        self._secrets = [s for s in secrets if s]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)  # traceback も含めた最終文字列を対象にする
        for secret in self._secrets:
            text = text.replace(secret, "***")
        text = _DISCORD_TOKEN_RE.sub("***", text)
        return _GOOGLE_KEY_RE.sub("***", text)


def setup_logging(level: str, secrets: Iterable[str]) -> None:
    handler = logging.StreamHandler(sys.stdout)  # systemd では journald に入る
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", secrets)
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # ライブラリのログは控えめに
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)
