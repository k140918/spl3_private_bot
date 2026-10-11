"""参加者のサーバー内表示名を取得する。

Embed 内の <@ID> は閲覧者の Discord クライアントがそのユーザーをキャッシュしている
場合しか名前に変換されないため、Bot 側で表示名を取得して文字列として埋め込む。
Members Intent・メンバーキャッシュなしで動くよう、guild.fetch_member()（REST）を使い、
結果は TTL 付きでキャッシュして API 呼び出しを抑える。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

import discord

log = logging.getLogger(__name__)

UNKNOWN_NAME = "（取得できないユーザー）"
_MAX_NAME_LENGTH = 32


def sanitize_name(name: str) -> str:
    """Markdown・メンション構文・改行を無害化する。"""
    name = " ".join(name.split())[:_MAX_NAME_LENGTH] or UNKNOWN_NAME
    name = discord.utils.escape_markdown(name)
    return discord.utils.escape_mentions(name)


class MemberNameResolver:
    def __init__(
        self,
        client: discord.Client,
        ttl_sec: float = 300.0,
        failure_ttl_sec: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl = ttl_sec
        self._failure_ttl = failure_ttl_sec
        self._clock = clock
        # (guild_id, user_id) -> (表示名, 有効期限)
        self._cache: dict[tuple[int, int], tuple[str, float]] = {}

    def remember(self, guild_id: int | None, user: discord.abc.User) -> None:
        """イベントに含まれる Member から、API を呼ばずに最新の表示名を記録する。"""
        if guild_id is None:
            return
        self._store(guild_id, user.id, user.display_name, self._ttl)

    async def resolve(self, guild_id: int, user_ids: Iterable[int]) -> dict[int, str]:
        """user_id -> 表示用の名前（エスケープ済み）。失敗しても例外は投げない。"""
        guild = self._client.get_guild(guild_id)
        names: dict[int, str] = {}
        for user_id in user_ids:
            names[user_id] = sanitize_name(await self._resolve_one(guild_id, guild, user_id))
        return names

    async def _resolve_one(
        self, guild_id: int, guild: discord.Guild | None, user_id: int
    ) -> str:
        key = (guild_id, user_id)
        cached = self._cache.get(key)
        if cached and cached[1] > self._clock():
            return cached[0]
        stale = cached[0] if cached else None

        if guild is not None:
            member = guild.get_member(user_id)  # キャッシュ無効時は通常 None
            if member is not None:
                return self._store(guild_id, user_id, member.display_name, self._ttl)
            try:
                member = await guild.fetch_member(user_id)
                return self._store(guild_id, user_id, member.display_name, self._ttl)
            except discord.NotFound:
                pass  # サーバーを抜けた等。下でユーザー名を試す
            except discord.Forbidden as e:
                log.warning("fetch_member forbidden guild=%s user=%s code=%s", guild_id, user_id, e.code)
                return self._fallback(key, stale)
            except discord.HTTPException as e:
                log.warning(
                    "fetch_member failed guild=%s user=%s status=%s code=%s",
                    guild_id, user_id, e.status, e.code,
                )
                return self._fallback(key, stale)

        try:
            user = await self._client.fetch_user(user_id)
            return self._store(guild_id, user_id, user.display_name, self._ttl)
        except discord.HTTPException as e:  # NotFound / Forbidden も含む
            log.warning("fetch_user failed user=%s status=%s code=%s", user_id, e.status, e.code)
            return self._fallback(key, stale)

    def _fallback(self, key: tuple[int, int], stale: str | None) -> str:
        """取得失敗時: 以前の名前があればそれを使い、短時間は再取得しない。"""
        return self._store(key[0], key[1], stale or UNKNOWN_NAME, self._failure_ttl)

    def _store(self, guild_id: int, user_id: int, name: str, ttl: float) -> str:
        self._cache[(guild_id, user_id)] = (name, self._clock() + ttl)
        if len(self._cache) > 2000:  # 期限切れを掃除してメモリを抑える
            now = self._clock()
            self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
        return name
