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
        ],
        resize_keyboard=True,
    )


def registration_done_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Регистрация завершена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def catalog_keyboard(categories: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"cat:{cat_id}")] for cat_id, name in categories]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def products_keyboard(products: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"prod:{prod_id}")] for prod_id, name in products]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
