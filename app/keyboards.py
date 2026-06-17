from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

MAIN_MENU_TEXTS = {
    "⚔️ دوئل", "🏆 لیدربورد", "🛒 فروشگاه", "👤 پروفایل",
    "➕ ثبت سوال", "🎁 رفرال", "🛡 پنل ادمین", "🏰 کلن (به‌زودی)",
}
CANCEL_TEXT = "↩️ انصراف / برگشت"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="⚔️ دوئل")],
        [KeyboardButton(text="🛒 فروشگاه"), KeyboardButton(text="🏆 لیدربورد")],
        [KeyboardButton(text="👤 پروفایل"), KeyboardButton(text="➕ ثبت سوال")],
        [KeyboardButton(text="🎁 رفرال"), KeyboardButton(text="🏰 کلن (به‌زودی)")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛡 پنل ادمین")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=CANCEL_TEXT)]], resize_keyboard=True)


def back_home_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="↩️ بازگشت به منوی اصلی", callback_data="nav:home")
    return b.as_markup()


def duel_menu(random_cost: int = 5, friendly_cost: int = 20) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🎲 دوئل شانسی — {random_cost} سکه", callback_data="duel:random")
    b.button(text=f"🤝 دعوت دوست — {friendly_cost} سکه", callback_data="duel:invite")
    b.button(text="↩️ برگشت", callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


def waiting_queue_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ خروج از صف و برگشت سکه", callback_data=f"duel:cancel_queue:{duel_id}")
    b.button(text="↩️ منوی اصلی", callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


def genres_keyboard(duel_id: int, genres: list[str], selected: set[str], max_count: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for g in genres:
        mark = "✅ " if g in selected else ""
        b.button(text=f"{mark}{g}", callback_data=f"genre:{duel_id}:{g}")
    b.button(text=f"تایید انتخاب ({len(selected)}/{max_count})", callback_data=f"genre_done:{duel_id}")
    b.adjust(2, 2, 1)
    return b.as_markup()


def question_keyboard(duel_id: int, qid: int, options: list[str], hidden: set[int] | None = None, cost_5050: int = 0, cost_hint: int = 0) -> InlineKeyboardMarkup:
    hidden = hidden or set()
    b = InlineKeyboardBuilder()
    for i, opt in enumerate(options, 1):
        if i in hidden:
            b.button(text="❌ حذف شد", callback_data="noop")
        else:
            b.button(text=f"{i}. {opt}", callback_data=f"ans:{duel_id}:{qid}:{i}")
    b.button(text=f"50:50 — {cost_5050} سکه", callback_data=f"power:5050:{duel_id}:{qid}")
    b.button(text=f"💡 کمک — {cost_hint} سکه", callback_data=f"power:hint:{duel_id}:{qid}")
    b.button(text="🚩 گزارش سوال", callback_data=f"report:{duel_id}:{qid}")
    b.adjust(1, 1, 1, 1, 2, 1)
    return b.as_markup()


def leaderboard_basis_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="بر اساس سطح", callback_data="lb_basis:level")
    b.button(text="بر اساس لیگ", callback_data="lb_basis:league")
    b.button(text="↩️ برگشت", callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


def leaderboard_period_keyboard(basis: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="روزانه", callback_data=f"lb:{basis}:daily")
    b.button(text="ماهانه", callback_data=f"lb:{basis}:monthly")
    b.button(text="کلی", callback_data=f"lb:{basis}:all")
    b.button(text="↩️ برگشت", callback_data="lb_back:basis")
    b.adjust(3, 1)
    return b.as_markup()


def shop_sections_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🪙 بسته‌های سکه", callback_data="shop_section:coins")
    b.button(text="⭐ بسته‌های سطح/XP", callback_data="shop_section:xp")
    b.button(text="↩️ برگشت", callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


def shop_keyboard(packages, package_type: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in packages:
        b.button(text=f"{p['title']} — {p['price_label']}", callback_data=f"shop:{p['id']}")
    b.button(text="↩️ برگشت به بخش‌ها", callback_data="shop_back:sections")
    b.adjust(1)
    return b.as_markup()


def review_tx_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تایید رسید", callback_data=f"tx:approve:{tx_id}"),
        InlineKeyboardButton(text="❌ رد رسید", callback_data=f"tx:reject:{tx_id}"),
    ]])


def review_question_keyboard(qid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تایید سوال", callback_data=f"qrev:approve:{qid}"),
        InlineKeyboardButton(text="❌ رد سوال", callback_data=f"qrev:reject:{qid}"),
    ]])


def admin_panel() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for text, data in [
        ("📊 آمار", "admin:stats"), ("⚙️ تنظیمات", "admin:settings"),
        ("👤 جستجوی کاربر", "admin:user_search"), ("➕ ادمین", "admin:add_admin"),
        ("➖ حذف ادمین", "admin:remove_admin"), ("💾 بک‌آپ", "admin:backup"),
        ("📥 افزودن Bulk سوال", "admin:bulk_questions"), ("🛒 مدیریت فروشگاه", "admin:shop_manage"),
        ("🎟 کدهای تخفیف", "admin:discounts"), ("🏆 مدیریت لیگ/تیر", "admin:leagues"),
        ("🛠 تغییر حالت تعمیر", "admin:maintenance_toggle"), ("🖼 عکس استارت", "admin:start_photo"),
        ("❓ مدیریت سوالات", "admin:question_manage"),
        ("🧹 پاکسازی ژانر نامعتبر", "admin:question_cleanup"),
    ]:
        b.button(text=text, callback_data=data)
    b.adjust(2)
    return b.as_markup()


