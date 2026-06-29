from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

MAIN_MENU_TEXTS = {
    "⚔️ دوئل", "🏆 لیدربورد", "🛒 فروشگاه", "👤 پروفایل",
    "➕ ثبت سوال", "🎁 رفرال", "🛡 پنل ادمین", "🏰 کلن (به‌زودی)",
}
CANCEL_TEXT = "↩️ انصراف / برگشت"


def main_menu(is_admin: bool = False, one_time_keyboard: bool = True) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="⚔️ دوئل")],
        [KeyboardButton(text="🛒 فروشگاه"), KeyboardButton(text="🏆 لیدربورد")],
        [KeyboardButton(text="👤 پروفایل"), KeyboardButton(text="➕ ثبت سوال")],
        [KeyboardButton(text="🎁 رفرال"), KeyboardButton(text="🏰 کلن (به‌زودی)")],
        [KeyboardButton(text="📞 تماس"), KeyboardButton(text="📘 راهنما")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛡 پنل ادمین")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=one_time_keyboard)


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
    b.adjust(1)
    return b.as_markup()


def waiting_queue_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ لغو و برگشت سکه", callback_data=f"duel:cancel_queue:{duel_id}")
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


def question_keyboard(duel_id: int, qid: int, options: list[str], hidden: set[int] | None = None, cost_remove2: int = 0, cost_auto: int = 0) -> InlineKeyboardMarkup:
    hidden = hidden or set()
    b = InlineKeyboardBuilder()
    for i, opt in enumerate(options, 1):
        if i in hidden:
            b.button(text="❌", callback_data="noop")
        else:
            b.button(text=f"{i}. {opt}", callback_data=f"ans:{duel_id}:{qid}:{i}")
    remove_text = "🔪 حذف دو گزینه — ❌" if cost_remove2 < 0 else f"🔪 حذف دو گزینه — {cost_remove2}🪙"
    auto_text = "🎯 جواب خودکار — ❌" if cost_auto < 0 else f"🎯 جواب خودکار — {cost_auto}🪙"
    b.button(text=remove_text, callback_data="noop" if cost_remove2 < 0 else f"power:remove2:{duel_id}:{qid}")
    b.button(text=auto_text, callback_data="noop" if cost_auto < 0 else f"power:auto:{duel_id}:{qid}")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def leaderboard_basis_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="بر اساس سطح", callback_data="lb_basis:level")
    b.button(text="بر اساس لیگ", callback_data="lb_basis:league")
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
        ("👥 مدیریت کاربران", "admin:user_management"),
        ("❓ مدیریت سوالات", "admin:question_management"),
        ("🎮 تنظیمات بازی", "admin:game_settings"),
        ("💰 تنظیمات اقتصادی", "admin:economy_settings"),
        ("🏆 تنظیمات لیگ و لول", "admin:league_level_settings"),
        ("📣 اعلان‌ها", "admin:notifications"),
        ("📊 آمار و گزارش", "admin:stats_reports"),
        ("🎬 پیش‌نمایش انیمیشن‌ها", "admin:animation_preview"),
        ("📁 مدیریت فایل Config", "admin:file_config"),
    ]:
        b.button(text=text, callback_data=data)
    b.adjust(1)
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


