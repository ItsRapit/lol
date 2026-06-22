import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from app.db import Database, now_iso
from app.keyboards import (
    admin_panel, settings_keyboard, user_admin_keyboard, main_menu, cancel_keyboard,
    admin_shop_types_keyboard, admin_shop_packages_keyboard, admin_shop_edit_keyboard,
    admin_leagues_keyboard, admin_league_edit_keyboard, admin_discounts_keyboard,
    discount_kind_keyboard, question_manage_keyboard, question_genres_keyboard, pending_questions_keyboard,
    invalid_questions_confirm_keyboard, review_question_keyboard, question_admin_actions_keyboard, question_search_results_keyboard, genre_edit_keyboard,
    titles_menu_keyboard, animation_preview_keyboard, admin_submenu_keyboard,
)
from app.states import AdminFlow, BulkQuestionImport, ShopPackageFlow, LeagueFlow, DiscountFlow, QuestionCleanupFlow, QuestionEditFlow, TitleFlow
from app.bulk_questions import parse_bulk_questions, format_bulk_report, bulk_help_text, extract_json_text, is_json_balanced, looks_like_json, looks_like_bulk_text
from app.time_utils import tehran_now, jalali_datetime
from app.notifications import run_edit_animation, levelup_steps, rankup_steps, title_steps, demotion_steps
from app.clean_questions import get_filter_words, get_clean_stats, clean_duplicate_questions

logger = logging.getLogger(__name__)
router = Router()


async def bulk_timeout_notice(state: FSMContext, bot: Bot, admin_id: int, stamp: str) -> None:
    try:
        await asyncio.sleep(1800)
        data = await state.get_data()
        if data.get("bulk_updated_at") == stamp and data.get("bulk_chunks") is not None:
            await state.clear()
            await bot.send_message(admin_id, "⏱ چون 30 دقیقه پیامی نفرستادی، بافر Bulk پاک شد و حالت Bulk لغو شد.")
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Bulk timeout notice failed")


async def require_admin_message(message: Message, db: Database) -> bool:
    if not await db.is_admin(message.from_user.id):
        await message.answer("دسترسی ادمین ندارید.")
        return False
    return True


async def require_admin_call(call: CallbackQuery, db: Database) -> bool:
    if not await db.is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید.", show_alert=True)
        return False
    return True


