import json
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.keyboards import (
    catalog_keyboard,
    auth_keyboard,
    main_menu_keyboard,
    order_actions_keyboard,
    product_actions_keyboard,
    products_keyboard,
    registration_done_keyboard,
    start_keyboard,
)
from app.bot.states import DebugOrgStates, LoginStates, RegistrationStates, RequestStates
from app.config import settings
from app.crud import (
    create_organization,
    create_thread,
    create_search_log,
    get_or_create_draft_order,
    add_item_to_order,
    get_user_by_phone,
    get_user_by_tg_id,
    list_orders_for_user,
    list_root_categories,
    list_products_by_category,
    list_subcategories,
)
from app.database import get_session_context
from app.models import Message as ThreadMessage
from app.models import OrgMember, Order, Product, Thread, User
from app.services.llm_client import chat
from app.services.llm_intent_router import get_stock_eta, route_message
from app.services.llm_category_narrow import narrow_categories
from app.services.llm_normalize import suggest_queries
from app.services.llm_rerank import rerank_products
from app.services.org_aliases import autolearn_org_alias, find_org_alias_candidates, normalize_alias_for_autolearn, upsert_org_alias
from app.services.order_parser import parse_order_text
from app.services.search import search_products
from app.services.history_candidates import get_org_candidates
from app.services.search_pipeline import run_search_pipeline
from app.request_handler import handle_message as handle_request_message
from app.request_handler.types import DialogContext
from app.utils.security import hash_password, verify_password

router = Router()
logger = logging.getLogger(__name__)
_CANDIDATES_TTL_SECONDS = 600
_REQUEST_MODE_TTL_SECONDS = 60 * 60 * 24
_REQUEST_MODE_MEM: dict[int, dict[str, object]] = {}


def _normalized_text(message: Message) -> str:
    return (message.text or "").strip().lower()


def _is_login_command(message: Message) -> bool:
    return _normalized_text(message) == "вход"


def _is_registration_command(message: Message) -> bool:
    return _normalized_text(message) == "регистрация"


def _normalize_phone(raw: str) -> str:
    return raw.strip().replace(" ", "")


def _is_valid_phone(raw: str) -> bool:
    normalized = _normalize_phone(raw)
    return normalized.startswith("+") and len(normalized) >= 11


