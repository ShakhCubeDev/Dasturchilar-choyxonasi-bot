from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    group = State()
    language = State()
    phone = State()
    name = State()
    age = State()
    profession = State()
    experience = State()
    purpose = State()
    confirm = State()