@router.message(F.text == "🛡 پنل ادمین")
@router.message(Command("admin"))
async def admin_entry(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        await state.clear()
        await message.answer("پنل ادمین:", reply_markup=ReplyKeyboardRemove())
        await message.answer("انتخاب کنید:", reply_markup=admin_panel())
    except Exception:
        logger.exception("Admin entry failed")
        await message.answer("خطا.")


@router.message(Command("backup"))
async def backup_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        path = await db.backup_copy()
        await message.answer_document(FSInputFile(path), caption="بک‌آپ دیتابیس")
        await db.log_admin(message.from_user.id, "backup")
    except Exception:
        logger.exception("Backup failed")
        await message.answer("خطا در ساخت بک‌آپ.")


async def format_question_admin_text(db: Database, qid: int) -> str | None:
    q = await db.get_question(qid)
    if not q:
        return None
    opts = [q['option1'], q['option2'], q['option3'], q['option4']]
    stats = await db.question_answer_stats(qid)
    submitter = await db.get_user(q['submitted_by']) if q['submitted_by'] else None
    submitter_name = (submitter['first_name'] or submitter['username'] or '—') if submitter else 'ادمین/نامشخص'
    submitter_id = q['submitted_by'] or '—'
    return (
        f"📋 سوال #{q['id']}\n\n"
        f"❓ متن: {q['text']}\n"
        f"🏷 ژانر: {q['genre']}\n"
        f"⚙️ سختی: {q['difficulty'] if 'difficulty' in q.keys() else 'متوسط'}\n"
        f"✅ جواب درست: {opts[int(q['correct_option']) - 1]}\n"
        f"🔘 گزینه‌ها:\n  1. {opts[0]}\n  2. {opts[1]}\n  3. {opts[2]}\n  4. {opts[3]}\n"
        f"📊 آمار: {stats['total']} بار پرسیده شده | {stats['pct']}% درصد پاسخ درست\n"
        f"👤 پیشنهاددهنده: {submitter_name} | ID: {submitter_id}\n"
        f"📅 تاریخ ثبت: {jalali_datetime(q['created_at'])}\n"
        f"📌 وضعیت: {q['status']}"
    )


@router.message(Command("migrate_xp_curve"))
async def migrate_xp_curve_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        backup = await db.migrate_xp_curve_v2()
        await db.log_admin(message.from_user.id, "migrate_xp_curve_v2", details=backup)
        await message.answer(
            "✅ منحنی XP جدید اعمال شد و level کاربران دوباره محاسبه شد.\n"
            f"قبل از migration بک‌آپ کاربران ذخیره شد:\n<code>{backup}</code>\n\n"
            f"XP لازم برای لول 100: {await db.xp_required_for_level(100)}"
        )
    except Exception:
        logger.exception("XP curve migration failed")
        await message.answer("خطا در migration منحنی XP.")


@router.message(Command("setlevel"))
async def setlevel_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        raw = (message.text or "").split(maxsplit=2)
        if len(raw) < 3 or not raw[1].isdigit():
            await message.answer("فرمت درست:\n<code>/setlevel LEVEL EMOJI NAME | XP</code>\nمثال: <code>/setlevel 5 🗡 شکارچی | 1200</code>")
            return
        level = int(raw[1])
        body = raw[2]
        left, _, xp_part = body.partition("|")
        xp_required = int(xp_part.strip()) if xp_part.strip().isdigit() else None
        parts = left.strip().split(maxsplit=1)
        emoji = parts[0] if parts else ""
        name = parts[1] if len(parts) > 1 else ""
        await db.set_level_config(level, name or None, emoji or None, xp_required)
        await db.log_admin(message.from_user.id, "setlevel", str(level), body)
        await message.answer(f"✅ لول {level} ذخیره شد:\n{await db.get_level_display(level)}\nXP لازم: {await db.xp_required_for_level(level)}")
    except Exception:
        logger.exception("Set level failed")
        await message.answer("خطا در تنظیم لول. مثال: /setlevel 5 🗡 شکارچی | 1200")


async def send_question_search_results(message: Message, db: Database, query: str, page: int = 0) -> None:
    results = await db.search_questions(query, page)
    if not results:
        await message.answer("❌ سوالی با این مشخصات پیدا نشد")
        return
    lines = [f"🔍 نتایج جستجو ({len(results)} مورد):"]
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, q in enumerate(results):
        status = "✅ تأییدشده" if q['status'] == 'active' else q['status']
        lines.append(f"\n{nums[i]} ID: {q['id']}\n❓ {q['text']}\n🏷 ژانر: {q['genre']} | {status}")
    await message.answer("\n".join(lines), reply_markup=question_search_results_keyboard(results, page, query))


@router.message(Command("searchquestion"))
async def search_question_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        query = (message.text or "").replace("/searchquestion", "", 1).strip()
        if not query:
            await message.answer("فرمت درست: /searchquestion متن یا ID")
            return
        await send_question_search_results(message, db, query, 0)
    except Exception:
        logger.exception("Search question failed")
        await message.answer("خطا در جستجوی سوال.")


@router.message(Command("question"))
async def question_lookup_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        parts = (message.text or '').split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("فرمت درست: /question ID")
            return
        qid = int(parts[1])
        text = await format_question_admin_text(db, qid)
        if not text:
            await message.answer("سوال پیدا نشد.")
            return
        await message.answer(text, reply_markup=question_admin_actions_keyboard(qid))
    except Exception:
        logger.exception("Question lookup failed")
        await message.answer("خطا در جستجوی سوال.")


async def send_section_backup(message: Message, db: Database, section: str, title: str) -> None:
    if not await require_admin_message(message, db):
        return
    path = await db.export_section_backup(section)
    await message.answer_document(FSInputFile(path), caption=title)
    await db.log_admin(message.from_user.id, f"backup_{section}")


@router.message(Command("backup_questions"))
async def backup_questions_command(message: Message, db: Database) -> None:
    await send_section_backup(message, db, "questions", "بک‌آپ سوالات")


@router.message(Command("backup_users"))
async def backup_users_command(message: Message, db: Database) -> None:
    await send_section_backup(message, db, "users", "بک‌آپ کاربران")


@router.message(Command("backup_settings"))
async def backup_settings_command(message: Message, db: Database) -> None:
    await send_section_backup(message, db, "settings", "بک‌آپ تنظیمات و ساختار")


@router.message(Command("upload_backup"))
async def upload_backup_start(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        await state.set_state(AdminFlow.waiting_backup_upload)
        await message.answer("فایل بک‌آپ (.json یا .sqlite/db) را همین‌جا ارسال کن تا روی Volume کنار دیتابیس ذخیره شود.", reply_markup=cancel_keyboard())
    except Exception:
        logger.exception("Upload backup start failed")
        await message.answer("خطا.")


@router.message(AdminFlow.waiting_backup_upload)
async def upload_backup_receive(message: Message, db: Database, state: FSMContext, bot: Bot) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        if not message.document:
            await message.answer("لطفاً فایل بک‌آپ را به صورت document ارسال کن.")
            return
        from pathlib import Path
        original = message.document.file_name or "backup_file"
        safe = ''.join(ch for ch in original if ch.isalnum() or ch in '._-')[:80] or 'backup_file'
        dest_dir = Path(db.path).parent / "uploaded_backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"uploaded_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{safe}"
        file = await bot.get_file(message.document.file_id)
        downloaded = await bot.download_file(file.file_path)
        dest.write_bytes(downloaded.read())
        await db.log_admin(message.from_user.id, "upload_backup", details=str(dest))
        await state.clear()
        await message.answer(f"✅ فایل بک‌آپ روی Volume ذخیره شد:\n<code>{dest}</code>", reply_markup=main_menu(True))
    except Exception:
        logger.exception("Upload backup receive failed")
        await message.answer("خطا در ذخیره فایل بک‌آپ.")


@router.message(Command("version"))
async def version_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        await message.answer(
            "🧩 نسخه کد فعال: <code>challeshino-2026-06-17-hotfix-powerup-stats-genres-v2</code>\n"
            "اگر این پیام را نمی‌بینی، Railway هنوز نسخه جدید را deploy نکرده یا سرویس restart نشده است."
        )
    except Exception:
        logger.exception("Version command failed")
        await message.answer("خطا در نمایش نسخه.")


@router.message(Command("sync_defaults"))
async def sync_defaults_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        force = "force" in (message.text or "").lower().split()
        await db.seed_defaults()
        if force:
            # Force only the latest gameplay defaults that users specifically asked for.
            await db.set_setting("question_approval_reward_coins", "10")
            await db.set_setting("random_duel_win_coin_bonus", "20")
            await db.set_setting("powerup_remove2_cost", "15")
            await db.set_setting("powerup_second_chance_cost", "20")
            await db.set_setting("visual_timer_enabled", "1")
            await db.set_setting("visual_timer_interval_seconds", "6")
            await db.set_setting("fast_bonus_xp_0_5", "5")
            await db.set_setting("fast_bonus_xp_5_10", "2")
            await db.set_setting("question_auto_disable_reports", "3")
            await db.set_setting("genre_stats_min_answers", "1")
            await db.set_setting("streak_day_1_coins", "5")
            await db.set_setting("streak_day_2_coins", "10")
            await db.set_setting("streak_day_3_coins", "15")
            await db.set_setting("streak_day_4_coins", "20")
            await db.set_setting("streak_day_5_coins", "25")
            await db.set_setting("streak_day_6_coins", "30")
            await db.set_setting("streak_day_7_coins", "50")
            await db.set_setting("streak_day_7_xp", "0")
        genres = await db.all_genres()
        p5050 = await db.get_setting("powerup_remove2_cost", "?")
        phint = await db.get_setting("powerup_second_chance_cost", "?")
        pmax = "1 بار برای هرکدام در هر دوئل"
        qreward = await db.get_setting("question_approval_reward_coins", "?")
        await db.log_admin(message.from_user.id, "sync_defaults", details="force" if force else "normal")
        await message.answer(
            "✅ همگام‌سازی پیش‌فرض‌ها انجام شد.\n\n"
            f"ژانرها ({len(genres)}): {', '.join(genres)}\n\n"
            f"پاداش تایید سوال: {qreward} سکه\n"
            f"قیمت حذف دو گزینه: {p5050}\n"
            f"قیمت شانس دوباره: {phint}\n"
            f"محدودیت پاورآپ: {pmax}\n\n"
            "اگر می‌خواهی مقادیر جدید حتماً جایگزین قبلی شوند، بزن:\n"
            "<code>/sync_defaults force</code>"
        )
    except Exception:
        logger.exception("Sync defaults failed")
        await message.answer("خطا در sync defaults.")


@router.message(Command("filterwords"))
async def filterwords_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        words = await get_filter_words(db)
        await message.answer("🔤 کلمات فیلتر فعلی:\n" + ("، ".join(words) if words else "هیچ کلمه‌ای ثبت نشده"))
    except Exception:
        logger.exception("Filterwords command failed")
        await message.answer("خطا در نمایش کلمات فیلتر.")


@router.message(Command("addfilterword"))
async def add_filterword_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer("فرمت درست: /addfilterword کلمه")
            return
        word = parts[1].strip()
        words = await get_filter_words(db)
        if word not in words:
            words.append(word)
            await db.set_setting("question_filter_words", ",".join(words))
        await message.answer("✅ کلمه اضافه شد.\n" + "، ".join(words))
    except Exception:
        logger.exception("Add filterword failed")
        await message.answer("خطا در افزودن کلمه فیلتر.")


@router.message(Command("removefilterword"))
async def remove_filterword_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer("فرمت درست: /removefilterword کلمه")
            return
        word = parts[1].strip()
        words = [w for w in await get_filter_words(db) if w != word]
        await db.set_setting("question_filter_words", ",".join(words))
        await message.answer("✅ کلمه حذف شد.\n" + ("، ".join(words) if words else "لیست خالی است"))
    except Exception:
        logger.exception("Remove filterword failed")
        await message.answer("خطا در حذف کلمه فیلتر.")


@router.message(Command("cleanquestions"))
async def cleanquestions_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        stats = await get_clean_stats(db)
        await message.answer(
            f"🔍 نتیجه‌ی بررسی:\n"
            f"📋 عیناً تکراری: {stats['exact_groups']} گروه — {stats['exact_to_delete']} سوال حذف می‌شود\n"
            f"🔎 مشابه (متن+جواب یکسان): {stats['similar_groups']} گروه — {stats['similar_to_delete']} سوال حذف می‌شود\n"
            f"📊 جمع کل: {stats['total_to_delete']} سوال حذف می‌شود",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ تأیید و پاک‌کردن", callback_data="confirm_clean"), InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_clean")]])
        )
    except Exception:
        logger.exception("Cleanquestions command failed")
        await message.answer("خطا در بررسی پاک‌سازی سوالات.")


@router.message(Command("confirmclean"))
async def confirmclean_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        backup = await db.export_section_backup("questions")
        result = await clean_duplicate_questions(db)
        await db.log_admin(message.from_user.id, "cleanquestions", details=f"backup={backup}")
        await message.answer(
            f"✅ پاک‌سازی انجام شد\n\n"
            f"🗂 بک‌آپ قبل از حذف: <code>{backup}</code>\n"
            f"📋 عیناً تکراری حذف‌شده: {result['exact_deleted']}\n"
            f"🔎 مشابه حذف‌شده: {result['similar_deleted']}\n"
            f"📊 جمع کل حذف‌شده: {result['deleted']}"
        )
    except Exception:
        logger.exception("Confirm clean failed")
        await message.answer("خطا در پاک‌سازی سوالات.")


