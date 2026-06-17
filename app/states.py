from aiogram.fsm.state import State, StatesGroup


class ShopReceipt(StatesGroup):
    waiting_discount = State()
    waiting_receipt = State()


class QuestionSubmit(StatesGroup):
    text = State()
    option1 = State()
    option2 = State()
    option3 = State()
    option4 = State()
    correct = State()
    genre = State()


class ReportQuestion(StatesGroup):
    reason = State()


class AdminFlow(StatesGroup):
    waiting_setting_value = State()
    waiting_user_id = State()
    waiting_user_delta = State()
    waiting_admin_id = State()
    waiting_start_photo = State()


class BulkQuestionImport(StatesGroup):
    waiting_json = State()


class ShopPackageFlow(StatesGroup):
    title = State()
    amount = State()
    price = State()
    edit_title = State()
    edit_amount = State()
    edit_price = State()


class LeagueFlow(StatesGroup):
    name = State()
    min_cups = State()
    win_cups = State()
    loss_cups = State()
    edit_name = State()
    edit_min_cups = State()
    edit_win_cups = State()
    edit_loss_cups = State()


class DiscountFlow(StatesGroup):
    code = State()
    kind = State()
    value = State()
    max_uses = State()
    expires_at = State()


class QuestionCleanupFlow(StatesGroup):
    confirm_delete_invalid = State()