def _phones_match(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    na = "".join(ch for ch in a if ch.isdigit())
    nb = "".join(ch for ch in b if ch.isdigit())
    if not na or not nb:
        return False
    return na[-10:] == nb[-10:]


def _admin_tg_match(message: Message) -> bool:
    if settings.admin_tg_id and message.from_user.id == settings.admin_tg_id:
        return True
    if settings.admin_tg_username:
        username = (message.from_user.username or "").lower()
        return username == settings.admin_tg_username.lower().lstrip("@")
    return False


def _redis_client() -> Redis | None:
    if not settings.redis_url:
        return None
    return Redis.from_url(settings.redis_url)


def _candidate_cache_key(tg_id: int, message_id: int) -> str:
    return f"candidates:{tg_id}:{message_id}"


def _admin_user_ids() -> set[int]:
    raw = (settings.admin_user_ids or "").strip()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    if settings.admin_tg_id:
        ids.add(settings.admin_tg_id)
    return ids


def _is_admin_tg_id(tg_id: int) -> bool:
    return tg_id in _admin_user_ids()


def _debug_org_key(tg_id: int) -> str:
    return f"tg:debug_org:{tg_id}"


def _menu_anchor_key(tg_id: int) -> str:
    return f"tg:menu_message:{tg_id}"


def _results_anchor_key(tg_id: int) -> str:
    return f"tg:results_message:{tg_id}"


def _request_mode_key(tg_id: int) -> str:
    return f"tg:request_mode:{tg_id}"


async def _get_debug_org_id(tg_id: int) -> int | None:
    client = _redis_client()
    if not client:
        return None
    value = await client.get(_debug_org_key(tg_id))
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _set_debug_org_id(tg_id: int, org_id: int | None) -> None:
    client = _redis_client()
    if not client:
        return
    key = _debug_org_key(tg_id)
    if org_id is None:
        await client.delete(key)
    else:
        await client.set(key, str(org_id), ex=60 * 60 * 24)


async def _resolve_org_for_user(session: AsyncSession, tg_id: int, user: User | None) -> int | None:
    debug_org_id = await _get_debug_org_id(tg_id)
    if debug_org_id:
        return debug_org_id
    if _is_admin_tg_id(tg_id):
        return 1
    if user:
        result = await session.execute(
            select(OrgMember)
            .where(OrgMember.user_id == user.id, OrgMember.status == "active")
            .order_by(OrgMember.org_id)
        )
        member = result.scalars().first()
        if member:
            return member.org_id
    return None


def _request_mode_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Создать новую заявку"), KeyboardButton(text="Добавить в заказ")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="Сменить организацию")])
    rows.append([KeyboardButton(text="Выйти")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _clip(text: str, max_len: int = 58) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _request_control_keyboard(data: dict[str, object], is_admin: bool = False) -> InlineKeyboardMarkup:
    mode = str(data.get("mode") or "start")
    selected_order_id = data.get("selected_order_id")
    items = data.get("items") if isinstance(data.get("items"), list) else []
    offset = int(data.get("items_page_offset") or 0)
    expanded = bool(data.get("clarify_expanded", data.get("expanded")))
    selected_idx = int(data.get("current_clarify_index", data.get("selected_item_index") or 0))

    rows: list[list[InlineKeyboardButton]] = []
    if mode == "choose_order":
        orders = data.get("orders_page") if isinstance(data.get("orders_page"), list) else []
        order_row = []
        for order in orders[:5]:
            if not isinstance(order, dict):
                continue
            oid = order.get("id")
            if isinstance(oid, int):
                order_row.append(InlineKeyboardButton(text=f"Заказ #{oid}", callback_data=f"rm:pick_order:{oid}"))
        if order_row:
            rows.append(order_row)
        rows.append([
            InlineKeyboardButton(text="◀", callback_data=f"rm:orders:prev:{max(0, offset-5)}"),
            InlineKeyboardButton(text="▶", callback_data=f"rm:orders:next:{offset+5}"),
        ])
        rows.append([InlineKeyboardButton(text="Вернуться", callback_data="rm:start")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if items:
        page_items = items[offset: offset + 10]
        item_row: list[InlineKeyboardButton] = []
        for index, item in enumerate(page_items, start=offset + 1):
            status = str((item or {}).get("status") or "unknown")
            icon = "✅" if status == "ok" else "❌"
            item_row.append(InlineKeyboardButton(text=f"{index} {icon}", callback_data=f"rm:item:{index-1}"))
            if len(item_row) == 5:
                rows.append(item_row)
                item_row = []
        if item_row:
            rows.append(item_row)
        if len(items) > 10:
            rows.append([
                InlineKeyboardButton(text="◀", callback_data=f"rm:items:prev:{max(0, offset-10)}"),
                InlineKeyboardButton(text="▶", callback_data=f"rm:items:next:{offset+10}"),
            ])

    if mode in {"draft", "review"}:
        add_label = f"Добавить в заказ #{selected_order_id}" if isinstance(selected_order_id, int) else "Добавить в заказ"
        rows.append([InlineKeyboardButton(text=add_label, callback_data="rm:orders")])
        rows.append([InlineKeyboardButton(text="Подтвердить распознанное", callback_data="rm:confirm")])
        rows.append([
            InlineKeyboardButton(text="Отменить", callback_data="rm:cancel"),
            InlineKeyboardButton(text="Передать всё менеджеру", callback_data="rm:manager_all"),
        ])
        toggle = "Разбор нераспознанных ▲" if expanded else "Разбор нераспознанных ▼"
        rows.append([InlineKeyboardButton(text=toggle, callback_data="rm:clarify:toggle")])

        if expanded and items and 0 <= selected_idx < len(items):
            current = items[selected_idx] if isinstance(items[selected_idx], dict) else {}
            clarification = current.get("clarification") if isinstance(current.get("clarification"), dict) else None
            if isinstance(clarification, dict):
                opts = clarification.get("options") if isinstance(clarification.get("options"), list) else []
                for idx, option in enumerate(opts[:10], start=1):
                    if not isinstance(option, dict):
                        continue
                    oid = option.get("id") or idx
                    label = _clip(str(option.get("label") or "Вариант"), 48)
                    rows.append([InlineKeyboardButton(text=label, callback_data=f"rm:clarify:choose:{oid}")])
                prev_offset = clarification.get("prev_offset")
                next_offset = clarification.get("next_offset")
                nav = []
                if isinstance(prev_offset, int):
                    nav.append(InlineKeyboardButton(text="◀", callback_data=f"rm:clarify:prev:{prev_offset}"))
                if isinstance(next_offset, int):
                    nav.append(InlineKeyboardButton(text="▶", callback_data=f"rm:clarify:next:{next_offset}"))
                if nav:
                    rows.append(nav)
            rows.append([InlineKeyboardButton(text="Пропустить/Менеджер", callback_data="rm:clarify:skip")])

        questions = data.get("questions") if isinstance(data.get("questions"), list) else []
        if questions:
            rows.append([
                InlineKeyboardButton(text="Вопросы → менеджеру", callback_data="rm:questions:manager"),
                InlineKeyboardButton(text="Ответить шаблоном", callback_data="rm:questions:template"),
            ])
    else:
        rows.append([
            InlineKeyboardButton(text="Создать новую заявку", callback_data="rm:new"),
            InlineKeyboardButton(text="Добавить в заказ", callback_data="rm:orders"),
        ])
        if is_admin:
            rows.append([InlineKeyboardButton(text="Сменить организацию", callback_data="rm:org")])
        rows.append([InlineKeyboardButton(text="Выход", callback_data="rm:exit")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _request_results_text(data: dict[str, object]) -> str:
    items = data.get("items") if isinstance(data.get("items"), list) else []
    if not items:
        return "Результаты обработки\nПока нет позиций."
    lines = ["Результаты обработки:"]
    for idx, item in enumerate(items[:10], start=1):
        if not isinstance(item, dict):
            continue
        raw = _clip(str(item.get("raw") or ""), 42)
        status = str(item.get("status") or "unknown")
        icon = "✅" if status == "ok" else ("⏳" if status == "question" else "❌")
        best = _clip(str(item.get("result_title") or "требуется уточнение"), 42)
        lines.append(f"{icon} {idx}) {raw} → {best}")
    if len(items) > 10:
        lines.append(f"…и ещё {len(items) - 10}")
    questions = data.get("questions") if isinstance(data.get("questions"), list) else []
    if questions:
        lines.append("❓ Вопросы:")
        for i, q in enumerate(questions[:3], start=1):
            lines.append(f"{i}) {_clip(str(q), 50)}")
    return "\n".join(lines)


def _request_control_text(data: dict[str, object]) -> str:
    mode = str(data.get("mode") or "start")
    status = str(data.get("status") or "Статус: ожидаю сообщение…")
    selected_order_id = data.get("selected_order_id")
    base: list[str]
    if mode == "start":
        base = [
            "Панель заявки",
            "Отправьте заявку или список (позиция + количество).",
        ]
    elif mode == "choose_order":
        base = ["Панель заявки", "Выберите заказ для добавления распознанных позиций."]
    else:
        order_text = f"#{selected_order_id}" if isinstance(selected_order_id, int) else "не выбран"
        base = ["Панель заявки", "Я распознал часть позиций. Проверьте.", f"Заказ: {order_text}"]

    expanded = bool(data.get("clarify_expanded", data.get("expanded")))
    if expanded:
        items = data.get("items") if isinstance(data.get("items"), list) else []
        idx = int(data.get("current_clarify_index", data.get("selected_item_index") or 0))
        if 0 <= idx < len(items) and isinstance(items[idx], dict):
            raw = _clip(str(items[idx].get("raw") or ""), 48)
            clar = items[idx].get("clarification") if isinstance(items[idx].get("clarification"), dict) else {}
            offset = int(clar.get("offset") or 0)
            total = int(clar.get("total") or 0)
            page = (offset // 10) + 1 if total else 1
            pages = (total + 9) // 10 if total else 1
            base.append(f"Уточните позицию {idx + 1}: {raw}")
            base.append(f"Варианты: стр. {page}/{pages}")
    base.append(status)
    return "\n".join(base)


def _default_request_state(org_id: int | None) -> dict[str, object]:
    return {
        "control_msg_id": None,
        "results_msg_id": None,
        "org_id_effective": org_id,
        "selected_order_id": None,
        "last_request_text": "",
        "items": [],
        "selected_item_index": 0,
        "current_clarify_index": 0,
        "items_page_offset": 0,
        "clarify_page_offset": 0,
        "clarify_expanded": False,
        "mode": "start",
        "status": "Статус: ожидаю сообщение…",
        "questions": [],
        "orders_offset": 0,
        "orders_page": [],
    }


async def _load_request_state(tg_id: int, org_id: int | None) -> dict[str, object]:
    client = _redis_client()
    if client:
        raw = await client.get(_request_mode_key(tg_id))
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    if org_id is not None:
                        data["org_id_effective"] = org_id
                    return data
            except Exception:
                logger.warning("Failed to decode request mode state", exc_info=True)
    data = _REQUEST_MODE_MEM.get(tg_id)
    if isinstance(data, dict):
        if org_id is not None:
            data["org_id_effective"] = org_id
        return data
    return _default_request_state(org_id)


async def _save_request_state(tg_id: int, data: dict[str, object]) -> None:
    client = _redis_client()
    if client:
        await client.setex(_request_mode_key(tg_id), _REQUEST_MODE_TTL_SECONDS, json.dumps(data, ensure_ascii=False))
    _REQUEST_MODE_MEM[tg_id] = data


async def _edit_card(message: Message, msg_id: int | None, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> int:
    if isinstance(msg_id, int):
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
            )
            return msg_id
        except Exception:
            logger.info("Card edit failed, sending new message", exc_info=True)
    sent = await message.answer(text, reply_markup=reply_markup)
    return sent.message_id


async def _render_request_cards(message: Message, data: dict[str, object], is_admin: bool = False) -> dict[str, object]:
    results_id = await _edit_card(message, data.get("results_msg_id"), _request_results_text(data))
    control_id = await _edit_card(
        message,
        data.get("control_msg_id"),
        _request_control_text(data),
        reply_markup=_request_control_keyboard(data, is_admin=is_admin),
    )
    data["results_msg_id"] = results_id
    data["control_msg_id"] = control_id
    await _save_request_state(message.from_user.id, data)
    return data



def _shorten_title(title: str, max_len: int = 50) -> str:
    cleaned = " ".join((title or "").split())
    if not cleaned:
        return "Товар"
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _alias_keyboard(titles: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for idx, title in enumerate(titles, start=1):
        short_title = _shorten_title(title)
        rows.append(
            [InlineKeyboardButton(text=f"✅ {idx}) {short_title}", callback_data=f"alias:{idx}")]
        )
    rows.append([InlineKeyboardButton(text="❌ Не оно", callback_data="alias:no")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _clarify_keyboard(clarification: dict[str, object]) -> InlineKeyboardMarkup:
    options = clarification.get("options") if isinstance(clarification, dict) else []
    rows = []
    for idx, option in enumerate((options or [])[:10], start=1):
        label = str((option or {}).get("label") or f"Вариант {idx}")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"clarify:choose:{idx}")])

    nav_row = []
    prev_offset = clarification.get("prev_offset") if isinstance(clarification, dict) else None
    next_offset = clarification.get("next_offset") if isinstance(clarification, dict) else None
    if isinstance(prev_offset, int):
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"clarify:prev:{prev_offset}"))
    if isinstance(next_offset, int):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"clarify:next:{next_offset}"))
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _apply_clarification_tokens(base_query: str, append_tokens: list[str] | None) -> str:
    query = (base_query or "").strip()
    extra = [str(token).strip() for token in (append_tokens or []) if str(token).strip()]
    if not extra:
        return query
    return " ".join([query, *extra]).strip()


async def _handle_auth_interrupts(message: Message, state: FSMContext) -> bool:
    if _is_login_command(message):
        await login_start(message, state)
        return True
    if _is_registration_command(message):
        await registration_start(message, state)
        return True
    return False


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Добро пожаловать в Партнер-м. Здесь вы сможете оформить заказ и провести необходимые операции с заказом, оплатой и отгрузкой.",
        reply_markup=start_keyboard(),
    )


@router.message(Command("llm_test"))
async def llm_test(message: Message) -> None:
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
    if not user:
        if not _admin_tg_match(message):
            await message.answer("Команда доступна только администратору.")
            return
    elif (
        user.role != "admin"
        and not _phones_match(user.phone, settings.admin_phone)
        and not _admin_tg_match(message)
    ):
        await message.answer("Команда доступна только администратору.")
        return
    try:
        content = await chat(
            [
                {"role": "system", "content": "Ответь одним словом: ок"},
                {"role": "user", "content": "ок"},
            ],
            temperature=0.2,
        )
    except Exception:
        logger.exception("LLM test failed")
        await message.answer("LLM тест не прошел. Проверьте локальный LLM/Ollama.")
        return
    await message.answer(f"LLM ответ: {content}")




@router.message(Command("org"))
async def org_command(message: Message, state: FSMContext) -> None:
    if not _is_admin_tg_id(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    current = await _get_debug_org_id(message.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Org 1", callback_data="org:set:1"), InlineKeyboardButton(text="Org 42", callback_data="org:set:42")],
            [InlineKeyboardButton(text="Сброс", callback_data="org:clear")],
        ]
    )
    await state.set_state(DebugOrgStates.awaiting_org_id)
    await message.answer(f"Текущая организация: {current or 1}. Выберите org или введите org_id числом.", reply_markup=kb)


@router.callback_query(F.data.startswith("org:"))
async def org_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 2:
        await callback.answer("Не удалось обработать")
        return
    action = parts[1]
    if action == "clear":
        await _set_debug_org_id(callback.from_user.id, None)
        await callback.answer("Организация сброшена")
        if callback.message:
            await callback.message.edit_text("Организация сброшена. Сейчас используется fallback org=1.")
        await state.clear()
        return
    if action == "set" and len(parts) == 3 and parts[2].isdigit():
        org_id = int(parts[2])
        await _set_debug_org_id(callback.from_user.id, org_id)
        await callback.answer("Готово")
        if callback.message:
            await callback.message.edit_text(f"Установлена организация: {org_id}")
        await state.clear()
        return
    await callback.answer("Не удалось обработать")


@router.message(DebugOrgStates.awaiting_org_id, F.text.regexp(r"^\d+$"))
async def org_input(message: Message, state: FSMContext) -> None:
    if not _is_admin_tg_id(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    org_id = int(message.text)
    await _set_debug_org_id(message.from_user.id, org_id)
    await state.clear()
    await message.answer(f"Установлена организация: {org_id}")


@router.callback_query(F.data.startswith("alias:"))
async def alias_confirm(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    action = callback.data.split(":", 1)[1]
    if action == "no":
        await callback.answer("Понял, не оно.")
        return
    try:
        index = int(action) - 1
    except ValueError:
        await callback.answer("Не удалось обработать выбор.")
        return
    redis_client = _redis_client()
    if not redis_client:
        await callback.answer("Контекст недоступен.")
        return
    cache_key = _candidate_cache_key(callback.from_user.id, callback.message.message_id)
    cached = await redis_client.get(cache_key)
    if not cached:
        await callback.answer("Контекст устарел.")
        return
    payload = json.loads(cached)
    products = payload.get("products") or []
    org_id = payload.get("org_id")
    alias_text = payload.get("alias_text") or ""
    if not isinstance(products, list) or index < 0 or index >= len(products):
        await callback.answer("Не удалось обработать выбор.")
        return
    product_id = products[index]
    if not isinstance(product_id, int) or not org_id:
        await callback.answer("Не удалось обработать выбор.")
        return
    async with get_session_context() as session:
        await upsert_org_alias(session, org_id, alias_text, product_id)
        await session.commit()
    await callback.answer("Запомнил. В следующий раз буду понимать быстрее.")


@router.callback_query(F.data.startswith("clarify:"))
async def clarify_choice(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Не удалось обработать выбор.")
        return

    action = parts[1]
    raw_value = parts[2]

    redis_client = _redis_client()
    if not redis_client:
        await callback.answer("Контекст недоступен.")
        return

    cache_key = _candidate_cache_key(callback.from_user.id, callback.message.message_id)
    cached = await redis_client.get(cache_key)
    if not cached:
        await callback.answer("Контекст устарел.")
        return

    payload = json.loads(cached)
    base_query = str(payload.get("base_query") or "")
    org_id = payload.get("org_id")
    user_id = payload.get("user_id")

    if action in {"next", "prev"}:
        try:
            offset = int(raw_value)
        except ValueError:
            await callback.answer("Не удалось обработать выбор.")
            return

        async with get_session_context() as session:
            pipeline_result = await run_search_pipeline(
                session,
                org_id=org_id if isinstance(org_id, int) else None,
                user_id=user_id if isinstance(user_id, int) else None,
                text=base_query,
                limit=5,
                enable_llm_narrow=False,
                enable_llm_rewrite=False,
                enable_rerank=False,
                clarify_offset=offset,
            )

        decision_payload = pipeline_result.get("decision", {}) if isinstance(pipeline_result, dict) else {}
        clarification = decision_payload.get("clarification") if isinstance(decision_payload, dict) else None
        if isinstance(clarification, dict):
            question = str(clarification.get("question") or "Уточни вариант:")
            await callback.message.edit_text(question, reply_markup=_clarify_keyboard(clarification))
            await redis_client.setex(
                cache_key,
                _CANDIDATES_TTL_SECONDS,
                json.dumps(
                    {
                        "org_id": org_id,
                        "user_id": user_id,
                        "base_query": base_query,
                        "clarification": clarification,
                    },
                    ensure_ascii=False,
                ),
            )
            await callback.answer()
            return

        await callback.answer("Больше вариантов нет.")
        return

    if action != "choose":
        await callback.answer("Не удалось обработать выбор.")
        return

    clarification = payload.get("clarification") or {}
    options = clarification.get("options") if isinstance(clarification, dict) else []
    if not isinstance(options, list) or not options:
        await callback.answer("Вариант недоступен.")
        return

    selected_option = None
    for option in options:
        if isinstance(option, dict) and str(option.get("id") or "") == raw_value:
            selected_option = option
            break

    if selected_option is None and raw_value.isdigit():
        idx = int(raw_value) - 1
        if 0 <= idx < len(options):
            option = options[idx]
            selected_option = option if isinstance(option, dict) else None

    if selected_option is None:
        await callback.answer("Вариант недоступен.")
        return

    apply = selected_option.get("apply") if isinstance(selected_option, dict) else {}
    apply = apply if isinstance(apply, dict) else {}
    if isinstance(apply.get("set_query"), str) and apply.get("set_query").strip():
        next_query = str(apply.get("set_query")).strip()
    else:
        append_tokens = apply.get("append_tokens") if isinstance(apply.get("append_tokens"), list) else []
        next_query = _apply_clarification_tokens(base_query, append_tokens)

    async with get_session_context() as session:
        pipeline_result = await run_search_pipeline(
            session,
            org_id=org_id if isinstance(org_id, int) else None,
            user_id=user_id if isinstance(user_id, int) else None,
            text=next_query,
            limit=5,
            enable_llm_narrow=False,
            enable_llm_rewrite=False,
            enable_rerank=False,
            clarify_offset=0,
        )

    results = pipeline_result.get("results", []) if isinstance(pipeline_result, dict) else []
    if results:
        lines = [f"{idx}. {item.get('title_ru')} (SKU: {item.get('sku')})" for idx, item in enumerate(results, start=1)]
        await callback.message.edit_text("Вот что нашлось:\n" + "\n".join(lines))
    else:
        await callback.message.edit_text("Не нашёл, уточни товар/артикул (можно размер/цвет/тип).")
    await callback.answer()


@router.message(StateFilter("*"), _is_registration_command)
async def registration_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RegistrationStates.fio)
    logger.info("Registration started for tg_id=%s", message.from_user.id)
    await message.answer("Введите ваше ФИО:")


@router.message(RegistrationStates.fio)
async def registration_fio(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    await state.update_data(fio=message.text)
    await state.set_state(RegistrationStates.org_name)
    await message.answer("Введите название организации или напишите 'Частное лицо':")


@router.message(RegistrationStates.org_name)
async def registration_org(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    await state.update_data(org_name=message.text)
    await state.set_state(RegistrationStates.phone)
    await message.answer("Введите телефон в формате +79998887766:")


@router.message(RegistrationStates.phone)
async def registration_phone(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    phone = _normalize_phone(message.text or "")
    if not _is_valid_phone(phone):
        await message.answer("Телефон некорректный. Введите в формате +79998887766:")
        return
    await state.update_data(phone=phone)
    await state.set_state(RegistrationStates.address)
    await message.answer("Введите адрес доставки:")


@router.message(RegistrationStates.address)
async def registration_address(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    await state.update_data(address=message.text)
    await state.set_state(RegistrationStates.work_time)
    await message.answer("Введите время работы (например 09:00-18:00) или 'Круглосуточно':")


@router.message(RegistrationStates.work_time)
async def registration_work_time(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    is_24h = message.text.lower() == "круглосуточно"
    await state.update_data(work_time=message.text, is_24h=is_24h)
    await state.set_state(RegistrationStates.email)
    await message.answer("Введите email (можно пропустить, отправив '-'): ")


@router.message(RegistrationStates.email)
async def registration_email(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    email = None if message.text.strip() == "-" else message.text
    await state.update_data(email=email)
    await state.set_state(RegistrationStates.password)
    await message.answer("Придумайте пароль:")


@router.message(RegistrationStates.password)
async def registration_password(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    if len(message.text or "") < 5:
        await message.answer("Пароль слишком короткий. Минимум 5 символов:")
        return
    data = await state.get_data()
    org_name = data["org_name"]
    async with get_session_context() as session:
        if await get_user_by_phone(session, data["phone"]):
            logger.info("Registration blocked: phone exists %s", data["phone"])
            await message.answer("Пользователь с таким телефоном уже существует. Попробуйте вход.")
            await state.clear()
            return
        role = "client"
        if data["phone"] == settings.admin_phone:
            role = "admin"
        elif data["phone"] == settings.manager_phone:
            role = "manager"
        user = User(
            fio=data["fio"],
            phone=data["phone"],
            email=data["email"],
            password_hash=hash_password(message.text),
            address=data["address"],
            work_time=data["work_time"],
            is_24h=data["is_24h"],
            role=role,
            tg_id=message.from_user.id,
        )
        try:
            session.add(user)
            await session.flush()
            if org_name.lower() != "частное лицо":
                org = await create_organization(session, org_name, user.id)
                session.add(OrgMember(org_id=org.id, user_id=user.id, role_in_org="owner"))
            await session.commit()
            logger.info("User registered id=%s phone=%s", user.id, user.phone)
        except IntegrityError:
            await session.rollback()
            logger.exception("Registration failed for phone=%s", data["phone"])
            await message.answer("Не удалось создать пользователя. Проверьте телефон и попробуйте снова.")
            await state.clear()
            return
    await state.clear()
    await message.answer(
        "Регистрация завершена. Теперь выполните вход по телефону и паролю.",
        reply_markup=auth_keyboard(),
    )


@router.message(lambda msg: _normalized_text(msg) == "регистрация завершена")
async def registration_done(message: Message) -> None:
    await message.answer("Теперь выполните вход по телефону и паролю.", reply_markup=auth_keyboard())


@router.message(StateFilter("*"), _is_login_command)
async def login_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(LoginStates.phone)
    logger.info("Login started for tg_id=%s", message.from_user.id)
    await message.answer("Введите телефон:")


@router.message(LoginStates.phone)
async def login_phone(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    phone = _normalize_phone(message.text or "")
    if not _is_valid_phone(phone):
        await message.answer("Телефон некорректный. Введите в формате +79998887766:")
        return
    await state.update_data(phone=phone)
    await state.set_state(LoginStates.password)
    await message.answer("Введите пароль:")


@router.message(LoginStates.password)
async def login_password(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    data = await state.get_data()
    async with get_session_context() as session:
        user = await get_user_by_phone(session, data["phone"])
        if not user or not verify_password(message.text, user.password_hash):
            logger.info("Login failed for phone=%s", data["phone"])
            await message.answer("Неверный телефон или пароль. Если нет аккаунта — нажмите «Регистрация».")
            await state.clear()
            return
        user.tg_id = message.from_user.id
        await session.commit()
        logger.info("Login success user_id=%s phone=%s", user.id, user.phone)
    await state.clear()
    await message.answer("Вход выполнен.", reply_markup=main_menu_keyboard())


@router.message(F.text == "Каталог")
@router.message(F.text == "Открыть каталог")
async def show_catalog(message: Message) -> None:
    async with get_session_context() as session:
        categories = await list_root_categories(session)
    if not categories:
        await message.answer("Каталог пуст. Обратитесь к менеджеру.")
        return
    await _send_category_page(message, categories, page=0)


async def _send_category_page(message: Message, categories: list, page: int) -> None:
    per_page = 8
    total_pages = max(1, (len(categories) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = categories[start : start + per_page]
    await message.answer(
        "Выберите категорию:",
        reply_markup=catalog_keyboard([(cat.id, cat.title_ru) for cat in chunk], page, total_pages, prefix="cat"),
    )


@router.callback_query(F.data.startswith("catpage:"))
async def category_page(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    async with get_session_context() as session:
        categories = await list_root_categories(session)
    await callback.message.edit_text(
        "Выберите категорию:",
        reply_markup=catalog_keyboard([(cat.id, cat.title_ru) for cat in categories[page * 8 : page * 8 + 8]], page, max(1, (len(categories) + 7) // 8), prefix="cat"),
    )


@router.callback_query(F.data.startswith("cat:"))
async def category_click(callback: CallbackQuery) -> None:
    cat_id = int(callback.data.split(":")[1])
    async with get_session_context() as session:
        subcats = await list_subcategories(session, cat_id)
        products = await list_products_by_category(session, cat_id)
    if subcats:
        await callback.message.edit_text(
            "Выберите подкатегорию:",
            reply_markup=catalog_keyboard([(cat.id, cat.title_ru) for cat in subcats], 0, 1, prefix="cat"),
        )
        return
    if products:
        await _send_product_page(callback.message, products, page=0, category_id=cat_id)
        return
    await callback.message.edit_text("В этой категории пока нет товаров.")

async def _send_product_page(message: Message, products: list[Product], page: int, category_id: int) -> None:
    per_page = 8
    total_pages = max(1, (len(products) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = products[start : start + per_page]
    await message.edit_text(
        "Товары:",
        reply_markup=products_keyboard(
            [(prod.id, prod.title_ru) for prod in chunk], page, total_pages, prefix="prod", context=str(category_id)
        ),
    )


@router.callback_query(F.data.startswith("prodpage:"))
async def product_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    page = int(parts[1])
    category_id = int(parts[2]) if len(parts) > 2 else 0
    async with get_session_context() as session:
        products = await list_products_by_category(session, category_id) if category_id else []
    if not products:
        await callback.message.edit_text("Товары не найдены.")
        return
    await _send_product_page(callback.message, products, page, category_id)


@router.callback_query(F.data.startswith("sprodpage:"))
async def search_product_page(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    async with get_session_context() as session:
        result = await session.execute(select(Product).order_by(Product.title_ru))
        products = result.scalars().all()
    await callback.message.edit_text(
        "Результаты поиска:",
        reply_markup=products_keyboard(
            [(prod.id, prod.title_ru) for prod in products[page * 8 : page * 8 + 8]],
            page,
            max(1, (len(products) + 7) // 8),
            prefix="sprod",
        ),
    )


@router.callback_query(F.data.startswith("prod:"))
@router.callback_query(F.data.startswith("sprod:"))
async def product_click(callback: CallbackQuery) -> None:
    prod_id = int(callback.data.split(":")[1])
    async with get_session_context() as session:
        result = await session.execute(select(Product).where(Product.id == prod_id))
        product = result.scalar_one_or_none()
    if not product:
        await callback.message.edit_text("Товар не найден.")
        return
    await callback.message.edit_text(
        f"{product.title_ru}\nЦена: {product.price}\nВ наличии: {product.stock_qty}\nSKU: {product.sku or '-'}",
        reply_markup=product_actions_keyboard(product.id),
    )


@router.callback_query(F.data.startswith("add:"))
async def add_to_order(callback: CallbackQuery) -> None:
    prod_id = int(callback.data.split(":")[1])
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Сначала выполните вход.")
            return
        result = await session.execute(select(Product).where(Product.id == prod_id))
        product = result.scalar_one_or_none()
        if not product:
            await callback.message.edit_text("Товар не найден.")
            return
        order = await get_or_create_draft_order(session, user)
        await add_item_to_order(session, order, product, qty=1)
        await session.commit()
    await callback.message.edit_text("Товар добавлен в черновик заказа.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "back:catalog")
async def back_to_catalog(callback: CallbackQuery) -> None:
    async with get_session_context() as session:
        categories = await list_root_categories(session)
    if not categories:
        await callback.message.edit_text("Каталог пуст. Обратитесь к менеджеру.")
        return
    await callback.message.edit_text(
        "Выберите категорию:",
        reply_markup=catalog_keyboard([(cat.id, cat.title_ru) for cat in categories[:8]], 0, max(1, (len(categories) + 7) // 8), prefix="cat"),
    )


@router.callback_query(F.data.startswith("order:submit:"))
async def submit_order(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    async with get_session_context() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            await callback.message.edit_text("Заказ не найден.")
            return
        order.status = "pending"
        await session.commit()
    await callback.message.edit_text("Заказ оформлен. Менеджер свяжется с вами.")


@router.callback_query(F.data.startswith("order:cancel:"))
async def cancel_order(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    async with get_session_context() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            await callback.message.edit_text("Заказ не найден.")
            return
        order.status = "cancelled"
        await session.commit()
    await callback.message.edit_text("Заказ отменён.")


@router.callback_query(F.data.startswith("order:question:"))
async def order_question(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Сначала выполните вход.")
            return
        title = f"Заказ #{order_id}"
        thread = await create_thread(session, user.org_memberships[0].org_id if user.org_memberships else None, title)
        session.add(
            ThreadMessage(
                thread_id=thread.id,
                author_user_id=user.id,
                author_name_snapshot=user.fio,
                text=f"Вопрос по заказу #{order_id}",
            )
        )
        await session.commit()
    await callback.message.edit_text("Вопрос создан. Менеджер ответит в ближайшее время.")


@router.message(F.text == "Заказы")
async def list_orders(message: Message) -> None:
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните вход.")
            return
        orders = await list_orders_for_user(session, user)
    if not orders:
        await message.answer("Заказов пока нет.")
        return
    for order in orders:
        items = ", ".join([f"{item.product.title_ru} x{item.qty}" for item in order.items]) if order.items else "без позиций"
        await message.answer(
            f"#{order.id} — {order.status}\n{items}",
            reply_markup=order_actions_keyboard(order.id, order.status),
        )


@router.message(F.text == "Мои вопросы")
async def list_questions(message: Message) -> None:
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните вход.")
            return
        if not user.org_memberships:
            await message.answer("У вашей организации пока нет вопросов.")
            return
        result = await session.execute(select(Thread).where(Thread.org_id == user.org_memberships[0].org_id))
        threads = result.scalars().all()
    if not threads:
        await message.answer("Вопросов пока нет.")
        return
    lines = [f"#{thread.id} — {thread.title}" for thread in threads]
    await message.answer("Темы:\n" + "\n".join(lines))


@router.message(F.text == "Баланс")
async def balance(message: Message) -> None:
    await message.answer("Баланс будет рассчитан после подключения платежей и отгрузок.")


@router.message(F.text == "Пригласить работника")
async def invite_worker(message: Message) -> None:
    await message.answer("Отправьте телефон сотрудника. Мы пригласим его в вашу организацию.")


@router.message(F.text == "Аккаунт")
async def account(message: Message) -> None:
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
    if not user:
        await message.answer("Сначала выполните вход.")
        return
    await message.answer(
        f"ФИО: {user.fio}\nТелефон: {user.phone}\nРоль: {user.role}\n",
    )




@router.message(F.text == "Отправить заявку")
async def request_mode_start(message: Message, state: FSMContext) -> None:
    is_admin = _is_admin_tg_id(message.from_user.id)
    await state.set_state(RequestStates.awaiting_text)
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        org_id = await _resolve_org_for_user(session, message.from_user.id, user)
    data = await _load_request_state(message.from_user.id, org_id)
    data["mode"] = "start"
    data["status"] = "Статус: ожидаю сообщение…"
    data["clarify_expanded"] = False
    if not isinstance(data.get("items"), list):
        data["items"] = []
    await _render_request_cards(message, data, is_admin=is_admin)


@router.message(RequestStates.awaiting_text, F.text == "Сменить организацию")
async def request_mode_change_org(message: Message, state: FSMContext) -> None:
    await org_command(message, state)


@router.message(RequestStates.awaiting_text, F.text == "Выйти")
async def request_mode_exit(message: Message, state: FSMContext) -> None:
    await state.clear()
    data = _default_request_state(None)
    await _save_request_state(message.from_user.id, data)
    await message.answer("Режим заявки завершен.", reply_markup=main_menu_keyboard())


@router.message(RequestStates.awaiting_text, F.text == "Добавить в заказ")
async def request_mode_add_to_order(message: Message) -> None:
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        org_id = await _resolve_org_for_user(session, message.from_user.id, user)
        data = await _load_request_state(message.from_user.id, org_id)
        if not user:
            data["status"] = "Статус: выполните вход"
            await _render_request_cards(message, data, is_admin=_is_admin_tg_id(message.from_user.id))
            return
        orders = await list_orders_for_user(session, user.id)
    active = [o for o in orders if o.status not in {"shipped", "cancelled"}]
    offset = int(data.get("orders_offset") or 0)
    offset = max(0, min(offset, max(0, len(active) - 5)))
    data["mode"] = "choose_order"
    data["orders_offset"] = offset
    data["orders_page"] = [
        {"id": o.id, "status": o.status, "created_at": str(getattr(o, "created_at", "") or "")}
        for o in active[offset: offset + 5]
    ]
    data["status"] = "Статус: выберите заказ"
    await _render_request_cards(message, data, is_admin=_is_admin_tg_id(message.from_user.id))


@router.callback_query(F.data.startswith("rm:"))
async def request_mode_callback(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    command = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""
    value2 = parts[3] if len(parts) > 3 else ""

    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        org_id = await _resolve_org_for_user(session, callback.from_user.id, user)
        data = await _load_request_state(callback.from_user.id, org_id)

        if command in {"start"}:
            data["mode"] = "start"
            data["status"] = "Статус: ожидаю сообщение…"
        elif command in {"new"}:
            data["mode"] = "draft"
            data["status"] = f"Статус: заявка #{callback.from_user.id} создана"
            data["items"] = []
            data["clarify_expanded"] = False
        elif command in {"orders"} and value in {"next", "prev"} and value2.isdigit():
            if not user:
                await callback.answer("Сначала выполните вход")
                return
            orders = await list_orders_for_user(session, user.id)
            active = [o for o in orders if o.status not in {"shipped", "cancelled"}]
            offset = max(0, min(int(value2), max(0, len(active) - 5)))
            data["mode"] = "choose_order"
            data["orders_offset"] = offset
            data["orders_page"] = [{"id": o.id, "status": o.status} for o in active[offset: offset + 5]]
        elif command in {"orders", "choose_order"}:
            if not user:
                await callback.answer("Сначала выполните вход")
                return
            orders = await list_orders_for_user(session, user.id)
            active = [o for o in orders if o.status not in {"shipped", "cancelled"}]
            data["mode"] = "choose_order"
            data["orders_offset"] = 0
            data["orders_page"] = [{"id": o.id, "status": o.status} for o in active[:5]]
            data["status"] = "Статус: выберите заказ"
        elif command == "pick_order" and value.isdigit():
            data["selected_order_id"] = int(value)
            data["mode"] = "draft"
            data["status"] = f"Статус: выбран заказ #{value}"
        elif command == "item" and value.isdigit():
            idx = int(value)
            items = data.get("items") if isinstance(data.get("items"), list) else []
            if 0 <= idx < len(items) and isinstance(items[idx], dict):
                item = items[idx]
                item["status"] = "needs_clarification" if item.get("status") == "ok" else "ok"
            data["current_clarify_index"] = idx
        elif command == "items" and value in {"next", "prev"} and value2.isdigit():
            data["items_page_offset"] = max(0, int(value2))
        elif command == "clarify" and value == "toggle":
            data["clarify_expanded"] = not bool(data.get("clarify_expanded"))
        elif command == "clarify" and value in {"next", "prev"} and value2.isdigit():
            idx = int(data.get("current_clarify_index", data.get("selected_item_index") or 0))
            items = data.get("items") if isinstance(data.get("items"), list) else []
            if 0 <= idx < len(items) and isinstance(items[idx], dict):
                item = items[idx]
                base_query = str(item.get("raw") or "")
                payload = await run_search_pipeline(
                    session,
                    org_id=org_id,
                    user_id=user.id if user else None,
                    text=base_query,
                    clarify_offset=int(value2),
                    enable_llm_narrow=False,
                    enable_llm_rewrite=False,
                    enable_rerank=False,
                )
                item["clarification"] = (payload.get("decision") or {}).get("clarification")
                logger.info(
                    "clarify render: reason=%s total=%s offset=%s",
                    ((item.get("clarification") or {}).get("reason") if isinstance(item.get("clarification"), dict) else None),
                    ((item.get("clarification") or {}).get("total") if isinstance(item.get("clarification"), dict) else None),
                    ((item.get("clarification") or {}).get("offset") if isinstance(item.get("clarification"), dict) else None),
                )
        elif command == "clarify" and value == "choose":
            opt_value = value2
            idx = int(data.get("current_clarify_index", data.get("selected_item_index") or 0))
            items = data.get("items") if isinstance(data.get("items"), list) else []
            if 0 <= idx < len(items) and isinstance(items[idx], dict):
                item = items[idx]
                clarification = item.get("clarification") if isinstance(item.get("clarification"), dict) else {}
                options = clarification.get("options") if isinstance(clarification.get("options"), list) else []
                selected = next((o for o in options if isinstance(o, dict) and str(o.get("id") or "") == opt_value), None)
                if selected is None and opt_value.isdigit():
                    ii = int(opt_value) - 1
                    if 0 <= ii < len(options) and isinstance(options[ii], dict):
                        selected = options[ii]
                if selected:
                    apply = selected.get("apply") if isinstance(selected.get("apply"), dict) else {}
                    if isinstance(apply.get("set_query"), str) and apply.get("set_query").strip():
                        next_query = str(apply.get("set_query")).strip()
                    else:
                        next_query = _apply_clarification_tokens(str(item.get("raw") or ""), apply.get("append_tokens"))
                    payload = await run_search_pipeline(
                        session,
                        org_id=org_id,
                        user_id=user.id if user else None,
                        text=next_query,
                        clarify_offset=0,
                        enable_llm_narrow=False,
                        enable_llm_rewrite=False,
                        enable_rerank=False,
                    )
                    results = payload.get("results") if isinstance(payload.get("results"), list) else []
                    if results:
                        item["status"] = "ok"
                        item["result_title"] = str(results[0].get("title_ru") or "")
                        item["clarification"] = None
                    else:
                        item["status"] = "needs_clarification"
                        item["clarification"] = (payload.get("decision") or {}).get("clarification")
            data["clarify_expanded"] = False
        elif command == "clarify" and value == "skip":
            idx = int(data.get("current_clarify_index", data.get("selected_item_index") or 0))
            items = data.get("items") if isinstance(data.get("items"), list) else []
            if 0 <= idx < len(items) and isinstance(items[idx], dict):
                items[idx]["status"] = "manager"
                items[idx]["result_title"] = "ждёт менеджера"
            data["clarify_expanded"] = False
        elif command == "confirm":
            items = data.get("items") if isinstance(data.get("items"), list) else []
            ok_count = len([i for i in items if isinstance(i, dict) and i.get("status") == "ok"])
            bad_count = len(items) - ok_count
            data["status"] = f"Статус: ✅ добавлено {ok_count}; ❌ осталось {bad_count}"
            data["mode"] = "start"
            data["clarify_expanded"] = False
        elif command == "cancel":
            data = _default_request_state(org_id)
        elif command == "manager_all":
            items = data.get("items") if isinstance(data.get("items"), list) else []
            for item in items:
                if isinstance(item, dict):
                    item["status"] = "manager"
                    item["result_title"] = "ждёт менеджера"
            data["mode"] = "start"
            data["status"] = "Статус: передано менеджеру"
            data["clarify_expanded"] = False
        elif command == "questions" and value in {"manager", "template"}:
            data["status"] = "Статус: вопросы обработаны"
        elif command in {"org", "change_org"}:
            await callback.answer("Введите /org для смены организации")
            return
        elif command == "exit":
            data = _default_request_state(org_id)

    await _render_request_cards(callback.message, data, is_admin=_is_admin_tg_id(callback.from_user.id))
    await callback.answer()


@router.message(RequestStates.awaiting_text, F.text)
async def request_mode_text(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    if not txt:
        return
    lowered = txt.lower()
    if lowered in {"добавить в заказ", "выйти", "сменить организацию", "создать новую заявку"}:
        return
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        org_id = await _resolve_org_for_user(session, message.from_user.id, user)
        data = await _load_request_state(message.from_user.id, org_id)
        data["mode"] = "draft"
        data["status"] = "Статус: 🔎 Ищу…"
        await _render_request_cards(message, data, is_admin=_is_admin_tg_id(message.from_user.id))

        intent_result = await route_message(txt)
        actions = intent_result.get("actions", []) if isinstance(intent_result, dict) else []
        add_actions = [a for a in actions if isinstance(a, dict) and a.get("type") == "ADD_ITEM"]
        question_actions = [a for a in actions if isinstance(a, dict) and a.get("type") != "ADD_ITEM"]
        if not add_actions:
            parsed = parse_order_text(txt)
            add_actions = [{"query_core": (p.get("query") or p.get("raw") or ""), "qty": p.get("qty"), "unit": p.get("unit")} for p in parsed]

        data["status"] = "Статус: 🧹 Нормализую…"
        await _render_request_cards(message, data, is_admin=_is_admin_tg_id(message.from_user.id))

        items: list[dict[str, object]] = []
        for action in add_actions[:20]:
            q = str(action.get("query_core") or "").strip()
            if not q:
                continue
            payload = await run_search_pipeline(
                session,
                org_id=org_id,
                user_id=user.id if user else None,
                text=q,
                clarify_offset=0,
                enable_llm_narrow=False,
                enable_llm_rewrite=False,
                enable_rerank=False,
            )
            results = payload.get("results") if isinstance(payload.get("results"), list) else []
            decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
            items.append(
                {
                    "raw": q,
                    "qty": action.get("qty"),
                    "unit": action.get("unit"),
                    "status": "ok" if results else "needs_clarification",
                    "selected": True,
                    "result_title": str(results[0].get("title_ru") or "") if results else "требуется уточнение",
                    "clarification": decision.get("clarification") if isinstance(decision.get("clarification"), dict) else None,
                }
            )

        first_bad = next((i for i, item in enumerate(items) if isinstance(item, dict) and item.get("status") != "ok"), 0)
        data["items"] = items
        data["current_clarify_index"] = first_bad
        data["selected_item_index"] = first_bad
        data["items_page_offset"] = 0
        data["clarify_expanded"] = False
        data["questions"] = [str(a.get("subject") or a.get("query_core") or "вопрос").strip() for a in question_actions[:3] if isinstance(a, dict)]
        data["mode"] = "review"
        data["status"] = "Статус: ✅ Готово"
        await _render_request_cards(message, data, is_admin=_is_admin_tg_id(message.from_user.id))
    await state.set_state(RequestStates.awaiting_text)



@router.message(F.text)
async def handle_text_order(message: Message) -> None:
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните вход.")
            return
        result = await session.execute(select(User).options(selectinload(User.org_memberships)).where(User.id == user.id))
        user = result.scalar_one()
        resolved_org_id = await _resolve_org_for_user(session, message.from_user.id, user)

        status_msg = await message.answer("🔎 Ищу позиции…")
        await status_msg.edit_text("🧹 Нормализую запрос…")
        intent_result = await route_message(message.text)
        actions = intent_result.get("actions", []) if isinstance(intent_result, dict) else []
        add_actions = [a for a in actions if isinstance(a, dict) and a.get("type") == "ADD_ITEM"]
        eta_actions = [a for a in actions if isinstance(a, dict) and a.get("type") == "ASK_STOCK_ETA"]
        if add_actions:
            lines: list[str] = []
            for idx, action in enumerate(add_actions[:5], start=1):
                action_query = str(action.get("query_core") or "").strip()
                if not action_query:
                    continue
                pipeline_result = await run_search_pipeline(
                    session,
                    org_id=resolved_org_id,
                    user_id=user.id,
                    text=action_query,
                    limit=5,
                    enable_llm_narrow=False,
                    enable_llm_rewrite=False,
                    enable_rerank=False,
                )
                stage_candidates = pipeline_result.get("results", []) if isinstance(pipeline_result, dict) else []
                decision_payload = pipeline_result.get("decision", {}) if isinstance(pipeline_result, dict) else {}
                clarification = decision_payload.get("clarification") if isinstance(decision_payload, dict) else None
                qty = action.get("qty")
                unit = action.get("unit")
                unit_suffix = f" {unit}" if isinstance(unit, str) and unit else ""
                qty_suffix = f" (qty: {int(qty) if isinstance(qty, (int, float)) else qty}{unit_suffix})" if qty else ""
                if stage_candidates:
                    top = stage_candidates[0]
                    lines.append(
                        f"{idx}. {action_query}{qty_suffix} → {top.get('title_ru')} (SKU: {top.get('sku')})"
                    )
                elif isinstance(clarification, dict) and clarification.get("options"):
                    question = str(clarification.get("question") or "Уточни вариант:")
                    options = clarification.get("options") or []
                    if isinstance(options, list) and options:
                        logger.info(
                            "clarify render: org_id=%s q=%s total=%s offset=%s reason=%s options_count=%s next=%s prev=%s",
                            decision_payload.get("history_org_id"),
                            action_query,
                            clarification.get("total"),
                            clarification.get("offset"),
                            clarification.get("reason"),
                            len(options),
                            clarification.get("next_offset"),
                            clarification.get("prev_offset"),
                        )
                        sent = await message.answer(question, reply_markup=_clarify_keyboard(clarification))
                        redis_client = _redis_client()
                        if redis_client:
                            cache_key = _candidate_cache_key(message.from_user.id, sent.message_id)
                            await redis_client.setex(
                                cache_key,
                                _CANDIDATES_TTL_SECONDS,
                                json.dumps(
                                    {
                                        "org_id": decision_payload.get("history_org_id"),
                                        "user_id": user.id,
                                        "base_query": action_query,
                                        "clarification": clarification,
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                    lines.append(f"{idx}. {action_query}{qty_suffix} → требуется уточнение")
                else:
                    lines.append(f"{idx}. {action_query}{qty_suffix} → не нашли, уточни товар/артикул")
            if eta_actions:
                eta_query = str(eta_actions[0].get("subject") or eta_actions[0].get("query_core") or "").strip()
                lines.append(await get_stock_eta(eta_query))
            if lines:
                await message.answer("Результат обработки:\n" + "\n".join(lines))
                return
        if eta_actions:
            eta_query = str(eta_actions[0].get("subject") or eta_actions[0].get("query_core") or "").strip()
            await status_msg.edit_text("✅ Готово")
            await message.answer(await get_stock_eta(eta_query))
            return
        parsed_items = parse_order_text(message.text)
        handler_result = handle_request_message(
            message.text,
            DialogContext(last_state=None, last_items=[], topic="unknown"),
        )
        logger.info("Request handler result: %s", handler_result.model_dump())
        item = parsed_items[0] if parsed_items else {}
        fallback_query = item.get("query") or item.get("raw") or ""
        primary_query = handler_result.items[0].normalized if handler_result.items else fallback_query
        search_query = (item.get("query") or "").strip() or (primary_query or "").strip() or fallback_query
        query = search_query
        history_org_id: int | None = None
        history_candidates_count = 0
        history_used = False
        history_query_used: str | None = None
        history_candidates_found = 0
        alias_candidates_count = 0
        alias_used = False
        alias_query_used: str | None = None
        alias_candidates_found = 0
        candidates: list[dict[str, object]] = []
        if parsed_items:
            result = await session.execute(
                select(OrgMember).where(OrgMember.user_id == user.id, OrgMember.status == "active")
            )
            membership = result.scalars().first()
            history_org_id = membership.org_id if membership else None
            if history_org_id:
                alias_product_ids = await find_org_alias_candidates(session, history_org_id, search_query, limit=5)
                alias_candidates_count = len(alias_product_ids)
                if alias_product_ids:
                    candidates = await search_products(
                        session,
                        search_query,
                        limit=5,
                        product_ids=alias_product_ids,
                    )
                    if candidates:
                        alias_used = True
                        alias_query_used = search_query
                        alias_candidates_found = len(candidates)
            if history_org_id and not candidates:
                history_candidate_ids = await get_org_candidates(session, history_org_id, limit=200)
                history_candidates_count = len(history_candidate_ids)
                if history_candidate_ids:
                    candidates = await search_products(
                        session,
                        search_query,
                        limit=5,
                        product_ids=history_candidate_ids,
                    )
                    if candidates:
                        history_used = True
                        history_query_used = search_query
                        history_candidates_found = len(candidates)
        if parsed_items and not candidates:
            candidates = await search_products(session, search_query, limit=5)
        candidates_count = len(candidates)
        decision = (
            "alias_ok"
            if alias_used
            else ("history_ok" if history_used else ("local_ok" if candidates_count > 0 else "needs_llm"))
        )
        alternatives: list[str] = []
        used_alternative: str | None = None
        if not parsed_items:
            await message.answer("Не удалось разобрать сообщение. Напишите, что нужно.")
            await _persist_search_log(
                session,
                user.id,
                message.text,
                _build_search_log_payload(
                    parsed_items,
                    query,
                    alternatives,
                    used_alternative,
                    0,
                    "needs_manager",
                ),
                [],
            )
            return
        category_ids: list[int] = []
        llm_narrow_confidence: float | None = None
        llm_narrow_reason: str | None = None
        narrowed_query: str | None = None
        if not candidates:
            alternatives = await suggest_queries(search_query or message.text)
            for alternative in alternatives:
                retry_candidates = await search_products(session, alternative, limit=5)
                if retry_candidates:
                    candidates = retry_candidates
                    candidates_count = len(candidates)
                    decision = "llm_ok"
                    used_alternative = alternative
                    break
            if not candidates:
                narrowed_query = search_query or message.text
                narrow_result = await narrow_categories(narrowed_query, session)
                category_ids = narrow_result.get("category_ids", [])
                llm_narrow_confidence = narrow_result.get("confidence")
                llm_narrow_reason = narrow_result.get("reason")
                if category_ids:
                    retry_candidates = await search_products(
                        session,
                        search_query,
                        limit=5,
                        category_ids=category_ids,
                    )
                    if retry_candidates:
                        candidates = retry_candidates
                        candidates_count = len(candidates)
                        decision = "llm_narrow_ok"
                    else:
                        for alternative in alternatives:
                            retry_candidates = await search_products(
                                session,
                                alternative,
                                limit=5,
                                category_ids=category_ids,
                            )
                            if retry_candidates:
                                candidates = retry_candidates
                                candidates_count = len(candidates)
                                decision = "llm_narrow_ok"
                                used_alternative = alternative
                                break
                        if not candidates:
                            decision = "needs_manager"
                else:
                    decision = "needs_manager"
        log_payload = _build_search_log_payload(
            parsed_items,
            query,
            alternatives,
            used_alternative,
            candidates_count,
            decision,
            category_ids=category_ids,
            llm_narrow_confidence=llm_narrow_confidence,
            llm_narrow_reason=llm_narrow_reason,
            narrowed_query=narrowed_query,
            history_org_id=history_org_id,
            history_candidates_count=history_candidates_count,
            history_used=history_used,
            history_query_used=history_query_used,
            history_candidates_found=history_candidates_found,
            alias_candidates_count=alias_candidates_count,
            alias_used=alias_used,
            alias_query_used=alias_query_used,
            alias_candidates_found=alias_candidates_found,
        )
        logger.info("Search decision: %s", log_payload)
        rerank_used = False
        rerank_best_ids: list[int] = []
        rerank_top_score: float | None = None
        rerank_candidates = [
            {
                "product_id": candidate.get("id"),
                "title": candidate.get("title_ru"),
                "category": None,
                "price": candidate.get("price"),
                "stock": candidate.get("stock_qty"),
            }
            for candidate in candidates
        ]
        rerank_attrs = None
        if handler_result.items:
            rerank_attrs = handler_result.items[0].attributes
        if len(rerank_candidates) >= 2:
            rerank = await rerank_products(search_query or fallback_query, rerank_candidates, rerank_attrs)
            best = rerank.get("best") if isinstance(rerank, dict) else None
            if isinstance(best, list) and best:
                rerank_used = True
                rerank_best_ids = [item.get("product_id") for item in best if isinstance(item, dict)]
                rerank_best_ids = [pid for pid in rerank_best_ids if isinstance(pid, int)]
                rerank_top_score = best[0].get("score") if isinstance(best[0], dict) else None
                score_by_id = {item["product_id"]: item.get("score", 0.0) for item in best if "product_id" in item}
                candidates.sort(
                    key=lambda item: (
                        score_by_id.get(item.get("id"), -1),
                        item.get("score", 0),
                    ),
                    reverse=True,
                )
        log_payload = {
            **log_payload,
            "rerank_used": rerank_used,
            "rerank_best_ids": rerank_best_ids,
            "rerank_top_score": rerank_top_score,
        }
        autolearn_attempted = False
        autolearn_applied = False
        autolearn_alias: str | None = None
        autolearn_product_id: int | None = None
        if history_org_id and candidates and decision != "needs_manager":
            if len(candidates) == 1 or (rerank_top_score is not None and rerank_top_score >= 0.85):
                autolearn_attempted = True
                alias_text = (
                    narrowed_query or search_query or message.text
                )
                product_id = candidates[0].get("id")
                if isinstance(product_id, int):
                    autolearn_applied = await autolearn_org_alias(
                        session,
                        history_org_id,
                        alias_text,
                        product_id,
                    )
                    if autolearn_applied:
                        autolearn_alias = normalize_alias_for_autolearn(alias_text)[:60]
                        autolearn_product_id = product_id
        log_payload = {
            **log_payload,
            "autolearn_attempted": autolearn_attempted,
            "autolearn_applied": autolearn_applied,
            "autolearn_alias": autolearn_alias,
            "autolearn_product_id": autolearn_product_id,
        }
        await _persist_search_log(session, user.id, message.text, log_payload, candidates)
        lines = [f"{idx}. {c['title_ru']} (SKU: {c['sku']})" for idx, c in enumerate(candidates, start=1)]
        redis_client = _redis_client()
        reply_markup = None
        if len(candidates) > 1 and history_org_id and redis_client:
            titles = [c.get("title_ru") or c.get("title") or "" for c in candidates]
            reply_markup = _alias_keyboard(titles)
        sent = await message.answer("Вот что нашлось:\n" + "\n".join(lines), reply_markup=reply_markup)
        if len(candidates) > 1 and history_org_id and redis_client:
            cache_key = _candidate_cache_key(message.from_user.id, sent.message_id)
            payload = {
                "org_id": history_org_id,
                "alias_text": message.text,
                "products": [c.get("id") for c in candidates if isinstance(c.get("id"), int)],
            }
            await redis_client.setex(cache_key, _CANDIDATES_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
        return
    await message.answer("Не удалось обработать запрос.")


async def _notify_manager(message: Message, user: User) -> None:
    async with get_session_context() as session:
        manager = await get_user_by_phone(session, settings.manager_phone)
    if manager and manager.tg_id:
        await message.bot.send_message(
            manager.tg_id,
            f"Новое сообщение от {user.fio} ({user.phone}): {message.text}",
        )


async def _persist_search_log(
    session: AsyncSession,
    user_id: int | None,
    text: str,
    log_payload: dict[str, object],
    candidates: list[dict[str, object]],
) -> None:
    try:
        await create_search_log(
            session,
            user_id,
            text,
            parsed_json=json.dumps(log_payload, ensure_ascii=False),
            selected_json=json.dumps(candidates, ensure_ascii=False),
            confidence=0.0,
        )
        await session.commit()
    except Exception:
        logger.warning("Failed to persist search log", exc_info=True)
        await session.rollback()


def _build_search_log_payload(
    parsed_items: list[dict[str, object]],
    original_query: str,
    alternatives: list[str],
    used_alternative: str | None,
    candidates_count_final: int,
    decision: str,
    category_ids: list[int] | None = None,
    llm_narrow_confidence: float | None = None,
    llm_narrow_reason: str | None = None,
    narrowed_query: str | None = None,
    history_org_id: int | None = None,
    history_candidates_count: int = 0,
    history_used: bool = False,
    history_query_used: str | None = None,
    history_candidates_found: int = 0,
    alias_candidates_count: int = 0,
    alias_used: bool = False,
    alias_query_used: str | None = None,
    alias_candidates_found: int = 0,
) -> dict[str, object]:
    return {
        "parsed_items": parsed_items,
        "original_query": original_query,
        "alternatives": alternatives,
        "used_alternative": used_alternative,
        "candidates_count_final": candidates_count_final,
        "decision": decision,
        "category_ids": category_ids or [],
        "llm_narrow_confidence": llm_narrow_confidence,
        "llm_narrow_reason": llm_narrow_reason,
        "narrowed_query": narrowed_query,
        "history_org_id": history_org_id,
        "history_candidates_count": history_candidates_count,
        "history_used": history_used,
        "history_query_used": history_query_used,
        "history_candidates_found": history_candidates_found,
        "alias_candidates_count": alias_candidates_count,
        "alias_used": alias_used,
        "alias_query_used": alias_query_used,
        "alias_candidates_found": alias_candidates_found,
    }


@router.message(F.text == "Восстановить данные")
async def recover_stub(message: Message) -> None:
    await message.answer("Функция восстановления данных будет добавлена позже.")


@router.message(F.text == "Запросить доступ")
async def access_request_stub(message: Message) -> None:
    await message.answer("Для запроса доступа свяжитесь с менеджером или администратором.")