@router.callback_query(F.data == "confirm_clean")
async def confirm_clean_callback(call: CallbackQuery, db: Database) -> None:
    await call.answer()
    try:
        if not await require_admin_call(call, db):
            return
        backup = await db.export_section_backup("questions")
        await call.message.edit_text("⏳ در حال پاک‌کردن...")
        result = await clean_duplicate_questions(db)
        await db.log_admin(call.from_user.id, "cleanquestions", details=f"backup={backup}")
        await call.message.edit_text(
            f"✅ پاک‌سازی انجام شد\n\n"
            f"🗂 بک‌آپ قبل از حذف: <code>{backup}</code>\n"
            f"📋 عیناً تکراری حذف‌شده: {result['exact_deleted']}\n"
            f"🔎 مشابه حذف‌شده: {result['similar_deleted']}\n"
            f"📊 جمع کل حذف‌شده: {result['deleted']}"
        )
    except Exception:
        logger.exception("Confirm clean callback failed")
        await call.message.answer("خطا در پاک‌سازی سوالات.")


@router.callback_query(F.data == "cancel_clean")
async def cancel_clean_callback(call: CallbackQuery) -> None:
    await call.answer("لغو شد", show_alert=False)
    try:
        await call.message.edit_text("❌ پاک‌سازی لغو شد.")
    except Exception:
        logger.exception("Cancel clean edit failed")


