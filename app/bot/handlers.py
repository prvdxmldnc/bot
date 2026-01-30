import json
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from app.bot.states import LoginStates, RegistrationStates
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
from app.services.llm_gigachat import parse_order, rerank_candidates
from app.services.search import search_products
from app.utils.security import hash_password, verify_password

router = Router()
logger = logging.getLogger(__name__)


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


@router.message(F.text)
async def handle_text_order(message: Message) -> None:
    async with get_session_context() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните вход.")
            return
        result = await session.execute(select(User).options(selectinload(User.org_memberships)).where(User.id == user.id))
        user = result.scalar_one()
        parsed = await parse_order(message.text)
        items = parsed.get("items") or []
        selected = []
        low_confidence = False
        order = await get_or_create_draft_order(session, user)
        for item in items:
            candidates = await search_products(session, item.get("query") or item.get("raw") or "", limit=10)
            rerank = await rerank_candidates(item, candidates)
            best_id = rerank.get("best_id")
            confidence = float(rerank.get("confidence") or 0)
            selected.append(
                {"item": item, "best_id": best_id, "confidence": confidence, "candidates": candidates[:5]}
            )
            if not best_id or confidence < 0.78:
                low_confidence = True
                continue
            result = await session.execute(select(Product).where(Product.id == best_id))
            product = result.scalar_one_or_none()
            if product:
                await add_item_to_order(session, order, product, qty=int(item.get("qty") or 1))
        await create_search_log(
            session,
            user.id,
            message.text,
            parsed_json=json.dumps(parsed, ensure_ascii=False),
            selected_json=json.dumps(selected, ensure_ascii=False),
            confidence=max((s["confidence"] for s in selected), default=0.0),
        )
        await session.commit()
        if low_confidence:
            thread = await create_thread(
                session,
                user.org_memberships[0].org_id if user.org_memberships else None,
                "Автоподбор: требуется уточнение",
            )
            session.add(
                ThreadMessage(
                    thread_id=thread.id,
                    author_user_id=user.id,
                    author_name_snapshot=user.fio,
                    text=f"Запрос: {message.text}\nParsed: {json.dumps(parsed, ensure_ascii=False)}",
                )
            )
            await session.commit()
            await message.answer(
                "Не удалось однозначно подобрать товары — передали менеджеру. Он свяжется с вами."
            )
            await _notify_manager(message, user)
            return
        await message.answer(
            "Автоподбор выполнен. Проверьте черновик заказа и нажмите «Оформить».",
            reply_markup=main_menu_keyboard(),
        )
        await _notify_manager(message, user)
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


@router.message(F.text == "Восстановить данные")
async def recover_stub(message: Message) -> None:
    await message.answer("Функция восстановления данных будет добавлена позже.")


@router.message(F.text == "Запросить доступ")
async def access_request_stub(message: Message) -> None:
    await message.answer("Для запроса доступа свяжитесь с менеджером или администратором.")
