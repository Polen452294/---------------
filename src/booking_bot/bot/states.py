from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    selecting_service = State()
    selecting_date = State()
    selecting_slot = State()
    waiting_phone = State()
    confirming = State()
    rescheduling_date = State()
    rescheduling_slot = State()
    rescheduling_confirming = State()


class MasterStates(StatesGroup):
    waiting_weekday_hours = State()
    waiting_time_block = State()
    waiting_extra_day = State()
    waiting_service_create_name = State()
    waiting_service_name = State()
    waiting_service_description = State()
    waiting_service_duration = State()
    waiting_service_price = State()
    waiting_service_buffers = State()
