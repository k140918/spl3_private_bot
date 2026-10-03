"""環境変数から設定を読み込む。

秘密情報は repr / ログに出さない（field(repr=False)）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "intent_classifier.md"


class ConfigError(Exception):
    """設定値が不正。メッセージに値そのものは含めない。"""


@dataclass(frozen=True)
class Settings:
    discord_token: str = field(repr=False)
    gemini_api_key: str = field(repr=False)
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_thinking_level: str = ""
    gemini_timeout_sec: float = 10.0
    gemini_max_concurrency: int = 3
    ai_confidence_threshold: float = 0.85
    ai_context_messages: int = 5
    ai_context_max_age_sec: float = 600.0
    ai_max_message_chars: int = 200
    ai_user_cooldown_sec: float = 5.0
    recruit_ttl_hours: float = 6.0
    db_path: Path = PROJECT_ROOT / "data" / "recruit.db"
    prompt_path: Path = DEFAULT_PROMPT_PATH
    log_level: str = "INFO"
    dev_guild_id: int | None = None

    @property
    def message_mode_enabled(self) -> bool:
        """Gemini API Key があるときだけメッセージ方式を有効にする。"""
        return bool(self.gemini_api_key)

    def secrets(self) -> list[str]:
        return [s for s in (self.discord_token, self.gemini_api_key) if s]


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} は整数で指定してください") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} は {lo}〜{hi} の範囲で指定してください")
    return value


def _float(name: str, default: float, lo: float, hi: float) -> float:
    raw = _get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} は数値で指定してください") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} は {lo}〜{hi} の範囲で指定してください")
    return value


def load_settings() -> Settings:
    # .env があれば読む（本番では systemd の EnvironmentFile で渡す）。既存の環境変数は上書きしない。
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    token = _get("DISCORD_TOKEN")
    if not token:
        raise ConfigError("DISCORD_TOKEN が設定されていません")

    db_raw = _get("DB_PATH", "data/recruit.db")
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    thinking = _get("GEMINI_THINKING_LEVEL").lower()
    if thinking and thinking not in {"minimal", "low", "medium", "high"}:
        raise ConfigError("GEMINI_THINKING_LEVEL は minimal / low / medium / high のいずれかです")

    log_level = _get("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("LOG_LEVEL は DEBUG / INFO / WARNING / ERROR のいずれかです")

    dev_guild_raw = _get("DEV_GUILD_ID")
    try:
        dev_guild_id = int(dev_guild_raw) if dev_guild_raw else None
    except ValueError:
        raise ConfigError("DEV_GUILD_ID は数値IDで指定してください") from None

    return Settings(
        discord_token=token,
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        gemini_thinking_level=thinking,
        gemini_timeout_sec=_float("GEMINI_TIMEOUT_SEC", 10.0, 1.0, 60.0),
        gemini_max_concurrency=_int("GEMINI_MAX_CONCURRENCY", 3, 1, 20),
        ai_confidence_threshold=_float("AI_CONFIDENCE_THRESHOLD", 0.85, 0.0, 1.0),
        ai_context_messages=_int("AI_CONTEXT_MESSAGES", 5, 0, 20),
        ai_context_max_age_sec=_float("AI_CONTEXT_MAX_AGE_SEC", 600.0, 0.0, 86400.0),
        ai_max_message_chars=_int("AI_MAX_MESSAGE_CHARS", 200, 10, 2000),
        ai_user_cooldown_sec=_float("AI_USER_COOLDOWN_SEC", 5.0, 0.0, 600.0),
        recruit_ttl_hours=_float("RECRUIT_TTL_HOURS", 6.0, 0.5, 168.0),
        db_path=db_path,
        log_level=log_level,
        dev_guild_id=dev_guild_id,
    )
