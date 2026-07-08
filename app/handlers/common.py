import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from app.db import Database, now_iso
from app.keyboards import main_menu, leaderboard_basis_keyboard, leaderboard_period_keyboard, CANCEL_TEXT, back_home_keyboard, quests_keyboard
from app.utils import ensure_user, xp_progress_text, rtl_line, to_english_digits, league_with_emoji, rank_with_emoji
from app.notifications import send_streak_notification
from app.time_utils import jalali_date, jalali_datetime
from app.profile_view import build_profile_text

logger = logging.getLogger(__name__)
router = Router()


def quest_progress_hint(goal_type: str, remaining: int) -> str:
    if remaining <= 0:
        return "تمومش کن، جایزه‌ات آماده‌ست"
    if goal_type == "win_duels":
        return f"فقط {remaining} تا دوئل دیگه ببر"
    if goal_type == "start_duels":
        return f"فرقی نمی‌کنه ببری یا نه، {remaining} تا دوئل دیگه بازی کن"
    if goal_type == "group_first_place":
        return f"{remaining} بار دیگه تو بازی گروهی نفر اول شو"
    if goal_type == "correct_answers":
        return f"{remaining} تا جواب درست دیگه بده، تو هر بازی‌ای حساب میشه"
    if goal_type == "play_group_games":
        return f"{remaining} بازی گروهی دیگه بازی کن، فقط شرکت کنی کافیه"
    return f"{remaining} تا مونده"


