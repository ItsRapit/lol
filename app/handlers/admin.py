import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from app.db import Database, now_iso
from app.keyboards import (
    admin_panel, settings_keyboard, user_admin_keyboard, main_menu, cancel_keyboard,
    admin_shop_types_keyboard, admin_shop_packages_keyboard, admin_shop_edit_keyboard,
    admin_leagues_keyboard, admin_league_edit_keyboard, admin_discounts_keyboard,
    discount_kind_keyboard, question_manage_keyboard, question_genres_keyboard, pending_questions_keyboard,
    invalid_questions_confirm_keyboard, review_question_keyboard,
)
from app.states import AdminFlow, BulkQuestionImport, ShopPackageFlow, LeagueFlow, DiscountFlow, QuestionCleanupFlow
from app.bulk_questions import parse_bulk_questions, format_bulk_report, bulk_help_text, extract_json_text, is_json_balanced, looks_like_json, looks_like_bulk_text
from app.time_utils import tehran_now, jalali_datetime

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
            await db.set_setting("question_approval_reward_coins", "20")
            await db.set_setting("powerup_5050_cost", "5")
            await db.set_setting("powerup_hint_cost", "5")
            await db.set_setting("powerup_max_uses_per_duel", "5")
            await db.set_setting("streak_day_1_coins", "5")
            await db.set_setting("streak_day_2_coins", "10")
            await db.set_setting("streak_day_3_coins", "15")
            await db.set_setting("streak_day_4_coins", "20")
            await db.set_setting("streak_day_5_coins", "25")
            await db.set_setting("streak_day_6_coins", "30")
            await db.set_setting("streak_day_7_coins", "50")
            await db.set_setting("streak_day_7_xp", "0")
        genres = await db.all_genres()
        p5050 = await db.get_setting("powerup_5050_cost", "?")
        phint = await db.get_setting("powerup_hint_cost", "?")
        pmax = await db.get_setting("powerup_max_uses_per_duel", "?")
        qreward = await db.get_setting("question_approval_reward_coins", "?")
        await db.log_admin(message.from_user.id, "sync_defaults", details="force" if force else "normal")
        await message.answer(
            "✅ همگام‌سازی پیش‌فرض‌ها انجام شد.\n\n"
            f"ژانرها ({len(genres)}): {', '.join(genres)}\n\n"
            f"پاداش تایید سوال: {qreward} سکه\n"
            f"قیمت 50:50: {p5050}\n"
            f"قیمت کمک: {phint}\n"
            f"حداکثر پاورآپ در هر دست: {pmax}\n\n"
            "اگر می‌خواهی مقادیر جدید حتماً جایگزین قبلی شوند، بزن:\n"
            "<code>/sync_defaults force</code>"
        )
    except Exception:
        logger.exception("Sync defaults failed")
        await message.answer("خطا در sync defaults.")


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
            await call.message.answer("پنل ادمین:", reply_markup=admin_panel())
        elif action == 'stats':
            s = await db.stats()
            await call.message.answer(
                "📊 آمار کامل ربات\n\n"
                f"👥 کل کاربران: {s['users']}\n"
                f"🆕 کاربران جدید امروز: {s['new_users_today']}\n\n"
                f"⚔️ کل دوئل‌ها: {s['duels']}\n"
                f"✅ دوئل‌های تمام‌شده: {s['finished_duels']}\n"
                f"🎮 بازی‌های امروز: {s['games_today']}\n\n"
                f"💰 درآمد این هفته: {s['revenue_week']:,} تومان\n"
                f"💰 درآمد این ماه: {s['revenue_month']:,} تومان\n"
                f"💰 درآمد این سال: {s['revenue_year']:,} تومان\n"
                f"🧾 خریدهای تاییدشده: {s['approved_transactions']}\n\n"
                f"🪙 کوین تولید شده: {s['coins_generated']}\n"
                f"🔥 کوین حذف/مصرف شده: {s['coins_burned']}\n\n"
                f"❓ کل سوالات: {s['total_questions']}\n"
                f"👤 سوالات ثبت‌شده توسط کاربر: {s['user_questions']}\n"
                f"🛡 سوالات ثبت‌شده توسط ادمین: {s['admin_questions']}\n"
                f"⏳ سوالات در صف بررسی: {s['pending_questions']}"
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


@router.message(AdminFlow.waiting_user_id, F.text)
async def user_lookup(message: Message, db: Database, state: FSMContext) -> None:
    try:
        if not await require_admin_message(message, db):
            return
        tg_id = int(message.text.strip())
        u = await db.get_user(tg_id)
        await state.clear()
        if not u:
            await message.answer("کاربر پیدا نشد.")
            return
        rank = await db.get_rank_title(u['level'])
        league = await db.get_user_league(u['cups'])
        await message.answer(
            f"👤 کاربر <code>{tg_id}</code>\nUsername: @{u['username'] or '-'}\nCoins: {u['coins']}\nXP: {u['xp']}\nLevel: {u['level']} — {rank}\nCups: {u['cups']} | League: {league['name'] if league else '-'}\nBlocked: {bool(u['is_blocked'])}\nWins/Losses/Draws: {u['wins']}/{u['losses']}/{u['draws']}",
            reply_markup=user_admin_keyboard(tg_id),
        )
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
            q = await db.get_question(int(parts[2]))
            if not q:
                await call.answer("سوال پیدا نشد.", show_alert=True); return
            text = f"سوال #{q['id']}\nژانر: {q['genre']}\nتاریخ ثبت: {jalali_datetime(q['created_at'])}\n\n{q['text']}\n1) {q['option1']}\n2) {q['option2']}\n3) {q['option3']}\n4) {q['option4']}\nCorrect: {q['correct_option']}"
            await call.message.answer(text, reply_markup=review_question_keyboard(q['id']))
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
