"""参加者の表示名取得（Discord API はダミー）。"""

from __future__ import annotations

from types import SimpleNamespace

import discord

from bot.models import Mode, Participant, ParticipantStatus, Recruitment, RecruitStatus, Source
from bot.views.member_names import UNKNOWN_NAME, MemberNameResolver, sanitize_name
from bot.views.render import build_embed

GUILD = 1


def _http_error(cls: type[discord.HTTPException], status: int) -> discord.HTTPException:
    return cls(SimpleNamespace(status=status, reason="x"), {"code": 0, "message": "x"})


class FakeGuild:
    def __init__(self, members: dict[int, str], error: discord.HTTPException | None = None):
        self.members = members
        self.error = error
        self.fetch_calls: list[int] = []

    def get_member(self, user_id):
        return None  # メンバーキャッシュ無効と同じ

    async def fetch_member(self, user_id):
        self.fetch_calls.append(user_id)
        if self.error:
            raise self.error
        if user_id not in self.members:
            raise _http_error(discord.NotFound, 404)
        return SimpleNamespace(id=user_id, display_name=self.members[user_id])


class FakeClient:
    def __init__(self, guild: FakeGuild | None, users: dict[int, str] | None = None):
        self.guild = guild
        self.users = users or {}
        self.fetch_user_calls: list[int] = []

    def get_guild(self, guild_id):
        return self.guild

    async def fetch_user(self, user_id):
        self.fetch_user_calls.append(user_id)
        if user_id not in self.users:
            raise _http_error(discord.NotFound, 404)
        return SimpleNamespace(id=user_id, display_name=self.users[user_id])


def _resolver(client, now):
    return MemberNameResolver(client, ttl_sec=300, failure_ttl_sec=60, clock=lambda: now[0])


async def test_resolves_server_display_name():
    guild = FakeGuild({10: "まようチャン❕", 20: "もかさま"})
    names = await _resolver(FakeClient(guild), [0.0]).resolve(GUILD, [10, 20])
    assert names == {10: "まようチャン❕", 20: "もかさま"}


async def test_cache_avoids_repeated_api_calls_until_ttl():
    guild = FakeGuild({10: "A"})
    now = [0.0]
    r = _resolver(FakeClient(guild), now)
    await r.resolve(GUILD, [10])
    await r.resolve(GUILD, [10])
    assert guild.fetch_calls == [10]
    guild.members[10] = "A改名"
    now[0] = 301.0  # TTL 経過後は再取得して改名が反映される
    assert (await r.resolve(GUILD, [10]))[10] == "A改名"
    assert guild.fetch_calls == [10, 10]


async def test_remember_updates_name_without_api_call():
    guild = FakeGuild({10: "旧名"})
    r = _resolver(FakeClient(guild), [0.0])
    await r.resolve(GUILD, [10])
    r.remember(GUILD, SimpleNamespace(id=10, display_name="新名"))
    assert (await r.resolve(GUILD, [10]))[10] == "新名"
    assert guild.fetch_calls == [10]


async def test_member_not_found_falls_back_to_user_name():
    client = FakeClient(FakeGuild({}), users={10: "抜けた人"})
    assert (await _resolver(client, [0.0]).resolve(GUILD, [10]))[10] == "抜けた人"


async def test_user_not_found_shows_placeholder():
    names = await _resolver(FakeClient(FakeGuild({})), [0.0]).resolve(GUILD, [10])
    assert names[10] == UNKNOWN_NAME


async def test_forbidden_and_http_errors_do_not_raise_and_keep_previous_name():
    for cls, status in ((discord.Forbidden, 403), (discord.HTTPException, 500)):
        guild = FakeGuild({10: "前の名前"})
        now = [0.0]
        r = _resolver(FakeClient(guild), now)
        await r.resolve(GUILD, [10])
        guild.error = _http_error(cls, status)
        now[0] = 301.0
        assert (await r.resolve(GUILD, [10]))[10] == "前の名前"
        # 失敗後しばらくは再取得しない（レート制限対策）
        await r.resolve(GUILD, [10])
        assert len(guild.fetch_calls) == 2


async def test_error_without_previous_name_shows_placeholder():
    guild = FakeGuild({10: "A"}, error=_http_error(discord.HTTPException, 500))
    assert (await _resolver(FakeClient(guild), [0.0]).resolve(GUILD, [10]))[10] == UNKNOWN_NAME


async def test_guild_not_cached_uses_user_lookup():
    client = FakeClient(None, users={10: "グローバル名"})
    assert (await _resolver(client, [0.0]).resolve(GUILD, [10]))[10] == "グローバル名"


def test_sanitize_escapes_markdown_and_mentions():
    assert sanitize_name("**太字**") == r"\*\*太字\*\*"
    assert "@everyone" not in sanitize_name("@everyone")
    assert "<@123456789012345678>" not in sanitize_name("<@123456789012345678>")
    assert sanitize_name("改行\nあり") == "改行 あり"
    assert sanitize_name("   ") == UNKNOWN_NAME


def test_embed_shows_names_not_mentions():
    rec = Recruitment(1, GUILD, 2, 3, 10, "えんじょいプラベ🐼", Mode.BOTH, 10, "21:00",
                      RecruitStatus.OPEN, "2026-10-11T00:00:00+00:00")
    parts = [Participant(1, uid, ParticipantStatus.JOINED, Source.BUTTON, "t") for uid in (10, 20)]
    embed = build_embed(rec, parts, {10: "まようチャン❕"})
    assert "・まようチャン❕" in embed.description
    assert f"・{UNKNOWN_NAME}" in embed.description  # 名前が無い場合も ID を出さない
    assert "<@" not in embed.description
    assert "👥 参加者 2/10" in embed.description
