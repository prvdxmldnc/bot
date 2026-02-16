from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    fio = State()
    org_name = State()
    phone = State()
    address = State()
    work_time = State()
    email = State()
    password = State()


class LoginStates(StatesGroup):
    phone = State()
    password = State()


class DebugOrgStates(StatesGroup):
    awaiting_org_id = State()


class RequestStates(StatesGroup):
    menu = State()
    awaiting_text = State()
