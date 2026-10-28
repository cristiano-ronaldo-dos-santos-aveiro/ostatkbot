"""👥 Бариста: добавить (по ID или ссылкой), переименовать, деактивировать."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_config
from bot.db import crud
from bot.db.models import Role, User
from bot.filters import TEXT_INPUT
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, markup, nav_row
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import InputError, clean_text, fmt_date, fmt_time
from bot.services.notify import notify_user
from bot.states import StaffAdd, StaffInvite, StaffRename
from bot.ui import alert, finish, render, render_input, start_flow, with_error

router = Router(name="manager_staff")


async def staff_view(session: AsyncSession, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    baristas = await crud.list_baristas(session)
    rows = [[btn("➕ Добавить по ID", "stf_add"), btn("🔗 Ссылка-приглашение", "stf_invite")]]
    for b in baristas:
        rows.append([btn(("🚫 " if not b.is_active else "👤 ") + b.name, "stf_card", b.id)])
    rows.append([close_btn()])
    text = "👥 Бариста\n\n🚫 — деактивирован."
    if not baristas:
        text = "👥 Бариста\n\nПока никого нет. Добавьте по telegram_id или отправьте ссылку-приглашение."
    if note:
        text = f"{note}\n\n{text}"
    return text, markup(rows)


async def card_view(session: AsyncSession, b: User, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    shift = await crud.get_open_shift(session, b.id)
    text = (
        f"👤 {b.name}\n"
        f"telegram_id: {b.telegram_id}\n"
        f"Добавлен: {fmt_date(b.created_at)}\n"
        f"Статус: {'активен' if b.is_active else '🚫 деактивирован'}\n"
        f"Смена: {'открыта с ' + fmt_time(shift.opened_at) if shift else 'не открыта'}"
    )
    if note:
        text = f"{note}\n\n{text}"
    return text, markup(
        [
            [btn("✏️ Переименовать", "stf_rename", b.id)],
            [btn("🚫 Деактивировать" if b.is_active else "✅ Активировать", "stf_active", b.id)],
            [back_btn("stf_list"), close_btn()],
        ]
    )


@router.message(F.text == MB.STAFF)
async def staff_entry(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await start_flow(message, state, *await staff_view(session))


@router.callback_query(CB.filter(F.a == "stf_list"))
async def cb_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await render(callback, state, *await staff_view(session))


async def _barista(session: AsyncSession, user_id: int, callback_answer: CallbackAnswer) -> User | None:
    b = await crud.get_user(session, user_id)
    if b is None or b.role != Role.BARISTA:
        alert(callback_answer, "Бариста не найден")
        return None
    return b


@router.callback_query(CB.filter(F.a == "stf_card"))
async def cb_card(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    if b := await _barista(session, callback_data.id, callback_answer):
        await render(callback, state, *await card_view(session, b))


@router.callback_query(CB.filter(F.a == "stf_active"))
async def cb_active(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    b = await _barista(session, callback_data.id, callback_answer)
    if b is None:
        return
    b.is_active = not b.is_active
    await session.commit()
    audit(user, "barista_active", id=b.id, name=b.name, value=b.is_active)
    note = "✅ Бариста активирован." if b.is_active else "🚫 Бариста деактивирован — доступ к боту закрыт."
    if not b.is_active and await crud.get_open_shift(session, b.id):
        note += "\n⚠️ У него осталась открытая смена — её нужно будет закрыть после активации."
    await render(callback, state, *await card_view(session, b, note))
    if b.is_active:
        await notify_user(callback.bot, b.telegram_id, "✅ Доступ к боту восстановлен. Нажмите /start")


# --- добавление по ID -----------------------------------------------------------------

ADD_ID_PROMPT = (
    "➕ Новый бариста\n\nОтправьте его telegram_id (число).\n"
    "Узнать ID: бариста пишет боту /start — бот покажет его ID."
)


@router.callback_query(CB.filter(F.a == "stf_add"))
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await finish(state)
    await state.set_state(StaffAdd.tg_id)
    await render(callback, state, ADD_ID_PROMPT, markup([nav_row(back_btn("stf_list"))]))


@router.message(StaffAdd.tg_id, TEXT_INPUT)
async def add_tg_id(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw = message.text.strip()
    error = None
    if not raw.isdigit():
        error = "telegram_id — это число, например 123456789."
    elif int(raw) in get_config().manager_ids:
        error = "Это менеджер — его не нужно добавлять как бариста."
    else:
        existing = await crud.get_user_by_tg(session, int(raw))
        if existing and existing.role == Role.BARISTA and existing.is_active:
            error = f"Этот пользователь уже бариста: {existing.name}."
    if error:
        await render_input(message, state, with_error(ADD_ID_PROMPT, error), markup([nav_row(back_btn("stf_list"))]))
        return
    await state.update_data(tg_id=int(raw))
    await state.set_state(StaffAdd.name)
    await render_input(message, state, f"➕ Новый бариста (id {raw})\n\nКак его зовут? Отправьте имя:",
                       markup([nav_row()]))


@router.message(StaffAdd.name, TEXT_INPUT)
async def add_name(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    try:
        name = clean_text(message.text, field="Имя")
    except InputError as e:
        await render_input(message, state, with_error("➕ Новый бариста\n\nОтправьте имя:", str(e)),
                           markup([nav_row()]))
        return
    b = await crud.upsert_barista(session, data["tg_id"], name)
    await session.commit()
    audit(user, "barista_add", id=b.id, tg=b.telegram_id, name=name)
    await finish(state)
    sent = await notify_user(message.bot, b.telegram_id, f"✅ {name}, вас добавили бариста. Нажмите /start")
    note = "✅ Бариста добавлен."
    if sent is None:
        note += "\nℹ️ Уведомление не доставлено — пусть он сам напишет боту /start."
    await render_input(message, state, *await card_view(session, b, note))


# --- приглашение ссылкой -------------------------------------------------------------

@router.callback_query(CB.filter(F.a == "stf_invite"))
async def cb_invite(callback: CallbackQuery, state: FSMContext) -> None:
    await finish(state)
    await state.set_state(StaffInvite.name)
    await render(callback, state, "🔗 Приглашение\n\nКак зовут нового бариста? Отправьте имя:",
                 markup([nav_row(back_btn("stf_list"))]))


@router.message(StaffInvite.name, TEXT_INPUT)
async def invite_name(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        name = clean_text(message.text, field="Имя")
    except InputError as e:
        await render_input(message, state, with_error("🔗 Приглашение\n\nОтправьте имя:", str(e)),
                           markup([nav_row()]))
        return
    invite = await crud.create_invite(session, name, user.id)
    await session.commit()
    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start=inv_{invite.token}"
    audit(user, "barista_invite", invite=invite.id, name=name)
    await finish(state)
    await render_input(
        message, state,
        f"🔗 Приглашение для {name} готово.\n\nПерешлите ему ссылку (одноразовая):\n{link}",
        markup([[back_btn("stf_list"), close_btn()]]),
    )


# --- переименование ----------------------------------------------------------------

@router.callback_query(CB.filter(F.a == "stf_rename"))
async def cb_rename(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    if b := await _barista(session, callback_data.id, callback_answer):
        await finish(state)
        await state.set_state(StaffRename.name)
        await state.update_data(barista_id=b.id)
        await render(callback, state, f"✏️ {b.name}\n\nОтправьте новое имя:",
                     markup([nav_row(back_btn("stf_card", b.id))]))


@router.message(StaffRename.name, TEXT_INPUT)
async def rename(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    b = await crud.get_user(session, (await state.get_data()).get("barista_id", 0))
    if b is None:
        await finish(state)
        return
    try:
        name = clean_text(message.text, field="Имя")
    except InputError as e:
        await render_input(message, state, with_error(f"✏️ {b.name}\n\nОтправьте новое имя:", str(e)),
                           markup([nav_row(back_btn("stf_card", b.id))]))
        return
    old, b.name = b.name, name
    await session.commit()
    audit(user, "barista_rename", id=b.id, old=old, new=name)
    await finish(state)
    await render_input(message, state, *await card_view(session, b, "✅ Имя изменено."))
