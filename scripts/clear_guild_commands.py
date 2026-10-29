"""サーバー専用に登録されたスラッシュコマンド（ギルドコマンド）を削除する。

開発中に DEV_GUILD_ID で登録した古いコマンドは、本番のグローバル登録とは別物なので
上書きされずに残り続ける。それを消すためのスクリプト。

Gateway には接続しない（HTTP API のみ）ので、VPS で Bot が動いていても二重起動にはならない。

使い方:
    python -m scripts.clear_guild_commands <サーバーID> [<サーバーID> ...]
"""

from __future__ import annotations

import asyncio
import sys

import discord

from bot.config import ConfigError, load_settings


async def clear(guild_ids: list[int]) -> int:
    settings = load_settings()
    client = discord.Client(intents=discord.Intents.none())
    async with client:
        await client.login(settings.discord_token)
        app = await client.application_info()
        failed = 0
        for guild_id in guild_ids:
            try:
                current = await client.http.get_guild_commands(app.id, guild_id)
                names = ", ".join(f"/{c['name']}" for c in current)
                if not current:
                    print(f"{guild_id}: サーバー専用のコマンドはありません")
                    continue
                await client.http.bulk_upsert_guild_commands(app.id, guild_id, [])
                print(f"{guild_id}: 削除しました（{names}）")
            except discord.HTTPException as e:
                failed += 1
                print(f"{guild_id}: 失敗しました（status={e.status} code={e.code}）")
    return 1 if failed else 0


def main() -> int:
    try:
        guild_ids = [int(arg) for arg in sys.argv[1:]]
    except ValueError:
        guild_ids = []
    if not guild_ids:
        print("使い方: python -m scripts.clear_guild_commands <サーバーID> [<サーバーID> ...]")
        return 2
    try:
        return asyncio.run(clear(guild_ids))
    except ConfigError as e:
        print(f"設定エラー: {e}")
        return 2
    except discord.LoginFailure:
        print("ログインに失敗しました。DISCORD_TOKEN を確認してください")
        return 1


if __name__ == "__main__":
    sys.exit(main())