@router.message(Command("guide"))
async def admin_guide(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        await message.answer(
            "📘 راهنمای کامندهای ادمین\n\n"
            "مدیریت موجودی کاربر:\n"
            "/addcoins USER_ID AMOUNT\n"
            "/takecoins USER_ID AMOUNT\n"
            "/addxp USER_ID AMOUNT\n"
            "/takexp USER_ID AMOUNT\n"
            "/addcups USER_ID AMOUNT\n"
            "/takecups USER_ID AMOUNT\n\n"
            "مثال:\n"
            "<code>/addcoins 123456789 500</code>\n"
            "<code>/takecups 123456789 20</code>\n\n"
            "سایر کامندها:\n"
            "/admin پنل ادمین\n"
            "/backup بک‌آپ دیتابیس\n"
            "/cancel لغو عملیات جاری"
        )
    except Exception:
        logger.exception("Admin guide failed")
        await message.answer("خطا در نمایش راهنما.")


async def apply_admin_balance_command(message: Message, db: Database, kind: str, sign: int) -> None:
    if not await require_admin_message(message, db):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("فرمت درست: /command USER_ID AMOUNT")
        return
    try:
        target = int(parts[1])
        amount = abs(int(parts[2])) * sign
    except ValueError:
        await message.answer("USER_ID و AMOUNT باید عددی باشند.")
        return
    user = await db.get_user(target)
    if not user:
        await message.answer("کاربر پیدا نشد.")
        return
    if kind == "coins":
        await db.change_coins(target, amount, "admin_command")
    elif kind == "xp":
        await db.change_xp(target, amount, "admin_command")
    elif kind == "cups":
        await db.change_cups(target, amount, "admin_command")
    updated = await db.get_user(target)
    await db.log_admin(message.from_user.id, f"cmd_{kind}", str(target), str(amount))
    await message.answer(
        f"انجام شد.\nکاربر: <code>{target}</code>\n"
        f"سکه: {updated['coins']}\nXP: {updated['xp']}\nجام: {updated['cups']}"
    )


@router.message(Command("addcoins"))
async def cmd_addcoins(message: Message, db: Database) -> None:
    await apply_admin_balance_command(message, db, "coins", +1)


@router.message(Command("takecoins"))
async def cmd_takecoins(message: Message, db: Database) -> None:
    await apply_admin_balance_command(message, db, "coins", -1)


@router.message(Command("addxp"))
async def cmd_addxp(message: Message, db: Database) -> None:
    await apply_admin_balance_command(message, db, "xp", +1)


@router.message(Command("takexp"))
async def cmd_takexp(message: Message, db: Database) -> None:
    await apply_admin_balance_command(message, db, "xp", -1)


@router.message(Command("addcups"))
async def cmd_addcups(message: Message, db: Database) -> None:
    await apply_admin_balance_command(message, db, "cups", +1)


@router.message(Command("takecups"))
async def cmd_takecups(message: Message, db: Database) -> None:
    await apply_admin_balance_command(message, db, "cups", -1)


@router.callback_query(F.data.startswith("admin:"))
async def admin_callback(call: CallbackQuery, db: Database, state: FSMContext, bot: Bot) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        await state.clear()
        action = call.data.split(":", 1)[1]
        if action == 'back':
            await call.message.answer("⚙️ پنل مدیریت", reply_markup=admin_panel())
        elif action == 'user_management':
            await call.message.answer("👥 مدیریت کاربران", reply_markup=admin_submenu_keyboard('user'))
        elif action == 'question_management':
            await call.message.answer("❓ مدیریت سوالات", reply_markup=admin_submenu_keyboard('question'))
        elif action == 'game_settings':
            await call.message.answer("🎮 تنظیمات بازی", reply_markup=admin_submenu_keyboard('game'))
        elif action == 'economy_settings':
            await call.message.answer("💰 تنظیمات اقتصادی", reply_markup=admin_submenu_keyboard('economy'))
        elif action == 'league_level_settings':
            await call.message.answer("🏆 تنظیمات لیگ و لول", reply_markup=admin_submenu_keyboard('league'))
        elif action == 'notifications':
            await call.message.answer("📣 اعلان‌ها", reply_markup=admin_submenu_keyboard('notifications'))
        elif action == 'stats_reports':
            await call.message.answer("📊 آمار و گزارش", reply_markup=admin_submenu_keyboard('reports'))
        elif action == 'file_config':
            await call.message.answer("📁 مدیریت فایل Config", reply_markup=admin_submenu_keyboard('file'))
        elif action == 'animation_preview':
            await call.message.answer("🎬 پیش‌نمایش انیمیشن‌ها", reply_markup=animation_preview_keyboard())
        elif action == 'titles':
            await call.message.answer("🏅 مدیریت لقب‌ها", reply_markup=titles_menu_keyboard())
        elif action == 'question_lookup_help':
            await call.message.answer("برای جستجوی سوال بزن: <code>/question ID</code>")
        elif action == 'manual_question_help':
            await call.message.answer("افزودن دستی از منوی کاربر «ثبت سوال» یا Bulk استفاده کن.")
        elif action == 'upload_backup':
            await state.set_state(AdminFlow.waiting_backup_upload)
            await call.message.answer("فایل بک‌آپ را ارسال کن تا روی Volume ذخیره شود.", reply_markup=cancel_keyboard())
        elif action == 'backup_questions':
            path = await db.export_section_backup('questions'); await call.message.answer_document(FSInputFile(path), caption='بک‌آپ سوالات')
        elif action == 'backup_users':
            path = await db.export_section_backup('users'); await call.message.answer_document(FSInputFile(path), caption='بک‌آپ کاربران')
        elif action == 'backup_settings':
            path = await db.export_section_backup('settings'); await call.message.answer_document(FSInputFile(path), caption='بک‌آپ تنظیمات')
        elif action == 'stats':
            s = await db.stats()
            await call.message.answer(
                "📊 آمار کامل چالشینو\n\n"
                "👥 کاربران\n"
                f"• کل کاربران: {s['users']}\n"
                f"• کاربران جدید امروز: {s['new_users_today']}\n\n"
                "⚔️ دوئل‌های پیوی\n"
                f"• کل دوئل‌ها: {s['duels']}\n"
                f"• دوئل‌های تمام‌شده: {s['finished_duels']}\n"
                f"• دوئل‌های امروز: {s['duels_today']}\n\n"
                "🎮 بازی‌های گروهی\n"
                f"• بازی گروهی انجام‌شده: {s['group_quiz_total']}\n"
                f"• دوئل‌های گروهی انجام‌شده: {s['group_duel_total']}\n"
                f"• کل بازی‌های گروهی امروز: {s['group_games_today']}\n\n"
                "💰 درآمد فروشگاه\n"
                f"• این هفته: {s['revenue_week']:,} تومان\n"
                f"• این ماه: {s['revenue_month']:,} تومان\n"
                f"• این سال: {s['revenue_year']:,} تومان\n"
                f"• خریدهای تاییدشده: {s['approved_transactions']}\n\n"
                "🪙 اقتصاد بازی\n"
                f"• کوین تولیدشده واقعی: {s['coins_generated']}\n"
                f"• کوین مصرف‌شده واقعی: {s['coins_burned']}\n"
                f"• کل ورودی کوین همه رویدادها: {s['coins_total_positive']}\n"
                f"• کل خروجی کوین همه رویدادها: {s['coins_total_negative']}\n\n"
                "❓ سوالات\n"
                f"• کل سوالات: {s['total_questions']}\n"
                f"• فعال: {s['active_questions']}\n"
                f"• غیرفعال: {s['disabled_questions']}\n"
                f"• در صف بررسی: {s['pending_questions']}\n"
                f"• ثبت‌شده توسط کاربر: {s['user_questions']}\n"
                f"• ثبت‌شده توسط ادمین: {s['admin_questions']}"
            )
        elif action == 'settings':
            await call.message.answer("برای ویرایش روی تنظیم کلیک کنید:", reply_markup=settings_keyboard(await db.all_settings()))
        elif action == 'user_search':
            await state.set_state(AdminFlow.waiting_user_id)
            await call.message.answer("آیدی عددی کاربر را بفرست:", reply_markup=cancel_keyboard())
        elif action in {'add_admin', 'remove_admin'}:
            await state.set_state(AdminFlow.waiting_admin_id)
            await state.update_data(admin_action=action)
            await call.message.answer("آیدی عددی ادمین را بفرست:", reply_markup=cancel_keyboard())
        elif action == 'backup':
            path = await db.backup_copy()
            await call.message.answer_document(FSInputFile(path), caption="بک‌آپ دیتابیس")
            await db.log_admin(call.from_user.id, "backup")
        elif action == 'bulk_questions':
            await state.set_state(BulkQuestionImport.waiting_json)
            stamp = tehran_now().isoformat()
            await state.update_data(bulk_chunks=[], bulk_updated_at=stamp)
            asyncio.create_task(bulk_timeout_notice(state, bot, call.from_user.id, stamp))
            await call.message.answer(bulk_help_text(await db.all_genres()), reply_markup=cancel_keyboard())
        elif action == 'shop_manage':
            await call.message.answer("کدام بخش فروشگاه مدیریت شود؟", reply_markup=admin_shop_types_keyboard())
        elif action == 'discounts':
            await call.message.answer("مدیریت کدهای تخفیف:", reply_markup=admin_discounts_keyboard(await db.discounts()))
        elif action == 'leagues':
            await call.message.answer("مدیریت لیگ‌ها/تیرها (ساختار ثابت است؛ فقط اعداد و نام‌ها ویرایش می‌شوند):", reply_markup=admin_leagues_keyboard(await db.all_leagues()))
        elif action == 'maintenance_toggle':
            cur = await db.get_int("maintenance_mode", 0)
            new_val = "0" if cur else "1"
            cancelled = []
            if new_val == "1":
                cancelled = await db.cancel_active_duels_with_refund()
                for item in cancelled:
                    for uid, amount in item['refunds'].items():
                        try:
                            await bot.send_message(uid, f"🛠 ربات وارد حالت تعمیر شد. دوئل/صف فعال شما بسته شد و {amount} سکه به حسابتان برگشت.", reply_markup=main_menu(await db.is_admin(uid)))
                        except Exception:
                            logger.exception("Could not notify maintenance refund user=%s", uid)
            await db.set_setting("maintenance_mode", new_val)
            await db.log_admin(call.from_user.id, "maintenance_toggle", details=f"{new_val}, cancelled={len(cancelled)}")
            await call.message.answer("حالت تعمیر " + ("روشن شد." if new_val == "1" else "خاموش شد.") + (f"\n{len(cancelled)} دوئل/صف فعال بسته و refund شد." if new_val == "1" else ""), reply_markup=admin_panel())
        elif action == 'start_photo':
            await state.set_state(AdminFlow.waiting_start_photo)
            await call.message.answer("عکس جدید پیام /start را ارسال کنید. برای حذف عکس، متن /remove_photo را بفرستید.", reply_markup=cancel_keyboard())
        elif action == 'levels':
            rows = await db.level_config_rows()
            preview = "\n".join(f"{r['level_number']}. {(r['emoji'] or '')} {(r['name'] or 'لول ' + str(r['level_number']))} | XP: {r['xp_required']}" for r in rows[:30])
            await call.message.answer(
                "🎚 مدیریت لول‌ها\n\n"
                "برای تنظیم نام/ایموجی/XP لازم از کامند زیر استفاده کن:\n"
                "<code>/setlevel LEVEL EMOJI NAME | XP</code>\n\n"
                "مثال:\n<code>/setlevel 5 🗡 شکارچی | 1200</code>\n\n"
                "نمونه لول‌ها:\n" + (preview or "هنوز تنظیمی وجود ندارد.")
            )
        elif action == 'question_manage':
            await call.message.answer("مدیریت سوالات — یکی از بخش‌ها را انتخاب کن:", reply_markup=question_manage_keyboard())
        elif action == 'question_cleanup':
            invalid = await db.invalid_genre_questions()
            if not invalid:
                await call.message.answer("هیچ سوالی با ژانر نامعتبر پیدا نشد.")
            else:
                lines = [f"#{r['id']} | {r['genre']} | {r['text'][:50]}" for r in invalid[:50]]
                await state.set_state(QuestionCleanupFlow.confirm_delete_invalid)
                await call.message.answer("سوالات با ژانر نامعتبر پیدا شدند:\n" + "\n".join(lines) + f"\n\nتعداد نمایش/حذف در این مرحله: {len(invalid)}", reply_markup=invalid_questions_confirm_keyboard())
        await call.answer()
    except Exception:
        logger.exception("Admin callback failed")
        await call.answer("خطا", show_alert=True)


@router.message(BulkQuestionImport.waiting_json)
async def bulk_questions_receive(message: Message, db: Database, state: FSMContext, bot: Bot) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        data_state = await state.get_data()
        last_update = data_state.get("bulk_updated_at")
        if last_update:
            try:
                if (tehran_now() - datetime.fromisoformat(last_update)).total_seconds() > 1800:
                    await state.clear()
                    await message.answer("⏱ بیش از 30 دقیقه گذشت؛ بافر Bulk پاک شد و حالت Bulk لغو شد.", reply_markup=main_menu(True))
                    return
            except Exception:
                logger.exception("Bulk timeout check failed")
        chunks = list(data_state.get("bulk_chunks", []))
        if message.text and message.text.strip() == "/done":
            payload = "\n".join(chunks).strip()
            if not payload:
                await message.answer("هنوز هیچ محتوایی دریافت نشده است.")
                return
            accepted, rejected = parse_bulk_questions(payload, await db.all_genres())
            success = 0
            if not rejected and accepted:
                success = await db.bulk_admin_add_questions(message.from_user.id, accepted)
            await db.log_admin(message.from_user.id, "bulk_questions", details=f"success={success}, rejected={len(rejected)}")
            await state.clear()
            await message.answer(format_bulk_report(success, rejected), reply_markup=ReplyKeyboardRemove())
            return
        payload = message.text or ""
        if message.document:
            name = message.document.file_name or ""
            if not (name.endswith(".json") or name.endswith(".txt")):
                await message.answer("فقط فایل .json یا .txt قابل قبول است.")
                return
            file = await bot.get_file(message.document.file_id)
            downloaded = await bot.download_file(file.file_path)
            payload = downloaded.read().decode("utf-8-sig")
        payload = extract_json_text(payload)
        if not payload.strip() or (message.text and not looks_like_json(payload) and not looks_like_bulk_text(payload) and not chunks):
            await message.answer("متن JSON یا فرم متنی سوال‌ها را بفرست؛ بعد از اتمام /done را ارسال کن.")
            return
        chunks.append(payload)
        joined = "\n".join(chunks)
        new_stamp = tehran_now().isoformat()
        await state.update_data(bulk_chunks=chunks, bulk_updated_at=new_stamp)
        asyncio.create_task(bulk_timeout_notice(state, bot, message.from_user.id, new_stamp))
        if not is_json_balanced(joined):
            await message.answer("⏳ ادامه رو بفرست... (یا /done برای پایان)")
        else:
            await message.answer(f"✅ بخش {len(chunks)} دریافت شد و JSON متوازن به نظر می‌رسد. اگر تمام شد /done را بفرست؛ در غیر این صورت بخش بعدی را ارسال کن.")
    except UnicodeDecodeError:
        logger.exception("Bulk file encoding failed")
        await message.answer("فایل باید UTF-8 باشد.")
    except Exception:
        logger.exception("Bulk import failed")
        await message.answer("خطا در افزودن Bulk سوال.")


@router.callback_query(F.data.startswith("set:"))
async def setting_pick(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        await state.clear()
        key = call.data.split(":", 1)[1]
        val = await db.get_setting(key)
        await state.set_state(AdminFlow.waiting_setting_value)
        await state.update_data(setting_key=key)
        await call.message.answer(f"مقدار جدید برای <code>{key}</code> را بفرست. مقدار فعلی: <code>{val}</code>", reply_markup=cancel_keyboard())
        await call.answer()
    except Exception:
        logger.exception("Setting pick failed")
        await call.answer("خطا", show_alert=True)


@router.message(AdminFlow.waiting_setting_value, F.text)
async def setting_value(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        data = await state.get_data()
        await db.set_setting(data['setting_key'], message.text.strip())
        await db.log_admin(message.from_user.id, "setting_update", data['setting_key'], message.text.strip())
        await state.clear()
        await message.answer("تنظیم ذخیره شد.", reply_markup=main_menu(True))
    except Exception:
        logger.exception("Setting save failed")
        await message.answer("خطا در ذخیره تنظیم.")


async def send_user_profile_admin(message: Message, db: Database, tg_id: int) -> None:
    u = await db.get_user(tg_id)
    if not u:
        await message.answer("کاربر پیدا نشد.")
        return
    rank = await db.get_rank_title(u['level'])
    league = await db.get_user_league(u['cups'])
    await message.answer(
        f"👤 کاربر <code>{tg_id}</code>\n"
        f"Username: @{u['username'] or '-'}\n"
        f"نام: {u['first_name'] or '-'}\n"
        f"سکه: {u['coins']}\n"
        f"ایکس‌پی: {u['xp']}\n"
        f"لول: {u['level']} — {rank}\n"
        f"جام: {u['cups']} | لیگ: {league['name'] if league else '-'}\n"
        f"Blocked: {bool(u['is_blocked'])}\n"
        f"Wins/Losses/Draws: {u['wins']}/{u['losses']}/{u['draws']}",
        reply_markup=user_admin_keyboard(tg_id),
    )


@router.message(Command("user"))
async def user_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("فرمت درست: /user USER_ID")
            return
        await send_user_profile_admin(message, db, int(parts[1]))
    except Exception:
        logger.exception("User command failed")
        await message.answer("خطا در جستجوی کاربر.")


@router.message(AdminFlow.waiting_user_id, F.text)
async def user_lookup(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        tg_id = int(message.text.strip())
        await state.clear()
        await send_user_profile_admin(message, db, tg_id)
    except ValueError:
        await message.answer("آیدی باید عددی باشد.")
    except Exception:
        logger.exception("User lookup failed")
        await message.answer("خطا.")


@router.callback_query(F.data.startswith(("ucoin:", "uxp:")))
async def user_delta_start(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        await state.clear()
        kind, tg_s = call.data.split(":")
        await state.set_state(AdminFlow.waiting_user_delta)
        await state.update_data(delta_kind=kind, target_id=int(tg_s))
        await call.message.answer("مقدار تغییر را با علامت وارد کنید؛ مثال: +100 یا -50", reply_markup=cancel_keyboard())
        await call.answer()
    except Exception:
        logger.exception("User delta start failed")
        await call.answer("خطا", show_alert=True)


@router.message(AdminFlow.waiting_user_delta, F.text)
async def user_delta_save(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        data = await state.get_data()
        amount = int(message.text.strip())
        target = int(data['target_id'])
        if data['delta_kind'] == 'ucoin':
            await db.change_coins(target, amount, 'admin_adjust')
        else:
            await db.change_xp(target, amount, 'admin_adjust')
        await db.log_admin(message.from_user.id, "user_adjust", str(target), f"{data['delta_kind']} {amount}")
        await state.clear()
        await message.answer("انجام شد.")
    except ValueError:
        await message.answer("عدد معتبر وارد کنید؛ مثال +100")
    except Exception:
        logger.exception("User delta save failed")
        await message.answer("خطا.")


@router.callback_query(F.data.startswith("ublock:"))
async def user_block_toggle(call: CallbackQuery, db: Database) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        tg_id = int(call.data.split(":")[1])
        u = await db.get_user(tg_id)
        if not u:
            await call.answer("کاربر پیدا نشد.", show_alert=True); return
        new_val = 0 if u['is_blocked'] else 1
        await db.execute_write("UPDATE users SET is_blocked=? WHERE telegram_id=?", (new_val, tg_id))
        await db.log_admin(call.from_user.id, "user_block_toggle", str(tg_id), str(new_val))
        await call.answer("انجام شد.")
    except Exception:
        logger.exception("Block toggle failed")
        await call.answer("خطا", show_alert=True)


@router.message(AdminFlow.waiting_admin_id, F.text)
async def admin_id_save(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        data = await state.get_data()
        target = int(message.text.strip())
        if data['admin_action'] == 'add_admin':
            await db.execute_write("INSERT OR REPLACE INTO admins(telegram_id,role,added_by,created_at) VALUES(?,?,?,?)", (target, 'admin', message.from_user.id, now_iso()))
            await db.log_admin(message.from_user.id, "admin_add", str(target))
            await message.answer("ادمین اضافه شد.")
        else:
            await db.execute_write("DELETE FROM admins WHERE telegram_id=? AND role<>'owner'", (target,))
            await db.log_admin(message.from_user.id, "admin_remove", str(target))
            await message.answer("ادمین حذف شد (مالک حذف نمی‌شود).")
        await state.clear()
    except ValueError:
        await message.answer("آیدی باید عددی باشد.")
    except Exception:
        logger.exception("Admin add/remove failed")
        await message.answer("خطا.")


@router.callback_query(F.data.startswith("ashop:"))
async def admin_shop_callback(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        await state.clear()
        _, action, value = call.data.split(":")
        if action == "list":
            packages = await db.shop_packages(value)
            await call.message.answer("بسته‌ها:", reply_markup=admin_shop_packages_keyboard(packages, value))
        elif action == "add":
            await state.set_state(ShopPackageFlow.title)
            await state.update_data(package_type=value)
            await call.message.answer("نام بسته را وارد کنید:", reply_markup=cancel_keyboard())
        elif action == "edit":
            pkg = await db.get_package(int(value))
            if not pkg:
                await call.answer("بسته پیدا نشد.", show_alert=True); return
            await call.message.answer(f"ویرایش بسته #{pkg['id']}\n{pkg['title']} | coins={pkg['coins']} | xp={pkg['xp']} | {pkg['price_label']}", reply_markup=admin_shop_edit_keyboard(pkg['id']))
        elif action == "delete":
            await db.delete_shop_package(int(value))
            await db.log_admin(call.from_user.id, "shop_package_delete", value)
            await call.message.answer("بسته حذف/غیرفعال شد.", reply_markup=admin_shop_types_keyboard())
        await call.answer()
    except Exception:
        logger.exception("Admin shop callback failed")
        await call.answer("خطا", show_alert=True)


@router.message(ShopPackageFlow.title, F.text)
async def shop_pkg_title(message: Message, state: FSMContext, db: Database) -> None:
    if not await require_admin_message(message, db): return
    await state.update_data(title=message.text.strip())
    await state.set_state(ShopPackageFlow.amount)
    await message.answer("مقدار سکه یا XP را عددی وارد کنید:", reply_markup=cancel_keyboard())


@router.message(ShopPackageFlow.amount, F.text)
async def shop_pkg_amount(message: Message, state: FSMContext, db: Database) -> None:
    try:
        if not await require_admin_message(message, db): return
        amount = int(message.text.strip())
        if amount <= 0: raise ValueError
        await state.update_data(amount=amount)
        await state.set_state(ShopPackageFlow.price)
        await message.answer("برچسب قیمت را وارد کنید؛ مثال: 50,000 تومان", reply_markup=cancel_keyboard())
    except ValueError:
        await message.answer("مقدار باید عدد مثبت باشد.")


@router.message(ShopPackageFlow.price, F.text)
async def shop_pkg_price(message: Message, state: FSMContext, db: Database) -> None:
    try:
        if not await require_admin_message(message, db): return
        data = await state.get_data()
        pid = await db.add_shop_package(data['package_type'], data['title'], int(data['amount']), message.text.strip())
        await db.log_admin(message.from_user.id, "shop_package_add", str(pid))
        await state.clear()
        await message.answer("بسته اضافه شد.", reply_markup=ReplyKeyboardRemove())
    except Exception:
        logger.exception("Shop package add failed")
        await message.answer("خطا در افزودن بسته.")


@router.callback_query(F.data.startswith("ashop_edit:"))
async def admin_shop_edit_start(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db): return
        await state.clear()
        _, field, pid = call.data.split(":")
        state_map = {"title": ShopPackageFlow.edit_title, "amount": ShopPackageFlow.edit_amount, "price": ShopPackageFlow.edit_price}
        await state.set_state(state_map[field])
        await state.update_data(package_id=int(pid), edit_field=field)
        prompt = {"title": "نام جدید:", "amount": "مقدار جدید عددی:", "price": "قیمت جدید:"}[field]
        await call.message.answer(prompt, reply_markup=cancel_keyboard())
        await call.answer()
    except Exception:
        logger.exception("Shop edit start failed")
        await call.answer("خطا", show_alert=True)


@router.message(ShopPackageFlow.edit_title, F.text)
@router.message(ShopPackageFlow.edit_amount, F.text)
@router.message(ShopPackageFlow.edit_price, F.text)
async def admin_shop_edit_save(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db): return
        data = await state.get_data()
        field = data['edit_field']
        db_field = 'price_label' if field == 'price' else field
        value = int(message.text.strip()) if field == 'amount' else message.text.strip()
        await db.update_shop_package_field(int(data['package_id']), db_field, value)
        await db.log_admin(message.from_user.id, "shop_package_edit", str(data['package_id']), f"{field}={value}")
        await state.clear()
        await message.answer("ویرایش ذخیره شد.")
    except ValueError:
        await message.answer("برای مقدار، عدد معتبر وارد کنید.")
    except Exception:
        logger.exception("Shop edit save failed")
        await message.answer("خطا در ذخیره ویرایش.")


@router.callback_query(F.data.startswith("league:"))
async def league_callback(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db): return
        await state.clear()
        parts = call.data.split(":")
        action = parts[1]
        if action == "add":
            await call.answer("ساختار لیگ ثابت است؛ فقط ویرایش مجاز است.", show_alert=True)
            return
        elif action == "edit":
            lg = await db.get_league(int(parts[2]))
            if not lg:
                await call.answer("لیگ پیدا نشد.", show_alert=True); return
            await call.message.answer(f"ویرایش لیگ #{lg['id']} — {lg['name']}", reply_markup=admin_league_edit_keyboard(lg['id']))
        elif action == "delete":
            await call.answer("حذف لیگ مجاز نیست؛ ساختار 4 لیگ × 3 تیر + لیگ نهایی ثابت است.", show_alert=True)
            return
        await call.answer()
    except Exception:
        logger.exception("League callback failed")
        await call.answer("خطا", show_alert=True)


@router.message(LeagueFlow.name, F.text)
async def league_name(message: Message, state: FSMContext, db: Database) -> None:
    if not await require_admin_message(message, db): return
    await state.update_data(name=message.text.strip())
    await state.set_state(LeagueFlow.min_cups)
    await message.answer("آستانه کاپ ورود به این لیگ را عددی وارد کنید:", reply_markup=cancel_keyboard())


@router.message(LeagueFlow.min_cups, F.text)
async def league_min(message: Message, state: FSMContext, db: Database) -> None:
    try:
        if not await require_admin_message(message, db): return
        await state.update_data(min_cups=int(message.text.strip()))
        await state.set_state(LeagueFlow.win_cups)
        await message.answer("مقدار کاپ برد در این لیگ را وارد کنید:", reply_markup=cancel_keyboard())
    except ValueError:
        await message.answer("عدد معتبر وارد کنید.")


@router.message(LeagueFlow.win_cups, F.text)
async def league_win(message: Message, state: FSMContext, db: Database) -> None:
    try:
        if not await require_admin_message(message, db): return
        await state.update_data(win_cups=int(message.text.strip()))
        await state.set_state(LeagueFlow.loss_cups)
        await message.answer("مقدار کاپ باخت را وارد کنید؛ مثال -10 یا 0:", reply_markup=cancel_keyboard())
    except ValueError:
        await message.answer("عدد معتبر وارد کنید.")


@router.message(LeagueFlow.loss_cups, F.text)
async def league_loss(message: Message, state: FSMContext, db: Database) -> None:
    try:
        if not await require_admin_message(message, db): return
        data = await state.get_data()
        lid = await db.add_league(data['name'], int(data['min_cups']), int(data['win_cups']), int(message.text.strip()))
        await db.log_admin(message.from_user.id, "league_add", str(lid))
        await state.clear()
        await message.answer("لیگ اضافه شد.", reply_markup=admin_leagues_keyboard(await db.all_leagues()))
    except ValueError:
        await message.answer("عدد معتبر وارد کنید.")
    except Exception:
        logger.exception("League add failed")
        await message.answer("خطا در افزودن لیگ. احتمالاً آستانه کاپ تکراری است.")


@router.callback_query(F.data.startswith("league_edit:"))
async def league_edit_start(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db): return
        await state.clear()
        _, field, lid = call.data.split(":")
        map_state = {"name": LeagueFlow.edit_name, "min": LeagueFlow.edit_min_cups, "win": LeagueFlow.edit_win_cups, "loss": LeagueFlow.edit_loss_cups}
        await state.set_state(map_state[field])
        await state.update_data(league_id=int(lid), league_field=field)
        prompt = {"name": "نام جدید:", "min": "آستانه کاپ جدید:", "win": "کاپ برد جدید:", "loss": "کاپ باخت جدید:"}[field]
        await call.message.answer(prompt, reply_markup=cancel_keyboard())
        await call.answer()
    except Exception:
        logger.exception("League edit start failed")
        await call.answer("خطا", show_alert=True)


@router.message(LeagueFlow.edit_name, F.text)
@router.message(LeagueFlow.edit_min_cups, F.text)
@router.message(LeagueFlow.edit_win_cups, F.text)
@router.message(LeagueFlow.edit_loss_cups, F.text)
async def league_edit_save(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db): return
        data = await state.get_data()
        field_map = {"name": "name", "min": "min_cups", "win": "win_cups", "loss": "loss_cups"}
        field = field_map[data['league_field']]
        value = message.text.strip() if field == 'name' else int(message.text.strip())
        await db.update_league_field(int(data['league_id']), field, value)
        await db.log_admin(message.from_user.id, "league_edit", str(data['league_id']), f"{field}={value}")
        await state.clear()
        await message.answer("لیگ ویرایش شد.", reply_markup=admin_leagues_keyboard(await db.all_leagues()))
    except ValueError:
        await message.answer("عدد معتبر وارد کنید.")
    except Exception:
        logger.exception("League edit save failed")
        await message.answer("خطا در ویرایش لیگ. احتمالاً آستانه کاپ تکراری است.")


@router.message(AdminFlow.waiting_start_photo)
async def start_photo_save(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        if message.text and message.text.strip() == "/remove_photo":
            await db.set_setting("start_photo_file_id", "")
            await db.log_admin(message.from_user.id, "start_photo_remove")
            await state.clear()
            await message.answer("عکس استارت حذف شد.", reply_markup=main_menu(True))
            return
        if not message.photo:
            await message.answer("لطفاً عکس ارسال کنید یا /remove_photo را بفرستید.")
            return
        file_id = message.photo[-1].file_id
        await db.set_setting("start_photo_file_id", file_id)
        await db.log_admin(message.from_user.id, "start_photo_update")
        await state.clear()
        await message.answer("عکس استارت ذخیره شد.", reply_markup=main_menu(True))
    except Exception:
        logger.exception("Start photo save failed")
        await message.answer("خطا در ذخیره عکس استارت.")


@router.callback_query(F.data.startswith("discount:"))
async def discount_callback(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        await state.clear()
        _, action, *rest = call.data.split(":")
        if action == "add":
            await state.set_state(DiscountFlow.code)
            await call.message.answer("کد تخفیف را وارد کنید (مثلاً OFF20):", reply_markup=cancel_keyboard())
        elif action == "disable":
            await db.disable_discount(int(rest[0]))
            await db.log_admin(call.from_user.id, "discount_disable", rest[0])
            await call.message.answer("کد تخفیف غیرفعال شد.", reply_markup=admin_discounts_keyboard(await db.discounts()))
        await call.answer()
    except Exception:
        logger.exception("Discount callback failed")
        await call.answer("خطا", show_alert=True)


@router.message(DiscountFlow.code, F.text)
async def discount_code(message: Message, db: Database, state: FSMContext) -> None:
    if not await require_admin_message(message, db): return
    await state.update_data(code=message.text.strip().upper())
    await state.set_state(DiscountFlow.kind)
    await message.answer("نوع تخفیف را انتخاب کنید:", reply_markup=discount_kind_keyboard())


@router.callback_query(F.data.startswith("discount_kind:"))
async def discount_kind(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    if not await require_admin_call(call, db): return
    kind = call.data.split(":")[1]
    await state.update_data(kind=kind)
    await state.set_state(DiscountFlow.value)
    await call.message.answer("مقدار تخفیف را عددی وارد کنید؛ برای درصد مثلاً 20، برای مبلغ ثابت مثلاً 50000:", reply_markup=cancel_keyboard())
    await call.answer()


@router.message(DiscountFlow.value, F.text)
async def discount_value(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db): return
        value = int(message.text.strip())
        if value <= 0: raise ValueError
        data = await state.get_data()
        if data.get("kind") == "percent" and value > 100:
            await message.answer("درصد باید بین 1 تا 100 باشد.")
            return
        await state.update_data(value=value)
        await state.set_state(DiscountFlow.max_uses)
        await message.answer("حداکثر تعداد استفاده را عددی وارد کنید؛ برای نامحدود 0 بفرستید:", reply_markup=cancel_keyboard())
    except ValueError:
        await message.answer("عدد معتبر وارد کنید.")


@router.message(DiscountFlow.max_uses, F.text)
async def discount_max_uses(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db): return
        max_uses = int(message.text.strip())
        await state.update_data(max_uses=None if max_uses <= 0 else max_uses)
        await state.set_state(DiscountFlow.expires_at)
        await message.answer("تاریخ انقضا را به فرمت ISO بفرستید مثل 2026-12-31T23:59:00+00:00؛ برای بدون انقضا 0 بفرستید:", reply_markup=cancel_keyboard())
    except ValueError:
        await message.answer("عدد معتبر وارد کنید.")


@router.message(DiscountFlow.expires_at, F.text)
async def discount_expires(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db): return
        data = await state.get_data()
        expires = None if message.text.strip() == "0" else message.text.strip()
        did = await db.create_discount(message.from_user.id, data['code'], data['kind'], int(data['value']), data.get('max_uses'), expires)
        await db.log_admin(message.from_user.id, "discount_add", str(did))
        await state.clear()
        await message.answer("کد تخفیف ساخته شد.", reply_markup=admin_discounts_keyboard(await db.discounts()))
    except Exception:
        logger.exception("Discount create failed")
        await message.answer("خطا در ساخت کد تخفیف. شاید کد تکراری است.")


@router.callback_query(F.data.startswith("qadmin_mode:"))
async def question_admin_mode(call: CallbackQuery, db: Database) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        mode = call.data.split(":", 1)[1]
        status = "pending" if mode == "pending" else "active"
        title = "سوالات در صف بررسی" if mode == "pending" else "جستجوی سوالات تاییدشده"
        counts = await db.question_genre_counts(status)
        await call.message.answer(f"{title}: ژانر را انتخاب کن:", reply_markup=question_genres_keyboard(counts, mode))
        await call.answer()
    except Exception:
        logger.exception("Question admin mode failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("qsearch:"))
async def qsearch_page_callback(call: CallbackQuery, db: Database) -> None:
    await call.answer()
    try:
        _, page_s, query = call.data.split(":", 2)
        results = await db.search_questions(query, int(page_s))
        if not results:
            await call.message.answer("❌ سوالی با این مشخصات پیدا نشد")
            return
        lines = [f"🔍 نتایج جستجو ({len(results)} مورد):"]
        for i, q in enumerate(results, 1):
            status = "✅ تأییدشده" if q['status'] == 'active' else q['status']
            lines.append(f"\n{i}. ID: {q['id']}\n❓ {q['text']}\n🏷 ژانر: {q['genre']} | {status}")
        await call.message.edit_text("\n".join(lines), reply_markup=question_search_results_keyboard(results, int(page_s), query))
    except Exception:
        logger.exception("Qsearch page failed")
        await call.message.answer("خطا در صفحه‌بندی جستجو.")


@router.callback_query(F.data.startswith("qedit:"))
async def qedit_callback(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await call.answer()
    try:
        if not await require_admin_call(call, db):
            return
        _, action, qid_s = call.data.split(":")
        qid = int(qid_s)
        await state.update_data(edit_qid=qid)
        if action == "text":
            await state.set_state(QuestionEditFlow.text)
            await call.message.answer("متن جدید صورت سوال را بفرست:", reply_markup=cancel_keyboard())
        elif action == "options":
            await state.set_state(QuestionEditFlow.option1)
            await call.message.answer("گزینه الف را بفرست:", reply_markup=cancel_keyboard())
        elif action == "genre":
            await call.message.answer("ژانر جدید را انتخاب کن:", reply_markup=genre_edit_keyboard(qid, await db.all_genres()))
    except Exception:
        logger.exception("Qedit callback failed")
        await call.message.answer("خطا در شروع ویرایش.")


@router.message(QuestionEditFlow.text, F.text)
async def qedit_text_save(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db): return
        data = await state.get_data()
        await db.update_question_text(int(data['edit_qid']), message.text.strip())
        await state.clear()
        await message.answer("✅ متن سوال ویرایش شد.")
    except Exception:
        logger.exception("Qedit text failed")
        await message.answer("خطا در ویرایش متن.")


@router.message(QuestionEditFlow.option1, F.text)
async def qedit_o1(message: Message, state: FSMContext, db: Database) -> None:
    if not await require_admin_message(message, db): return
    await state.update_data(o1=message.text.strip())
    await state.set_state(QuestionEditFlow.option2)
    await message.answer("گزینه ب را بفرست:")


@router.message(QuestionEditFlow.option2, F.text)
async def qedit_o2(message: Message, state: FSMContext, db: Database) -> None:
    if not await require_admin_message(message, db): return
    await state.update_data(o2=message.text.strip())
    await state.set_state(QuestionEditFlow.option3)
    await message.answer("گزینه ج را بفرست:")


@router.message(QuestionEditFlow.option3, F.text)
async def qedit_o3(message: Message, state: FSMContext, db: Database) -> None:
    if not await require_admin_message(message, db): return
    await state.update_data(o3=message.text.strip())
    await state.set_state(QuestionEditFlow.option4)
    await message.answer("گزینه د را بفرست:")


@router.message(QuestionEditFlow.option4, F.text)
async def qedit_o4(message: Message, state: FSMContext, db: Database) -> None:
    if not await require_admin_message(message, db): return
    await state.update_data(o4=message.text.strip())
    await state.set_state(QuestionEditFlow.correct)
    await message.answer("کدام گزینه درست است؟ (الف/ب/ج/د)")


@router.message(QuestionEditFlow.correct, F.text)
async def qedit_correct(message: Message, state: FSMContext, db: Database) -> None:
    try:
        if not await require_admin_message(message, db): return
        mapping = {"الف": 1, "ب": 2, "ج": 3, "د": 4, "a": 1, "b": 2, "c": 3, "d": 4, "1": 1, "2": 2, "3": 3, "4": 4}
        key = message.text.strip().lower()
        if key not in mapping:
            await message.answer("فقط الف/ب/ج/د یا 1 تا 4 قابل قبول است.")
            return
        data = await state.get_data()
        await db.update_question_options(int(data['edit_qid']), [data['o1'], data['o2'], data['o3'], data['o4']], mapping[key])
        await state.clear()
        await message.answer("✅ گزینه‌ها و جواب درست ویرایش شدند.")
    except Exception:
        logger.exception("Qedit options failed")
        await message.answer("خطا در ویرایش گزینه‌ها.")


@router.callback_query(F.data.startswith("qedit_genre:"))
async def qedit_genre_save(call: CallbackQuery, db: Database) -> None:
    await call.answer()
    try:
        _, qid_s, genre = call.data.split(":", 2)
        await db.update_question_genre(int(qid_s), genre)
        await call.message.answer(f"✅ ژانر سوال به {genre} تغییر کرد.")
    except Exception:
        logger.exception("Qedit genre failed")
        await call.message.answer("خطا در ویرایش ژانر.")


@router.callback_query(F.data.startswith("qadmin:"))
async def question_admin_callback(call: CallbackQuery, db: Database) -> None:
    try:
        if not await require_admin_call(call, db): return
        parts = call.data.split(":", 3)
        action = parts[1]
        if action == "genre":
            mode = parts[2]
            genre = parts[3]
            status = "pending" if mode == "pending" else "active"
            qs = await db.questions_by_genre(genre, status)
            title = "در صف بررسی" if mode == "pending" else "تاییدشده"
            await call.message.answer(f"سوالات {title} ژانر {genre}:", reply_markup=pending_questions_keyboard(qs, genre, mode))
        elif action == "view":
            qid = int(parts[2])
            text = await format_question_admin_text(db, qid)
            if not text:
                await call.answer("سوال پیدا نشد.", show_alert=True); return
            await call.message.answer(text, reply_markup=question_admin_actions_keyboard(qid))
        await call.answer()
    except Exception:
        logger.exception("Question admin callback failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data == "qcleanup:confirm")
async def question_cleanup_confirm(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_call(call, db): return
        count = await db.delete_invalid_genre_questions()
        await db.log_admin(call.from_user.id, "question_cleanup_invalid", details=str(count))
        await state.clear()
        await call.message.answer(f"{count} سوال با ژانر نامعتبر حذف شد.", reply_markup=admin_panel())
        await call.answer()
    except Exception:
        logger.exception("Question cleanup failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("qact:"))
async def question_action_callback(call: CallbackQuery, db: Database) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        _, action, qid_s = call.data.split(":")
        qid = int(qid_s)
        if action == "delete":
            await db.delete_question(qid)
            await db.log_admin(call.from_user.id, "question_delete", str(qid))
            await call.message.answer(f"سوال #{qid} حذف شد.")
        elif action == "disable":
            await db.deactivate_question(qid)
            await db.log_admin(call.from_user.id, "question_disable", str(qid))
            await call.message.answer(f"سوال #{qid} غیرفعال شد.")
        elif action == "edit":
            await call.message.answer("ویرایش متنی هنوز مرحله‌ای نشده؛ فعلاً از حذف/غیرفعال و ثبت مجدد استفاده کن.")
        await call.answer()
    except Exception:
        logger.exception("Question action failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("report_ignore:"))
async def report_ignore_callback(call: CallbackQuery, db: Database) -> None:
    try:
        if not await require_admin_call(call, db):
            return
        rid = int(call.data.split(":")[1])
        await db.execute_write("UPDATE question_reports SET status='ignored' WHERE id=?", (rid,))
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("نادیده گرفته شد.")
    except Exception:
        logger.exception("Report ignore failed")
        await call.answer("خطا", show_alert=True)


@router.message(Command("titles"))
async def titles_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        await message.answer("🏅 مدیریت لقب‌ها", reply_markup=titles_menu_keyboard())
    except Exception:
        logger.exception("Titles command failed")
        await message.answer("خطا در مدیریت لقب‌ها.")


@router.callback_query(F.data.startswith("title:"))
async def title_callback(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await call.answer()
    try:
        if not await require_admin_call(call, db):
            return
        action = call.data.split(":", 1)[1]
        if action == "add":
            await state.set_state(TitleFlow.name)
            await call.message.answer("نام لقب را وارد کن؛ مثال: شکارچی", reply_markup=cancel_keyboard())
        elif action == "list":
            rows = await db.titles()
            text = "📋 لقب‌های تعریف‌شده:\n\n" + ("\n".join(f"#{r['id']} {r['emoji'] or ''} {r['name']} — از لول {r['min_level']}" for r in rows) or "هنوز لقبی تعریف نشده.")
            await call.message.answer(text, reply_markup=titles_menu_keyboard())
        elif action == "delete_help":
            await call.message.answer("برای حذف لقب بزن: <code>/deltitle ID</code>")
    except Exception:
        logger.exception("Title callback failed")
        await call.message.answer("خطا در مدیریت لقب.")


@router.message(TitleFlow.name, F.text)
async def title_name_step(message: Message, db: Database, state: FSMContext) -> None:
    if not await require_admin_message(message, db):
        return
    await state.update_data(title_name=message.text.strip())
    await state.set_state(TitleFlow.emoji)
    await message.answer("ایموجی لقب را وارد کن؛ مثال: ⚔️", reply_markup=cancel_keyboard())


@router.message(TitleFlow.emoji, F.text)
async def title_emoji_step(message: Message, db: Database, state: FSMContext) -> None:
    if not await require_admin_message(message, db):
        return
    await state.update_data(title_emoji=message.text.strip())
    await state.set_state(TitleFlow.min_level)
    await message.answer("حداقل لول برای دریافت لقب را عددی وارد کن؛ مثال: 5", reply_markup=cancel_keyboard())


@router.message(TitleFlow.min_level, F.text)
async def title_min_level_step(message: Message, db: Database, state: FSMContext) -> None:
    if not await require_admin_message(message, db):
        return
    try:
        lvl = int(message.text.strip())
        await state.update_data(title_min_level=lvl)
        await state.set_state(TitleFlow.description)
        await message.answer("توضیح کوتاه لقب را وارد کن؛ اگر توضیح نمی‌خواهی 0 بفرست.", reply_markup=cancel_keyboard())
    except ValueError:
        await message.answer("لطفاً عدد معتبر وارد کن.")


@router.message(TitleFlow.description, F.text)
async def title_description_step(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        data = await state.get_data()
        desc = None if message.text.strip() == "0" else message.text.strip()
        tid = await db.add_title(data['title_name'], data['title_emoji'], int(data['title_min_level']), desc)
        await db.log_admin(message.from_user.id, "title_add", str(tid))
        await state.clear()
        await message.answer(f"✅ لقب #{tid} ساخته شد.", reply_markup=titles_menu_keyboard())
    except Exception:
        logger.exception("Title create failed")
        await message.answer("خطا در ساخت لقب.")


@router.message(Command("deltitle"))
async def delete_title_command(message: Message, db: Database) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("فرمت درست: /deltitle ID")
            return
        await db.delete_title(int(parts[1]))
        await db.log_admin(message.from_user.id, "title_delete", parts[1])
        await message.answer("لقب حذف شد.", reply_markup=titles_menu_keyboard())
    except Exception:
        logger.exception("Delete title failed")
        await message.answer("خطا در حذف لقب.")


@router.callback_query(F.data.startswith("animprev:"))
async def animation_preview_callback(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        if not await require_admin_call(call, db):
            return
        kind = call.data.split(":", 1)[1]
        if kind == "level":
            await run_edit_animation(bot, call.from_user.id, await levelup_steps(5, 6), 0.6)
        elif kind == "rank":
            await run_edit_animation(bot, call.from_user.id, await rankup_steps("🥉 برنزی 3", "🥈 نقره‌ای 1", 5, 6, True), 0.6)
        elif kind == "title":
            await run_edit_animation(bot, call.from_user.id, await title_steps(db, "بدون لقب", "⚔️ شکارچی", "🥉 برنزی 3", "🥈 نقره‌ای 1", 5, 6, True, True), 0.6)
        elif kind == "down":
            await run_edit_animation(bot, call.from_user.id, await demotion_steps("🥈 نقره‌ای 1", "🥉 برنزی 3"), 0.6)
    except Exception:
        logger.exception("Animation preview failed")
        await call.message.answer("خطا در پیش‌نمایش انیمیشن.")
