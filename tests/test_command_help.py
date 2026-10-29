from types import SimpleNamespace

from bot.cogs.recruit_commands import RecruitCommands


def make_cog(ttl=6):
    return RecruitCommands(SimpleNamespace(settings=SimpleNamespace(recruit_ttl_hours=ttl)))


def test_commands_are_grouped_and_autocomplete_preserved():
    cog = make_cog()
    group = cog.__cog_app_commands_group__
    assert group.name == "プラベ"
    assert group.guild_only
    assert {c.name for c in group.commands} == {
        "ヘルプ", "募集", "参加取消", "追加", "一括追加", "募集文", "締切", "引き継ぎ",
    }
    for command in group.commands:
        assert command.qualified_name == f"プラベ {command.name}"
        assert command.binding is cog
    for name in ("参加取消", "追加", "一括追加", "募集文", "締切"):
        params = {p.name: p for p in group.get_command(name).parameters}
        assert params["recruitment_id"].autocomplete
    assert not group.get_command("募集").get_parameter("deadline").required


def test_help_lists_actual_commands_and_configured_ttl():
    cog = make_cog(ttl=12)
    embed = cog.build_help()
    fields = {f.name: f.value for f in embed.fields}
    for command in cog.__cog_app_commands_group__.commands:
        assert fields[f"/{command.qualified_name}"] == command.description
    assert "12 時間" in fields["時刻の意味"]
    assert "メッセージの管理" in fields["操作できる人"]
    assert "1募集につき1回" in fields["一括追加と通知"]
    assert len(embed) < 6000
