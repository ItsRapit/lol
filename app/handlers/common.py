import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from app.db import Database
from app.keyboards import main_menu, leaderboard_basis_keyboard, leaderboard_period_keyboard, CANCEL_TEXT, back_home_keyboard
from app.utils import ensure_user, xp_progress_text, rtl_line, to_english_digits, league_with_emoji, rank_with_emoji
from app.notifications import send_streak_notification
from app.time_utils import jalali_date, jalali_datetime

logger = logging.getLogger(__name__)
router = Router()


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
        if payload and payload.startswith('invite_'):
            if was_new and signup_gift > 0:
                await message.answer(f"🎁 {signup_gift}تا سکه برای شروع در اختیار شما قرار گرفت.")
            await send_streak_notification(bot, message.from_user.id, streak_reward)
            from app.handlers.duel import join_invite_from_start
            await join_invite_from_start(message, db, payload.removeprefix('invite_'))
            return
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
    is_admin = await db.is_admin(message.from_user.id)
    await message.answer("عملیات لغو شد. به منوی اصلی برگشتی.", reply_markup=main_menu(is_admin))


@router.callback_query(F.data == "nav:home")
async def nav_home(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    is_admin = await db.is_admin(call.from_user.id)
    await call.message.answer("به منوی اصلی برگشتی.", reply_markup=main_menu(is_admin))
    await call.answer()


@router.message(F.text == "👤 پروفایل")
async def profile(message: Message, db: Database) -> None:
    try:
        u = await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        title = await db.user_title(message.from_user.id)
        if title:
            title_text = f"{title['emoji'] or ''} {title['name']}".strip()
        else:
            title_text = rank_with_emoji(await db.get_rank_title(u['level']))
        league = await db.get_user_league(u['cups'])
        league_name = league_with_emoji(league['name'] if league else 'بدون لیگ')
        cur, nxt = await db.level_bounds(u['level'])
        username = f"@{u['username']}" if u['username'] else ""
        current_xp = max(0, int(u['xp']) - int(cur))
        required_xp = max(1, int(nxt) - int(cur))
        filled = max(0, min(10, int((current_xp / required_xp) * 10)))
        xp_bar_blocks = "▰" * filled + "▱" * (10 - filled)
        analysis = await db.user_strengths_weaknesses(message.from_user.id)
        genre_analysis = ""
        if analysis['strengths']:
            strengths = "\n".join(f"🥇 {r['genre']} — {int(r['pct'])}%" if i == 0 else f"🥈 {r['genre']} — {int(r['pct'])}%" for i, r in enumerate(analysis['strengths']))
            weaknesses = "\n".join(f"📉 {r['genre']} — {int(r['pct'])}%" for r in analysis['weaknesses'])
            genre_analysis = f"\n\n💪 نقاط قوت:\n{strengths}"
            if weaknesses:
                genre_analysis += f"\n\n⚠️ نقاط ضعف:\n{weaknesses}"
        total_duels = int(u['wins']) + int(u['losses']) + int(u['draws'])
        wrong = max(0, int(u['total_answers']) - int(u['correct_answers']))
        await message.answer(
            f"👤 <b>{u['first_name'] or 'کاربر'}</b> {username}\n"
            f"{title_text} | لول {u['level']}\n"
            f"ایکس‌پی {current_xp}/{required_xp} {xp_bar_blocks}\n"
            f"🏆 {league_name} — {u['cups']} جام\n"
            f"🪙 سکه: {u['coins']}\n\n"
            f"⚔️ دوئل‌ها: {total_duels} | برد {u['wins']} / مساوی {u['draws']} / شکست {u['losses']}\n"
            f"✅ پاسخ صحیح: {u['correct_answers']} | ❌ پاسخ غلط: {wrong}"
            f"{genre_analysis}",
            reply_markup=back_home_keyboard(),
        )
    except Exception:
        logger.exception("Profile failed")
        await message.answer("خطا در نمایش پروفایل.")


@router.message(F.text == "🏆 لیدربورد")
async def leaderboard_entry(message: Message) -> None:
    await message.answer("لیدربورد را بر چه اساسی ببینی؟", reply_markup=ReplyKeyboardRemove())
    await message.answer("انتخاب کن:", reply_markup=leaderboard_basis_keyboard())


@router.callback_query(F.data.startswith("lb_basis:"))
async def leaderboard_basis(call: CallbackQuery) -> None:
    basis = call.data.split(":", 1)[1]
    await call.message.edit_text("بازه زمانی را انتخاب کن:", reply_markup=leaderboard_period_keyboard(basis))
    await call.answer()


@router.callback_query(F.data == "lb_back:basis")
async def leaderboard_back(call: CallbackQuery) -> None:
    await call.message.edit_text("لیدربورد را بر چه اساسی ببینی؟", reply_markup=leaderboard_basis_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("lb:"))
async def leaderboard_callback(call: CallbackQuery, db: Database) -> None:
    try:
        _, basis, period = call.data.split(":")
        basis_title = "سطح" if basis == "level" else "لیگ"
        period_title = {"daily": "روزانه", "monthly": "ماهانه", "all": "کلی"}.get(period, "کلی")
        rows = await db.leaderboard(basis, period)
        text = rtl_line(f"🏆 لیدربورد {basis_title} — {period_title}") + "\n\n"
        if not rows:
            text += rtl_line("هنوز امتیازی ثبت نشده است.")
        for i, r in enumerate(rows, 1):
            raw_name = r['first_name'] or (('@' + r['username']) if r['username'] else str(r['telegram_id']))
            name = raw_name if len(raw_name) <= 17 else raw_name[:17] + "..."
            safe_name = f"\u2068{name}\u2069"
            league_name = league_with_emoji(r['league_name'])
            if basis == "league":
                line = f"{i}. {safe_name} | {league_name} | جام: {r['cups']} | سطح: {r['level']}"
            else:
                line = f"{i}. {safe_name} | {league_name} | جام: {r['cups']} | سطح: {r['level']} | امتیاز: {r['score']}"
            text += rtl_line(line) + "\n"
        await call.message.edit_text(text, reply_markup=leaderboard_period_keyboard(basis))
        await call.answer()
    except Exception:
        logger.exception("Leaderboard failed")
        await call.answer("خطا", show_alert=True)


@router.message(F.text == "🎁 رفرال")
async def referral(message: Message, db: Database, bot_username: str) -> None:
    await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    link = f"https://t.me/{bot_username}?start=ref_{message.from_user.id}"
    rc = await db.get_int("referral_referrer_coins", 50)
    rx = await db.get_int("referral_referrer_xp", 50)
    nc = await db.get_int("referral_referred_coins", 25)
    nx = await db.get_int("referral_referred_xp", 25)
    await message.answer(
        "🎁 لینک دعوت اختصاصی شما:\n"
        f"{link}\n\n"
        f"اگر دوستت با لینک تو وارد بشه و اولین دوئلش رو بازی کنه، تو <b>{rc} سکه و {rx} XP</b> می‌گیری، اون هم <b>{nc} سکه و {nx} XP</b> هدیه می‌گیره.",
        reply_markup=back_home_keyboard(),
    )


@router.message(F.text == "🏰 کلن (به‌زودی)")
async def clan_placeholder(message: Message) -> None:
    await message.answer("🏰 قابلیت کلن به‌زودی اضافه می‌شود.", reply_markup=ReplyKeyboardRemove())
    await message.answer("برای برگشت به منوی اصلی بزن:", reply_markup=back_home_keyboard())


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
