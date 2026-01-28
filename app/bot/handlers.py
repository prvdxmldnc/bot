from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.keyboards import (
    catalog_keyboard,
    auth_keyboard,
    main_menu_keyboard,
    products_keyboard,
    registration_done_keyboard,
    start_keyboard,
)
from app.bot.states import LoginStates, RegistrationStates
from app.config import settings
from app.crud import (
    create_order,
    create_organization,
    create_thread,
    find_product_by_text,
    get_user_by_phone,
    get_user_by_tg_id,
    list_orders_for_user,
    list_root_categories,
    list_products_by_category,
    list_subcategories,
)
from app.database import get_session
from app.models import OrgMember, Product, Thread, User
from app.utils.security import hash_password, verify_password

router = Router()


def _normalized_text(message: Message) -> str:
    return (message.text or "").strip().lower()


def _is_login_command(message: Message) -> bool:
    return _normalized_text(message) == "вход"


def _is_registration_command(message: Message) -> bool:
    return _normalized_text(message) == "регистрация"


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
    await state.update_data(phone=message.text)
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
    data = await state.get_data()
    org_name = data["org_name"]
    async with get_session() as session:
        if await get_user_by_phone(session, data["phone"]):
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
        session.add(user)
        await session.flush()
        if org_name.lower() != "частное лицо":
            org = await create_organization(session, org_name, user.id)
            session.add(OrgMember(org_id=org.id, user_id=user.id, role_in_org="owner"))
        await session.commit()
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
    await message.answer("Введите телефон:")


@router.message(LoginStates.phone)
async def login_phone(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    await state.update_data(phone=message.text)
    await state.set_state(LoginStates.password)
    await message.answer("Введите пароль:")


@router.message(LoginStates.password)
async def login_password(message: Message, state: FSMContext) -> None:
    if await _handle_auth_interrupts(message, state):
        return
    data = await state.get_data()
    async with get_session() as session:
        user = await get_user_by_phone(session, data["phone"])
        if not user or not verify_password(message.text, user.password_hash):
            await message.answer("Неверные данные. Попробуйте снова.")
            await state.clear()
            return
        user.tg_id = message.from_user.id
        await session.commit()
    await state.clear()
    await message.answer("Вход выполнен.", reply_markup=main_menu_keyboard())


@router.message(F.text == "Каталог")
@router.message(F.text == "Открыть каталог")
async def show_catalog(message: Message) -> None:
    async with get_session() as session:
        categories = await list_root_categories(session)
    if not categories:
        await message.answer("Каталог пуст. Обратитесь к менеджеру.")
        return
    await message.answer(
        "Выберите категорию:",
        reply_markup=catalog_keyboard([(cat.id, cat.title_ru) for cat in categories]),
    )


@router.callback_query(F.data.startswith("cat:"))
async def category_click(callback) -> None:
    cat_id = int(callback.data.split(":")[1])
    async with get_session() as session:
        subcats = await list_subcategories(session, cat_id)
        products = await list_products_by_category(session, cat_id)
    if subcats:
        await callback.message.edit_text(
            "Выберите подкатегорию:",
            reply_markup=catalog_keyboard([(cat.id, cat.title_ru) for cat in subcats]),
        )
        return
    if products:
        await callback.message.edit_text(
            "Товары:",
            reply_markup=products_keyboard([(prod.id, prod.title_ru) for prod in products]),
        )
        return
    await callback.message.edit_text("В этой категории пока нет товаров.")


@router.callback_query(F.data.startswith("prod:"))
async def product_click(callback) -> None:
    prod_id = int(callback.data.split(":")[1])
    async with get_session() as session:
        result = await session.execute(select(Product).where(Product.id == prod_id))
        product = result.scalar_one_or_none()
    if not product:
        await callback.message.edit_text("Товар не найден.")
        return
    await callback.message.edit_text(
        f"{product.title_ru}\nЦена: {product.price}\nВ наличии: {product.stock_qty}\\n"
        "Чтобы заказать, отправьте название товара в чат.",
    )


@router.message(F.text == "Заказы")
async def list_orders(message: Message) -> None:
    async with get_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните вход.")
            return
        orders = await list_orders_for_user(session, user)
    if not orders:
        await message.answer("Заказов пока нет.")
        return
    lines = [f"#{order.id} — {order.status} от {order.created_at:%d.%m}" for order in orders]
    await message.answer("Ваши заказы:\n" + "\n".join(lines))


@router.message(F.text == "Мои вопросы")
async def list_questions(message: Message) -> None:
    async with get_session() as session:
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
    async with get_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
    if not user:
        await message.answer("Сначала выполните вход.")
        return
    await message.answer(
        f"ФИО: {user.fio}\nТелефон: {user.phone}\nРоль: {user.role}\n",
    )


@router.message(F.text)
async def handle_text_order(message: Message) -> None:
    async with get_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните вход.")
            return
        result = await session.execute(select(User).options(selectinload(User.org_memberships)).where(User.id == user.id))
        user = result.scalar_one()
        product = await find_product_by_text(session, message.text)
        if not product:
            await message.answer("Не удалось найти товар. Менеджер свяжется с вами.")
            return
        order = await create_order(session, user, product)
        await session.commit()
        await message.answer(
            f"Создан заказ #{order.id} на {product.title_ru}. Менеджер свяжется с вами.",
            reply_markup=main_menu_keyboard(),
        )


@router.message(F.text == "Восстановить данные")
async def recover_stub(message: Message) -> None:
    await message.answer("Функция восстановления данных будет добавлена позже.")


@router.message(F.text == "Запросить доступ")
async def access_request_stub(message: Message) -> None:
    await message.answer("Для запроса доступа свяжитесь с менеджером или администратором.")
