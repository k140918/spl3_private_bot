"""募集メッセージの「⚙️ 管理」ボタンと管理パネル（Discord API はダミー）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.models import Mode, Outcome, RecruitStatus, Source
from bot.views.buttons import RecruitButton
from bot.views.bulk_add import BulkAddView
from bot.views.description_modal import DescriptionModal
from bot.views.manage_panel import (
    CarryOverView,
    ConfirmCloseView,
    DeadlineModal,
    GuestModal,
    ManagePanel,
    RemoveView,
    load_manageable,
)
from bot.views.render import build_embed, build_view
from tests.conftest import CREATOR, GUILD, make_recruitment


def _interaction(user_id: int = CREATOR, manage_messages: bool = False):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=GUILD,
        permissions=SimpleNamespace(manage_messages=manage_messages),
        response=SimpleNamespace(
            send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock()
        ),
    )


def _bot(service, names: dict[int, str] | None = None):
    return SimpleNamespace(
        service=service,
        updater=SimpleNamespace(refresh=AsyncMock()),
        member_names=SimpleNamespace(resolve=AsyncMock(return_value=names or {})),
        context_buffer=None,
    )


def _sent_text(interaction) -> str:
    call = interaction.response.send_message.await_args
    return call.args[0] if call.args else call.kwargs.get("content", "")


# ---- 募集メッセージのボタン ----


async def test_view_has_manage_button_for_every_mode(service):
    button = await make_recruitment(service, mode=Mode.BUTTON)
    ids = [item.custom_id for item in build_view(button).children]
    assert ids == [f"recruit:join:{button.id}", f"recruit:leave:{button.id}", f"recruit:manage:{button.id}"]

    message = await make_recruitment(service, mode=Mode.MESSAGE, channel_id=999)
    assert [item.custom_id for item in build_view(message).children] == [f"recruit:manage:{message.id}"]

    await service.set_status(button.id, RecruitStatus.CLOSED)
    assert build_view(await service.get(button.id)) is None


def test_manage_button_restores_from_custom_id():
    match = RecruitButton.__discord_ui_compiled_template__.fullmatch("recruit:manage:42")
    assert match and match["action"] == "manage" and match["rid"] == "42"


async def test_button_mode_embed_has_no_how_to_field(service):
    rec = await make_recruitment(service, mode=Mode.BUTTON)
    assert build_embed(rec, [], {}).fields == []
    msg = await make_recruitment(service, mode=Mode.MESSAGE, channel_id=999)
    assert [f.name for f in build_embed(msg, [], {}).fields] == ["参加方法"]


# ---- 権限 ----


async def test_only_creator_or_manager_can_open(service):
    rec = await make_recruitment(service)
    bot = _bot(service)

    stranger = _interaction(user_id=555)
    assert await load_manageable(bot, stranger, rec.id) is None
    assert "募集作成者" in _sent_text(stranger)

    assert (await load_manageable(bot, _interaction(), rec.id)).id == rec.id
    assert (await load_manageable(bot, _interaction(555, manage_messages=True), rec.id)).id == rec.id

    await service.set_status(rec.id, RecruitStatus.CLOSED)
    closed = _interaction()
    assert await load_manageable(bot, closed, rec.id) is None
    assert "終了" in _sent_text(closed)


async def test_panel_buttons_reject_strangers(service):
    rec = await make_recruitment(service)
    panel = ManagePanel(_bot(service), rec.id)
    for item in panel.children:
        stranger = _interaction(user_id=555)
        await item.callback(stranger)
        stranger.response.send_modal.assert_not_awaited()
        stranger.response.edit_message.assert_not_awaited()
        assert "募集作成者" in _sent_text(stranger)


# ---- 各操作 ----


async def test_member_add_opens_bulk_select(service):
    rec = await make_recruitment(service)
    panel = ManagePanel(_bot(service), rec.id)
    i = _interaction()
    await panel.add_members.callback(i)
    assert isinstance(i.response.edit_message.await_args.kwargs["view"], BulkAddView)


async def test_modals_open_with_current_values(service):
    rec = await make_recruitment(service)
    await service.set_description(rec.id, "前回の募集文")
    panel = ManagePanel(_bot(service), rec.id)
    for button, cls in ((panel.add_guest, GuestModal), (panel.description, DescriptionModal),
                        (panel.deadline, DeadlineModal)):
        i = _interaction()
        await button.callback(i)
        assert isinstance(i.response.send_modal.await_args.args[0], cls)
    i = _interaction()
    await panel.description.callback(i)
    assert i.response.send_modal.await_args.args[0].text.default == "前回の募集文"


async def test_guest_modal_adds_guest(service):
    rec = await make_recruitment(service)
    bot = _bot(service)
    modal = GuestModal(bot, rec.id)
    modal.guest_input._value = "フォロワーA"
    i = _interaction()
    await modal.on_submit(i)
    assert "フォロワーA（新規）を参加者に追加しました" in _sent_text(i)
    assert [p.guest_name for p in await service.list_participants(rec.id) if p.is_guest] == ["フォロワーA"]
    bot.updater.refresh.assert_awaited_once_with(rec.id)


async def test_deadline_modal_validates_and_sets(service):
    rec = await make_recruitment(service)
    bot = _bot(service)
    modal = DeadlineModal(bot, rec.id)
    modal.when._value = "あした"
    i = _interaction()
    await modal.on_submit(i)
    assert "形式" in _sent_text(i)
    assert (await service.get(rec.id)).deadline_at is None

    modal.when._value = "23:59"
    i = _interaction()
    await modal.on_submit(i)
    assert "締切を" in _sent_text(i)
    assert (await service.get(rec.id)).deadline_at is not None
    bot.updater.refresh.assert_awaited_once_with(rec.id)


async def test_remove_users_and_guests(service):
    rec = await make_recruitment(service)
    await service.join(rec.id, 1, Source.BUTTON)
    await service.add_guest(rec.id, "フォロワーA")
    bot = _bot(service, names={CREATOR: "募集主", 1: "メンバー1"})
    panel = ManagePanel(bot, rec.id)
    i = _interaction()
    await panel.remove.callback(i)
    view = i.response.edit_message.await_args.kwargs["view"]
    assert isinstance(view, RemoveView)
    labels = [o.label for o in view.select.options]
    assert labels == ["募集主（募集者）", "メンバー1", "フォロワーA（新規）"]

    guest_id = next(p.user_id for p in await service.list_participants(rec.id) if p.is_guest)
    view.select._values = ["1", str(guest_id)]
    i = _interaction()
    await view.select.callback(i)
    assert i.response.edit_message.await_args.kwargs["content"] == "2人を参加者から外しました。"
    assert [p.user_id for p in await service.list_participants(rec.id)] == [CREATOR]
    bot.updater.refresh.assert_awaited_once_with(rec.id)


async def test_close_requires_confirmation(service):
    rec = await make_recruitment(service)
    bot = _bot(service)
    panel = ManagePanel(bot, rec.id)
    i = _interaction()
    await panel.close_now.callback(i)
    confirm = i.response.edit_message.await_args.kwargs["view"]
    assert isinstance(confirm, ConfirmCloseView)
    assert (await service.get(rec.id)).is_open  # 確認前は閉じない

    i = _interaction()
    await confirm.cancel.callback(i)
    assert isinstance(i.response.edit_message.await_args.kwargs["view"], ManagePanel)
    assert (await service.get(rec.id)).is_open

    i = _interaction()
    await confirm.confirm.callback(i)
    assert not (await service.get(rec.id)).is_open
    assert "引き継ぎ" in i.response.edit_message.await_args.kwargs["content"]
    bot.updater.refresh.assert_awaited_once_with(rec.id)


async def test_carry_over_from_panel(service):
    old = await make_recruitment(service, mode=Mode.BUTTON)
    await service.join(old.id, 1, Source.BUTTON)
    await service.add_guest(old.id, "フォロワーA")
    await service.set_status(old.id, RecruitStatus.CLOSED)
    new = await make_recruitment(service, mode=Mode.BUTTON)
    bot = _bot(service)

    i = _interaction()
    await ManagePanel(bot, new.id).carry_over.callback(i)
    view = i.response.edit_message.await_args.kwargs["view"]
    assert isinstance(view, CarryOverView)
    assert [o.value for o in view.select.options] == [str(old.id)]

    view.select._values = [str(old.id)]
    i = _interaction()
    await view.select.callback(i)
    content = i.response.edit_message.await_args.kwargs["content"]
    assert "2人を追加しました" in content and "フォロワーA（新規）" in content
    assert len(await service.list_participants(new.id)) == 3


async def test_carry_over_without_ended_recruitments(service):
    rec = await make_recruitment(service)
    i = _interaction()
    await ManagePanel(_bot(service), rec.id).carry_over.callback(i)
    assert "終了済みの募集がありません" in _sent_text(i)
    i.response.edit_message.assert_not_awaited()


async def test_full_outcome_unchanged_by_panel(service):
    rec = await make_recruitment(service, capacity=2)
    await service.join(rec.id, 1, Source.BUTTON)
    modal = GuestModal(_bot(service), rec.id)
    modal.guest_input._value = "あふれた人"
    i = _interaction()
    await modal.on_submit(i)
    assert _sent_text(i) == "満員のため追加できません。"
    assert await service.join(rec.id, 2, Source.BUTTON) is Outcome.FULL
