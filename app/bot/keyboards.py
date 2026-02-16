from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Регистрация"), KeyboardButton(text="Вход")],
            [KeyboardButton(text="Восстановить данные")],
            [KeyboardButton(text="Запросить доступ")],
            [KeyboardButton(text="Открыть каталог")],
        ],
        resize_keyboard=True,
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Заказы"), KeyboardButton(text="Мои вопросы")],
            [KeyboardButton(text="Баланс"), KeyboardButton(text="Пригласить работника")],
            [KeyboardButton(text="Каталог"), KeyboardButton(text="Аккаунт")],
            [KeyboardButton(text="Отправить заявку")],
        ],
        resize_keyboard=True,
    )


def registration_done_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Регистрация завершена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def auth_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Вход")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def catalog_keyboard(
    categories: list[tuple[int, str]],
    page: int,
    total_pages: int,
    prefix: str = "cat",
) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"{prefix}:{cat_id}")] for cat_id, name in categories]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}page:{page + 1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def products_keyboard(
    products: list[tuple[int, str]],
    page: int,
    total_pages: int,
    prefix: str = "prod",
    context: str | None = None,
) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"{prefix}:{prod_id}")] for prod_id, name in products]
    nav = []
    if page > 0:
        target = f"{prefix}page:{page - 1}"
        if context:
            target = f"{target}:{context}"
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=target))
    if page < total_pages - 1:
        target = f"{prefix}page:{page + 1}"
        if context:
            target = f"{target}:{context}"
        nav.append(InlineKeyboardButton(text="➡️", callback_data=target))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def product_actions_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить в заказ", callback_data=f"add:{product_id}")],
            [InlineKeyboardButton(text="Назад", callback_data="back:catalog")],
        ]
    )


def order_actions_keyboard(order_id: int, status: str) -> InlineKeyboardMarkup:
    buttons = []
    if status == "draft":
        buttons.append([InlineKeyboardButton(text="Оформить", callback_data=f"order:submit:{order_id}")])
        buttons.append([InlineKeyboardButton(text="Отменить", callback_data=f"order:cancel:{order_id}")])
    buttons.append([InlineKeyboardButton(text="Задать вопрос", callback_data=f"order:question:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
