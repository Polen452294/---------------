from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    selecting_service = State()
    selecting_master = State()
    selecting_date = State()
    selecting_slot = State()
    waiting_phone = State()
    confirming = State()


class MasterStates(StatesGroup):
    waiting_weekday_hours = State()
    waiting_time_block = State()
    waiting_extra_day = State()