async def check_force_join_private(message: Message, db: Database, bot: Bot) -> bool:
    if await db.is_admin(message.from_user.id):
        return True
    enabled = await db.get_int("force_join_enabled", 0)
    channel = await db.get_setting("force_join_channel", "")
    if not enabled or not channel:
        return True
    try:
        member = await bot.get_chat_member(channel, message.from_user.id)
        if member.status not in {"left", "kicked", "banned"}:
            return True
    except Exception:
        logger.exception("Private force join check failed; allowing")
        return True
    buttons = []
    if channel.startswith("@"):
        buttons.append([InlineKeyboardButton(text="عضویت توی کانال", url=f"https://t.me/{channel.lstrip('@')}")])
    buttons.append([InlineKeyboardButton(text="✅ بررسی عضویت", callback_data="check_force_join")])
    await message.answer(
        "برای استفاده از ربات اول باید عضو کانال اسپانسر شوید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    return False


@router.message(CommandStart())
async def start(message: Message, db: Database, state: FSMContext, bot: Bot, command: CommandObject | None = None) -> None:
    await state.clear()
    payload = command.args if command else None
    try:
        if not await check_force_join_private(message, db, bot):
            return
        was_new = await db.get_user(message.from_user.id) is None
        await ensure_user(db, message.from_user, payload)
        is_admin = await db.is_admin(message.from_user.id)
        signup_gift = 0
        if was_new:
            signup_gift = await db.get_int("initial_signup_coins", 50)
            if signup_gift > 0:
                await db.change_coins(message.from_user.id, signup_gift, "initial_signup")
        streak_reward = await db.claim_streak_reward(message.from_user.id)
        await db.get_today_quests(message.from_user.id)
        welcome = await db.get_setting("welcome_text", "سلام! به ربات کوییز دوئلی خوش آمدی. از منوی پایین انتخاب کن:")
        if was_new and signup_gift > 0:
            welcome += f"\n\n🎁 {signup_gift}تا سکه برای شروع در اختیار شما قرار گرفت."
        photo_id = await db.get_setting("start_photo_file_id", "")
        if photo_id:
            await message.answer_photo(photo_id, caption=welcome, reply_markup=main_menu(is_admin))
        else:
            await message.answer(welcome, reply_markup=main_menu(is_admin))
        await send_streak_notification(bot, message.from_user.id, streak_reward)
    except Exception:
        logger.exception("Start failed")
        await message.answer("خطایی رخ داد. لطفاً دوباره تلاش کن.")


@router.message(Command("version"))
async def version_command_public(message: Message, db: Database) -> None:
    if not await db.is_admin(message.from_user.id):
        await message.answer("❌ این دستور فقط برای ادمین‌هاست.")
        return
    await message.answer(
        "🧩 نسخه کد فعال: <code>challeshino-group-inline-profile-v3</code>\n"
        "اگر این پیام را می‌بینی یعنی کد جدید روی همین بات فعال است."
    )


@router.message(Command("help"))
async def help_command(message: Message, db: Database) -> None:
    try:
        await message.answer(await db.render_help_text(), reply_markup=ReplyKeyboardRemove())
    except Exception:
        logger.exception("Help failed")
        await message.answer("خطا در نمایش راهنما.")


@router.message(Command("cancel"))
@router.message(F.text == CANCEL_TEXT)
async def cancel(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    try:
        from app.handlers.duel import queue_timeout_tasks
        duel = await db.active_duel_for_user(message.from_user.id)
        if duel and duel['status'] == 'waiting' and duel['player1_id'] == message.from_user.id:
            task = queue_timeout_tasks.pop(duel['id'], None)
            if task and not task.done():
                task.cancel()
            cost = await db.get_int('random_duel_cost', 5)
            await db.execute_write("UPDATE duels SET status='cancelled', finished_at=? WHERE id=?", (now_iso(), duel['id']))
            await db.change_coins(message.from_user.id, cost, 'random_duel_queue_cancel_refund', duel['id'])
            await message.answer(f"از صف خارج شدی و {cost} سکه به حسابت برگشت")
    except Exception:
        logger.exception("Cancel active queue on /cancel failed")
    is_admin = await db.is_admin(message.from_user.id)
    await message.answer("منوی اصلی", reply_markup=main_menu(is_admin))


@router.callback_query(F.data == "nav:home")
async def nav_home(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    is_admin = await db.is_admin(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("Could not clear nav markup", exc_info=True)
    await call.message.answer("منوی اصلی", reply_markup=main_menu(is_admin))
    await call.answer()


@router.message(F.text == "👤 پروفایل")
async def profile(message: Message, db: Database) -> None:
    try:
        await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name, from_pv=True)
        await message.answer(await build_profile_text(db, message.from_user.id))
    except Exception:
        logger.exception("Profile failed")
        await message.answer("خطا در نمایش پروفایل.")


@router.message(F.text == "🏆 لیدربورد")
async def leaderboard_entry(message: Message) -> None:
    await message.answer("🏆 لیدربرد\nلیدربرد را بر چه اساسی ببینی؟", reply_markup=leaderboard_basis_keyboard())


@router.callback_query(F.data.startswith("lb_basis:"))
async def leaderboard_basis(call: CallbackQuery) -> None:
    basis = call.data.split(":", 1)[1]
    await call.message.edit_text("بازه زمانی را انتخاب کن:", reply_markup=leaderboard_period_keyboard(basis))
    await call.answer()


@router.callback_query(F.data == "lb_back:basis")
async def leaderboard_back(call: CallbackQuery) -> None:
    await call.message.edit_text("🏆 لیدربرد\nلیدربرد را بر چه اساسی ببینی؟", reply_markup=leaderboard_basis_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("lb:"))
async def leaderboard_callback(call: CallbackQuery, db: Database) -> None:
    try:
        _, basis, period = call.data.split(":")
        rows = await db.leaderboard(basis, period)
        medals = ["🥇", "🥈", "🥉"]
        if basis == "league":
            text = "🏆 لیدربرد لیگ\n\n"
            if not rows:
                text += "هنوز رتبه‌ای ثبت نشده است.\n"
            for i, r in enumerate(rows[:10], 1):
                raw_name = r['first_name'] or (('@' + r['username']) if r['username'] else str(r['telegram_id']))
                name = raw_name if len(raw_name) <= 20 else raw_name[:20] + "..."
                prefix = medals[i - 1] if i <= 3 else f"#{i}"
                cups_value = int(r['score'] if period != 'all' else r['cups'])
                text += rtl_line(f"{prefix} \u2068{name}\u2069 — {league_with_emoji(r['league_name'])} — 🏆 {cups_value}") + "\n"
            me = await db.leaderboard_user_position(call.from_user.id, basis, period)
            if me:
                text += (
                    "\n━━━━━━━━━━━━━━\n\n"
                    "📍 رتبه شما:\n"
                    f"#{me['rank']}\n\n"
                    f"👑 لیگ شما: {league_with_emoji(me['league_name'])}\n"
                    f"🏆 جام شما: {me['cups']}\n"
                    "━━━━━━━━━━━━━━"
                )
        else:
            text = "🏆 لیدربرد XP\n\n"
            if not rows:
                text += "هنوز رتبه‌ای ثبت نشده است.\n"
            for i, r in enumerate(rows[:10], 1):
                raw_name = r['first_name'] or (('@' + r['username']) if r['username'] else str(r['telegram_id']))
                name = raw_name if len(raw_name) <= 20 else raw_name[:20] + "..."
                prefix = medals[i - 1] if i <= 3 else f"#{i}"
                xp_value = int(r['score'] if period != 'all' else (r['xp'] if 'xp' in r.keys() else r['score']))
                text += rtl_line(f"{prefix} \u2068{name}\u2069 — Level {r['level']} — XP {xp_value:,}") + "\n"
            me = await db.leaderboard_user_position(call.from_user.id, basis, period)
            if me:
                text += (
                    "\n━━━━━━━━━━━━━━\n\n"
                    "📍 رتبه شما:\n"
                    f"#{me['rank']}\n\n"
                    f"🎖 Level: {me['level']}\n"
                    f"⭐ XP: {me['xp']:,}\n"
                    "━━━━━━━━━━━━━━"
                )
        await call.message.edit_text(text, reply_markup=leaderboard_period_keyboard(basis))
        await call.answer()
    except Exception:
        logger.exception("Leaderboard failed")
        await call.answer("خطا", show_alert=True)


@router.message(F.text == "🎁 رفرال")
async def referral(message: Message, db: Database, bot_username: str) -> None:
    await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name, from_pv=True)
    link = f"https://t.me/{bot_username}?start=ref_{message.from_user.id}"
    rc = await db.get_int("referral_referrer_coins", 50)
    rx = await db.get_int("referral_referrer_xp", 50)
    nc = await db.get_int("referral_referred_coins", 25)
    nx = await db.get_int("referral_referred_xp", 25)
    await message.answer(
        "🎁 لینک دعوت اختصاصی شما:\n"
        f"{link}\n\n"
        f"اگر دوستت با لینک تو وارد بشه و اولین دوئلش رو بازی کنه، تو <b>{rc} سکه و {rx} XP</b> می‌گیری، اون هم <b>{nc} سکه و {nx} XP</b> هدیه می‌گیره.",
        )


@router.message(F.text == "🎯 کوئست روزانه")
async def daily_quests(message: Message, db: Database) -> None:
    u = await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name, from_pv=True)
    if u['is_blocked']:
        await message.answer("حساب شما مسدود است")
        return
    quests = await db.get_today_quests(message.from_user.id)
    if not quests:
        await message.answer("امروز کوئستی برات نداریم بعدا سر بزن")
        return
    lines = ["🎯 کوئست‌های امروزت\n"]
    for q in quests:
        progress = min(int(q['progress']), int(q['goal_count']))
        goal = int(q['goal_count'])
        remaining = max(0, goal - progress)
        if q['claimed']:
            mark = "✅"
            status = "تکمیل شد"
        elif q['completed']:
            mark = "✅"
            status = "آماده دریافت"
        else:
            mark = "⬜"
            status = f"{progress}/{goal}"
        hint = q['description'] if q['claimed'] or q['completed'] else quest_progress_hint(q['goal_type'], remaining)
        lines.append(f"{mark} {q['title']} — {status}\n{hint}\n🎁 {q['reward_coins']} سکه + {q['reward_xp']} XP")
    await message.answer("\n\n".join(lines), reply_markup=quests_keyboard(quests))


@router.callback_query(F.data == "quest_claim_all")
async def quest_claim_all(call: CallbackQuery, db: Database) -> None:
    try:
        result = await db.claim_all_quest_rewards(call.from_user.id)
        if not result:
            await call.answer("جایزه‌ای در دسترس نیست", show_alert=True)
            return
        await call.answer(f"🎉 جایزه دریافت شد\n+{result['coins']} سکه\n+{result['xp']} XP", show_alert=True)
        quests = await db.get_today_quests(call.from_user.id)
        lines = ["🎯 کوئست‌های امروزت\n"]
        for q in quests:
            progress = min(int(q['progress']), int(q['goal_count']))
            goal = int(q['goal_count'])
            remaining = max(0, goal - progress)
            if q['claimed']:
                mark = "✅"
                status = "تکمیل شد"
            elif q['completed']:
                mark = "✅"
                status = "آماده دریافت"
            else:
                mark = "⬜"
                status = f"{progress}/{goal}"
            hint = q['description'] if q['claimed'] or q['completed'] else quest_progress_hint(q['goal_type'], remaining)
            lines.append(f"{mark} {q['title']} — {status}\n{hint}\n🎁 {q['reward_coins']} سکه + {q['reward_xp']} XP")
        await call.message.edit_text("\n\n".join(lines), reply_markup=quests_keyboard(quests))
        if quests and all(q['claimed'] for q in quests):
            await call.message.answer("🔥 امروز فعال بودی\nکل کوئست‌های امروز تموم شد و جایزه هارو گرفتی\nفردا یه سری کوئست جدید منتظر")
    except Exception:
        logger.exception("Quest claim failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data == "check_force_join")
async def check_force_join_callback(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        enabled = await db.get_int("force_join_enabled", 0)
        channel = await db.get_setting("force_join_channel", "")
        if not enabled or not channel:
            await call.message.answer("جوین اجباری فعال نیست. /start را بزن.")
            return
        try:
            member = await bot.get_chat_member(channel, call.from_user.id)
            if member.status not in {"left", "kicked", "banned"}:
                await call.message.answer("✅ عضویت تایید شد. حالا /start را بزن.")
            else:
                await call.message.answer("هنوز عضو کانال نیستی.")
        except Exception:
            logger.exception("Force join callback check failed")
            await call.message.answer("فعلاً امکان بررسی نبود. /start را دوباره بزن.")
    except Exception:
        logger.exception("Check force join callback failed")


@router.message(F.text == "📞 تماس")
async def contact_entry(message: Message, db: Database) -> None:
    try:
        contact = await db.get_setting("contact_admin_id", "@ChalleshinoSupport")
        await message.answer(f"📞 ارتباط با پشتیبانی:\n{contact}")
    except Exception:
        logger.exception("Contact failed")
        await message.answer("خطا در نمایش اطلاعات تماس.")


@router.message(F.text == "📘 راهنما")
async def help_menu_entry(message: Message, db: Database) -> None:
    try:
        await message.answer(await db.render_help_text())
    except Exception:
        logger.exception("Help menu failed")
        await message.answer("خطا در نمایش راهنما.")
