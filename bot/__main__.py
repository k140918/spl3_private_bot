"""起動: python -m bot"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import discord

from bot.config import ConfigError, Settings, load_settings
from bot.logging_setup import setup_logging
from bot.main import RecruitBot

log = logging.getLogger("bot")


async def _run(settings: Settings) -> None:
    bot = RecruitBot(settings)
    if sys.platform != "win32":
        # systemctl stop / restart (SIGTERM) で DB を閉じてから終了する
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(bot.close()))
    async with bot:
        await bot.start(settings.discord_token)


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 2

    setup_logging(settings.log_level, settings.secrets())
    log.info(
        "starting bot message_mode=%s model=%s db=%s",
        settings.message_mode_enabled, settings.gemini_model, settings.db_path,
    )
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        pass
    except discord.LoginFailure:
        log.error("Discord login failed: DISCORD_TOKEN を確認してください")
        return 1
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Message Content Intent が無効です: Developer Portal > Bot で有効化するか、"
            "GEMINI_API_KEY を空にしてメッセージ方式を無効にしてください"
        )
        return 1
    log.info("bot stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