def settings_keyboard(settings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in settings:
        b.button(text=f"{s['key']} = {s['value']}", callback_data=f"set:{s['key']}")
    b.button(text="↩️ برگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()


def user_admin_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="تغییر سکه", callback_data=f"ucoin:{tg_id}")
    b.button(text="تغییر XP", callback_data=f"uxp:{tg_id}")
    b.button(text="مسدود/آزاد", callback_data=f"ublock:{tg_id}")
    b.button(text="↩️ برگشت", callback_data="admin:back")
    b.adjust(2, 1, 1)
    return b.as_markup()


def admin_shop_types_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🪙 بسته‌های سکه", callback_data="ashop:list:coins")
    b.button(text="⭐ بسته‌های XP", callback_data="ashop:list:xp")
    b.button(text="🎟 مدیریت کد تخفیف", callback_data="admin:discounts")
    b.button(text="↩️ برگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()


def admin_shop_packages_keyboard(packages, package_type: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ افزودن بسته", callback_data=f"ashop:add:{package_type}")
    for p in packages:
        b.button(text=f"✏️ {p['title']} — {p['price_label']}", callback_data=f"ashop:edit:{p['id']}")
        b.button(text=f"🗑 حذف #{p['id']}", callback_data=f"ashop:delete:{p['id']}")
    b.button(text="↩️ برگشت", callback_data="admin:shop_manage")
    b.adjust(1)
    return b.as_markup()


def admin_shop_edit_keyboard(package_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="ویرایش نام", callback_data=f"ashop_edit:title:{package_id}")
    b.button(text="ویرایش مقدار", callback_data=f"ashop_edit:amount:{package_id}")
    b.button(text="ویرایش قیمت", callback_data=f"ashop_edit:price:{package_id}")
    b.button(text="↩️ برگشت", callback_data="admin:shop_manage")
    b.adjust(1)
    return b.as_markup()


def admin_leagues_keyboard(leagues) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ افزودن لیگ", callback_data="league:add")
    for lg in leagues:
        b.button(text=f"✏️ {lg['name']} | cup≥{lg['min_cups']} | +{lg['win_cups']}/{lg['loss_cups']}", callback_data=f"league:edit:{lg['id']}")
        b.button(text=f"🗑 حذف #{lg['id']}", callback_data=f"league:delete:{lg['id']}")
    b.button(text="↩️ برگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()


def admin_league_edit_keyboard(league_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="ویرایش نام", callback_data=f"league_edit:name:{league_id}")
    b.button(text="ویرایش آستانه کاپ", callback_data=f"league_edit:min:{league_id}")
    b.button(text="ویرایش کاپ برد", callback_data=f"league_edit:win:{league_id}")
    b.button(text="ویرایش کاپ باخت", callback_data=f"league_edit:loss:{league_id}")
    b.button(text="↩️ برگشت", callback_data="admin:leagues")
    b.adjust(1)
    return b.as_markup()


def discount_apply_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎟 وارد کردن کد تخفیف", callback_data=f"discount_apply:{tx_id}")
    b.button(text="ادامه بدون تخفیف", callback_data=f"pay:start:{tx_id}")
    b.button(text="↩️ انصراف", callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


def payment_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="پرداخت کارت‌به‌کارت و ارسال رسید", callback_data=f"pay:start:{tx_id}")
    b.button(text="↩️ برگشت", callback_data="shop_back:sections")
    b.adjust(1)
    return b.as_markup()


def admin_discounts_keyboard(discounts) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ افزودن کد تخفیف", callback_data="discount:add")
    for d in discounts:
        status = "فعال" if d["is_active"] else "غیرفعال"
        b.button(text=f"🗑 {d['code']} | {d['discount_type']} {d['value']} | {status}", callback_data=f"discount:disable:{d['id']}")
    b.button(text="↩️ برگشت", callback_data="admin:shop_manage")
    b.adjust(1)
    return b.as_markup()


def discount_kind_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="درصدی", callback_data="discount_kind:percent")
    b.button(text="مبلغ ثابت", callback_data="discount_kind:fixed")
    b.adjust(2)
    return b.as_markup()


def admin_leagues_keyboard(leagues) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for lg in leagues:
        label = f"✏️ {lg['name']} | cup≥{lg['min_cups']} | +{lg['win_cups']}/{lg['loss_cups']}"
        b.button(text=label, callback_data=f"league:edit:{lg['id']}")
    b.button(text="↩️ برگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()


def question_manage_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⏳ سوالات در صف بررسی", callback_data="qadmin_mode:pending")
    b.button(text="🔎 جستجوی سوالات تاییدشده بر اساس ژانر", callback_data="qadmin_mode:active")
    b.button(text="↩️ برگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()


def question_genres_keyboard(genres, mode: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for g, c in genres:
        b.button(text=f"{g} ({c})", callback_data=f"qadmin:genre:{mode}:{g}")
    b.button(text="↩️ برگشت", callback_data="admin:question_manage")
    b.adjust(1)
    return b.as_markup()


def pending_questions_keyboard(questions, genre: str, mode: str = "pending") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for q in questions:
        status = "⏳" if q['status'] == 'pending' else "✅"
        b.button(text=f"{status} #{q['id']} {q['text'][:35]}", callback_data=f"qadmin:view:{q['id']}")
    b.button(text="↩️ ژانرها", callback_data=f"qadmin_mode:{mode}")
    b.adjust(1)
    return b.as_markup()


def invalid_questions_confirm_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ تایید حذف سوالات نامعتبر", callback_data="qcleanup:confirm")
    b.button(text="❌ انصراف", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()