def issue_report_reasons_keyboard(duel_id: int, qid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    reasons = [
        ("جواب اشتباه است ❌", "wrong_answer"),
        ("سوال نامفهوم است ❓", "unclear"),
        ("گزینه‌ها تکراری‌اند 🔁", "duplicate_options"),
        ("سایر 📝", "other"),
    ]
    for text, code in reasons:
        b.button(text=text, callback_data=f"issue_reason:{code}:{duel_id}:{qid}")
    b.adjust(1)
    return b.as_markup()


def report_admin_keyboard(qid: int, report_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="مشاهده سوال 🔍", callback_data=f"qadmin:view:{qid}")
    b.button(text="حذف سوال 🗑", callback_data=f"qact:delete:{qid}")
    b.button(text="نادیده گرفتن ✅", callback_data=f"report_ignore:{report_id}")
    b.adjust(1)
    return b.as_markup()


def question_admin_actions_keyboard(qid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ ویرایش صورت سوال", callback_data=f"qedit:text:{qid}")
    b.button(text="🔘 ویرایش گزینه‌ها", callback_data=f"qedit:options:{qid}")
    b.button(text="🏷 ویرایش دسته‌بندی", callback_data=f"qedit:genre:{qid}")
    b.button(text="حذف 🗑", callback_data=f"qact:delete:{qid}")
    b.button(text="غیرفعال ⏸", callback_data=f"qact:disable:{qid}")
    b.button(text="🔙 بازگشت", callback_data="admin:question_management")
    b.adjust(1)
    return b.as_markup()


def question_search_results_keyboard(results, page: int, query: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for q in results:
        b.button(text=f"مشاهده و ویرایش #{q['id']}", callback_data=f"qadmin:view:{q['id']}")
    if page > 0:
        b.button(text="⬅️ قبلی", callback_data=f"qsearch:{page-1}:{query}")
    if len(results) >= 10:
        b.button(text="بعدی ➡️", callback_data=f"qsearch:{page+1}:{query}")
    b.button(text="🔙 بازگشت", callback_data="admin:question_management")
    b.adjust(1)
    return b.as_markup()


def genre_edit_keyboard(qid: int, genres: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for g in genres:
        b.button(text=g, callback_data=f"qedit_genre:{qid}:{g}")
    b.button(text="🔙 بازگشت", callback_data=f"qadmin:view:{qid}")
    b.adjust(2)
    return b.as_markup()


def result_report_keyboard(duel_id: int, qid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ گزارش مشکل سوال", callback_data=f"issue_report:{duel_id}:{qid}")
    return b.as_markup()


def titles_menu_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ لقب جدید", callback_data="title:add")
    b.button(text="📋 لیست لقب‌ها", callback_data="title:list")
    b.button(text="🗑 حذف لقب", callback_data="title:delete_help")
    b.button(text="🔙 بازگشت", callback_data="admin:league_level_settings")
    b.adjust(1)
    return b.as_markup()


def animation_preview_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎬 لول‌آپ ساده — متن A/B", callback_data="animprev:level")
    b.button(text="🎬 رنک‌آپ", callback_data="animprev:rank")
    b.button(text="🎬 لقب جدید", callback_data="animprev:title")
    b.button(text="🎬 لیگ‌داون", callback_data="animprev:down")
    b.button(text="🔙 بازگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()


def admin_submenu_keyboard(kind: str) -> InlineKeyboardMarkup:
    menus = {
        "user": [("🔍 جستجوی کاربر", "admin:user_search"), ("🪙 تغییر موجودی کاربر", "admin:user_search"), ("🚫 بن/آنبن کاربر", "admin:user_search"), ("👑 مدیریت ادمین‌ها", "admin:add_admin")],
        "question": [("📋 سوالات در انتظار تأیید", "qadmin_mode:pending"), ("🔍 جستجوی سوال با ID", "admin:question_lookup_help"), ("➕ افزودن سوال دستی", "admin:manual_question_help"), ("📤 آپلود Bulk سوال", "admin:bulk_questions"), ("⚠️ سوالات گزارش‌شده", "admin:question_cleanup")],
        "game": [("⏱ تایمر سوال", "admin:settings"), ("🎲 هزینه دوئل شانسی", "admin:settings"), ("🔋 تنظیمات پاورآپ‌ها", "admin:settings"), ("📦 تنظیمات جعبه سوال گروهی", "admin:settings"), ("⚔️ تنظیمات دوئل", "admin:settings")],
        "economy": [("🪙 سکه‌ی اولیه ثبت‌نام", "admin:settings"), ("🎁 جوایز دوئل", "admin:settings"), ("👥 جوایز رفرال", "admin:settings"), ("💎 بسته‌های جم", "admin:shop_manage"), ("🛒 آیتم‌های فروشگاه", "admin:shop_manage")],
        "league": [("📊 مدیریت لول‌ها", "admin:levels"), ("🏅 مدیریت لقب‌ها", "admin:titles"), ("🏆 مدیریت لیگ‌ها", "admin:leagues"), ("✏️ ویرایش متن‌های انیمیشن", "admin:settings")],
        "reports": [("📊 آمار", "admin:stats"), ("💾 بک‌آپ کامل", "admin:backup"), ("❓ بک‌آپ سوالات", "admin:backup_questions"), ("👥 بک‌آپ کاربران", "admin:backup_users"), ("⚙️ بک‌آپ تنظیمات", "admin:backup_settings")],
        "file": [("📤 آپلود بک‌آپ", "admin:upload_backup"), ("💾 بک‌آپ کامل", "admin:backup")],
        "notifications": [("🖼 عکس استارت", "admin:start_photo"), ("🛠 تغییر حالت تعمیر", "admin:maintenance_toggle"), ("⚙️ متن‌ها", "admin:settings")],
    }
    b = InlineKeyboardBuilder()
    for text, data in menus.get(kind, []):
        b.button(text=text, callback_data=data)
    b.button(text="🔙 بازگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()



def group_duel_lobby_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚔️ قبول می‌کنم", callback_data="group_duel_accept")
    b.adjust(1)
    return b.as_markup()


def group_finished_keyboard(game_id: str, report_prefix: str = "gqreport") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ گزارش و جواب‌ها", callback_data=f"{report_prefix}:menu:{game_id}")],
        [
            InlineKeyboardButton(text="🎮 بازی با رفیقم", switch_inline_query=""),
            InlineKeyboardButton(text="🔁 بازی مجدد", switch_inline_query_current_chat=""),
        ],
    ])


def group_report_questions_keyboard(game_id: str, count: int, report_prefix: str = "gqreport") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i in range(1, count + 1):
        b.button(text=str(i), callback_data=f"{report_prefix}:q:{game_id}:{i-1}")
    b.button(text="انصراف", callback_data=f"{report_prefix}:cancel:{game_id}")
    b.adjust(5)
    return b.as_markup()


def group_replay_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎮 بازی با رفیقم", switch_inline_query=""),
        InlineKeyboardButton(text="🔁 بازی مجدد", switch_inline_query_current_chat=""),
    ]])


def submission_genre_keyboard(genres: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, genre in enumerate(genres):
        b.button(text=genre, callback_data=f"submit_genre:{idx}")
    b.adjust(2)
    return b.as_markup()


def duel_finished_keyboard(duel_id: int, opponent_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 گزارش و جواب‌ها", callback_data=f"duel_report_answers:{duel_id}")
    b.button(text="👤 دیدن پروفایل حریف", callback_data=f"opponent_profile:{opponent_id}")
    b.button(text="🔁 درخواست بازی مجدد", callback_data=f"rematch_request:{opponent_id}")
    b.adjust(1)
    return b.as_markup()


def rematch_keyboard(requester_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ قبول", callback_data=f"rematch_accept:{requester_id}")
    b.button(text="❌ رد", callback_data=f"rematch_decline:{requester_id}")
    b.adjust(2)
    return b.as_markup()

# --- Persian settings panel ---
SETTING_LABELS = {
    "duel_question_count": "تعداد سوال دوئل",
    "question_timer_seconds": "زمان پاسخ هر سوال",
    "genres_to_offer": "تعداد ژانر پیشنهادی",
    "genres_to_choose": "تعداد ژانر انتخابی",
    "random_duel_cost": "هزینه دوئل شانسی",
    "friendly_duel_cost": "هزینه دوئل دوستانه",
    "matchmaking_timeout_seconds": "زمان انتظار صف شانسی",
    "genre_selection_timeout_seconds": "مهلت انتخاب ژانر",
    "inactive_forfeit_penalty_coins": "جریمه بی‌پاسخی",
    "reward_coin_per_correct": "سکه هر پاسخ درست",
    "reward_xp_per_correct": "ایکس‌پی هر پاسخ درست",
    "random_duel_win_coin_bonus": "جایزه برد دوئل شانسی",
    "winner_bonus_xp": "ایکس‌پی برد",
    "duel_draw_coin_reward": "جایزه مساوی",
    "initial_signup_coins": "سکه شروع ثبت‌نام",
    "question_approval_reward_coins": "پاداش تایید سوال",
    "referral_referrer_coins": "رفرال دعوت‌کننده: سکه",
    "referral_referrer_xp": "رفرال دعوت‌کننده: ایکس‌پی",
    "referral_referred_coins": "رفرال دعوت‌شونده: سکه",
    "referral_referred_xp": "رفرال دعوت‌شونده: ایکس‌پی",
    "streak_day_1_coins": "کمک روزانه 1",
    "streak_day_2_coins": "کمک روزانه 2",
    "streak_day_3_coins": "کمک روزانه 3",
    "streak_day_4_coins": "کمک روزانه 4",
    "streak_day_5_coins": "کمک روزانه 5",
    "streak_day_6_coins": "کمک روزانه 6",
    "streak_day_7_coins": "کمک روزانه 7",
    "powerup_remove2_cost": "هزینه حذف دو گزینه",
    "powerup_auto_answer_cost": "هزینه جواب خودکار",
    "powerup_max_uses_per_duel": "حداکثر استفاده پاورآپ",
    "group_quiz_max_players": "حداکثر بازیکن گروهی",
    "group_quiz_question_count": "تعداد سوال بازی گروهی",
    "group_quiz_timer_seconds": "زمان سوال بازی گروهی",
    "group_quiz_entry_cost": "هزینه ورود بازی گروهی",
    "max_level": "حداکثر لول",
    "xp_level_curve_factor": "ضریب منحنی ایکس‌پی قدیمی",
    "genre_stats_min_answers": "حداقل پاسخ برای تحلیل ژانر",
    "payment_card_number": "شماره کارت",
    "payment_card_holder": "نام صاحب کارت",
    "payment_method": "روش پرداخت",
    "contact_admin_id": "آیدی پشتیبانی",
    "welcome_text": "متن خوش‌آمدگویی",
    "help_text": "متن راهنما",
    "start_photo_file_id": "عکس استارت",
    "maintenance_mode": "حالت تعمیر",
    "maintenance_text": "متن حالت تعمیر",
    "force_join_enabled": "جوین اجباری فعال",
    "force_join_channel": "کانال جوین اجباری",
    "admin_review_channel_id": "کانال بررسی ادمین",
    "reports_channel_id": "کانال گزارش‌ها",
    "question_filter_words": "کلمات فیلتر سوال",
    "daily_question_limit": "سقف ثبت سوال روزانه",
    "visual_timer_enabled": "تایمر نمایشی",
    "visual_timer_interval_seconds": "فاصله ادیت تایمر",
    "fast_bonus_xp_0_5": "بونوس سرعت 0 تا 5 ثانیه",
    "fast_bonus_xp_5_10": "بونوس سرعت 5 تا 10 ثانیه",
    "question_auto_disable_reports": "غیرفعال‌سازی خودکار گزارش",
}

SETTING_CATEGORIES = {
    "duel": ("⚔️ تنظیمات دوئل", [
        "duel_question_count", "question_timer_seconds", "genres_to_offer", "genres_to_choose",
        "random_duel_cost", "friendly_duel_cost", "matchmaking_timeout_seconds",
        "genre_selection_timeout_seconds", "inactive_forfeit_penalty_coins",
    ]),
    "rewards": ("🎁 جوایز و اقتصاد بازی", [
        "reward_coin_per_correct", "reward_xp_per_correct", "random_duel_win_coin_bonus",
        "winner_bonus_xp", "duel_draw_coin_reward", "initial_signup_coins",
        "question_approval_reward_coins",
    ]),
    "powerups": ("🔋 پاورآپ‌ها", [
        "powerup_remove2_cost", "powerup_auto_answer_cost", "powerup_max_uses_per_duel",
    ]),
    "referral": ("👥 رفرال و کمک روزانه", [
        "referral_referrer_coins", "referral_referrer_xp", "referral_referred_coins", "referral_referred_xp",
        "streak_day_1_coins", "streak_day_2_coins", "streak_day_3_coins", "streak_day_4_coins",
        "streak_day_5_coins", "streak_day_6_coins", "streak_day_7_coins",
    ]),
    "group": ("🎮 بازی گروهی", [
        "group_quiz_max_players", "group_quiz_question_count", "group_quiz_timer_seconds", "group_quiz_entry_cost",
    ]),
    "level": ("🏆 لول، لیگ و تحلیل", [
        "max_level", "xp_level_curve_factor", "genre_stats_min_answers",
    ]),
    "shop": ("🛒 فروشگاه و پرداخت", [
        "payment_card_number", "payment_card_holder", "payment_method",
    ]),
    "texts": ("📝 متن‌ها و پیام‌ها", [
        "welcome_text", "help_text", "maintenance_text", "contact_admin_id",
    ]),
    "system": ("🛠 سیستم و امنیت", [
        "maintenance_mode", "force_join_enabled", "force_join_channel", "start_photo_file_id",
        "admin_review_channel_id", "reports_channel_id", "question_filter_words", "daily_question_limit",
    ]),
}


def setting_label(key: str) -> str:
    return SETTING_LABELS.get(key, key)


def admin_settings_categories_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat, (title, _keys) in SETTING_CATEGORIES.items():
        b.button(text=title, callback_data=f"settings_cat:{cat}")
    b.button(text="📦 سایر تنظیمات", callback_data="settings_cat:other")
    b.button(text="🔙 برگشت", callback_data="admin:back")
    b.adjust(1)
    return b.as_markup()


def admin_settings_list_keyboard(settings, category: str) -> InlineKeyboardMarkup:
    rows = list(settings)
    by_key = {s['key']: s for s in rows}
    used = {k for _cat, (_title, keys) in SETTING_CATEGORIES.items() for k in keys}
    if category == "other":
        selected = [s for s in rows if s['key'] not in used]
        title = "سایر تنظیمات"
    else:
        keys = SETTING_CATEGORIES.get(category, ("", []))[1]
        selected = [by_key[k] for k in keys if k in by_key]
        title = SETTING_CATEGORIES.get(category, ("تنظیمات", []))[0]
    b = InlineKeyboardBuilder()
    for s in selected:
        value = str(s['value'])
        if len(value) > 28:
            value = value[:28] + "..."
        b.button(text=f"{setting_label(s['key'])}: {value}", callback_data=f"set:{s['key']}:{category}")
    b.button(text="🔙 دسته‌بندی تنظیمات", callback_data="admin:settings")
    b.adjust(1)
    return b.as_markup()
