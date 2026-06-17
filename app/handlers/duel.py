from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext

from app.db import Database, now_iso
from app.keyboards import duel_menu, genres_keyboard, question_keyboard, main_menu, waiting_queue_keyboard
from app.utils import invite_token, options_from_question
from app.states import ReportQuestion
from app.notifications import send_duel_transition_notifications, send_streak_notification

logger = logging.getLogger(__name__)
router = Router()


@dataclass
class DuelRuntime:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    question_started_at: float = 0.0
    timeout_task: asyncio.Task | None = None


runtimes: dict[int, DuelRuntime] = {}
user_genre_temp: dict[tuple[int, int], set[str]] = {}
user_offer_temp: dict[tuple[int, int], list[str]] = {}
hidden_options_temp: dict[tuple[int, int, int], set[int]] = {}
queue_timeout_tasks: dict[int, asyncio.Task] = {}


def runtime(duel_id: int) -> DuelRuntime:
    runtimes.setdefault(duel_id, DuelRuntime())
    return runtimes[duel_id]


@router.message(F.text == "⚔️ دوئل")
async def duel_entry(message: Message, db: Database) -> None:
    u = await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if u['is_blocked']:
        await message.answer("حساب شما مسدود است.")
        return
    random_cost = await db.get_int('random_duel_cost', 5)
    friendly_cost = await db.get_int('friendly_duel_cost', 20)
    await message.answer("دوئل:", reply_markup=ReplyKeyboardRemove())
    await message.answer("نوع دوئل را انتخاب کن:", reply_markup=duel_menu(random_cost, friendly_cost))


@router.callback_query(F.data == "duel:random")
async def random_duel(call: CallbackQuery, db: Database, bot: Bot) -> None:
    try:
        active = await db.active_duel_for_user(call.from_user.id)
        if active:
            await call.answer("شما یک دوئل فعال دارید.", show_alert=True)
            return
        cost = await db.get_int('random_duel_cost', 5)
        user = await db.get_user(call.from_user.id)
        if not user or user['coins'] < cost:
            await call.answer(f"برای ورود به صف دوئل شانسی به {cost} سکه نیاز داری.", show_alert=True)
            return
        waiting = await db.find_waiting_duel(call.from_user.id)
        await db.change_coins(call.from_user.id, -cost, 'random_duel_entry')
        if waiting:
            task = queue_timeout_tasks.pop(waiting['id'], None)
            if task and not task.done():
                task.cancel()
            await db.join_duel(waiting['id'], call.from_user.id)
            await call.message.answer("حریف پیدا شد! انتخاب ژانر شروع شد.")
            await bot.send_message(waiting['player1_id'], "حریف پیدا شد! انتخاب ژانر شروع شد.")
            await offer_genres(waiting['id'], db, bot)
        else:
            duel_id = await db.create_waiting_duel(call.from_user.id)
            timeout = await db.get_int('matchmaking_timeout_seconds', 120)
            queue_timeout_tasks[duel_id] = asyncio.create_task(random_queue_timeout(duel_id, call.from_user.id, cost, timeout, db, bot))
            await call.message.answer(f"در صف انتظار قرار گرفتی. حداکثر {timeout} ثانیه منتظر حریف می‌مانی. اگر حریفی پیدا نشود، صف لغو و {cost} سکه برگردانده می‌شود.", reply_markup=waiting_queue_keyboard(duel_id))
        await call.answer()
    except Exception:
        logger.exception("Random duel failed")
        await call.answer("خطا", show_alert=True)


async def random_queue_timeout(duel_id: int, user_id: int, cost: int, seconds: int, db: Database, bot: Bot) -> None:
    try:
        await asyncio.sleep(seconds)
        duel = await db.get_duel(duel_id)
        if duel and duel['status'] == 'waiting' and duel['player1_id'] == user_id:
            await db.execute_write("UPDATE duels SET status='cancelled', finished_at=? WHERE id=?", (now_iso(), duel_id))
            await db.change_coins(user_id, cost, 'random_duel_timeout_refund')
            await bot.send_message(user_id, f"⏱ حریفی پیدا نشد؛ صف دوئل شانسی لغو شد و {cost} سکه به حساب شما برگشت.", reply_markup=main_menu(await db.is_admin(user_id)))
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Random queue timeout failed")


@router.callback_query(F.data.startswith("duel:cancel_queue:"))
async def cancel_random_queue(call: CallbackQuery, db: Database) -> None:
    try:
        duel_id = int(call.data.split(":")[2])
        duel = await db.get_duel(duel_id)
        if not duel or duel['status'] != 'waiting' or duel['player1_id'] != call.from_user.id:
            await call.answer("صف فعالی برای لغو پیدا نشد.", show_alert=True)
            return
        task = queue_timeout_tasks.pop(duel_id, None)
        if task and not task.done():
            task.cancel()
        cost = await db.get_int('random_duel_cost', 5)
        await db.execute_write("UPDATE duels SET status='cancelled', finished_at=? WHERE id=?", (now_iso(), duel_id))
        await db.change_coins(call.from_user.id, cost, 'random_duel_queue_cancel_refund', duel_id)
        await call.message.answer(f"از صف خارج شدی و {cost} سکه به حسابت برگشت.", reply_markup=main_menu(await db.is_admin(call.from_user.id)))
        await call.answer()
    except Exception:
        logger.exception("Cancel random queue failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data == "duel:invite")
async def invite_duel(call: CallbackQuery, db: Database, bot_username: str) -> None:
    try:
        active = await db.active_duel_for_user(call.from_user.id)
        if active:
            await call.answer("شما یک دوئل فعال دارید.", show_alert=True)
            return
        cost = await db.get_int('friendly_duel_cost', 20)
        user = await db.get_user(call.from_user.id)
        if not user or user['coins'] < cost:
            await call.answer(f"برای ساخت دوئل دوستانه به {cost} سکه نیاز داری.", show_alert=True)
            return
        await db.change_coins(call.from_user.id, -cost, 'friendly_duel_create')
        token = invite_token()
        await db.create_invite_duel(call.from_user.id, token)
        link = f"https://t.me/{bot_username}?start=invite_{token}"
        await call.message.answer(f"{cost} سکه از سازنده کسر شد. این لینک را برای دوستت بفرست:\n{link}")
        await call.answer()
    except Exception:
        logger.exception("Invite duel failed")
        await call.answer("خطا", show_alert=True)


async def join_invite_from_start(message: Message, db: Database, token: str) -> None:
    duel = await db.get_invite_duel(token)
    if not duel:
        await message.answer("این دعوت‌نامه معتبر نیست یا قبلاً استفاده شده است.")
        return
    if duel['player1_id'] == message.from_user.id:
        await message.answer("نمی‌توانی با خودت دوئل کنی.")
        return
    await db.join_duel(duel['id'], message.from_user.id)
    await message.answer("وارد دوئل دعوتی شدی. انتخاب ژانر شروع شد.")
    from aiogram import Bot as BotType
    bot: BotType = message.bot
    await bot.send_message(duel['player1_id'], "دوستت وارد دوئل شد. انتخاب ژانر شروع شد.")
    await offer_genres(duel['id'], db, bot)


async def offer_genres(duel_id: int, db: Database, bot: Bot) -> None:
    duel = await db.get_duel(duel_id)
    if not duel or not duel['player2_id']:
        return
    all_genres = await db.available_genres()
    already = set(g for g in (duel['offered_genres'] or '').split('|') if g)
    candidates = [g for g in all_genres if g not in already]
    offer_n = await db.get_int('genres_to_offer', 4)
    choose_n = await db.get_int('genres_to_choose', 2)
    if len(candidates) < offer_n:
        await bot.send_message(duel['player1_id'], "ژانر/سوال فعال کافی برای شروع دوئل وجود ندارد؛ هزینه پرداخت‌شده برگردانده شد.")
        await bot.send_message(duel['player2_id'], "ژانر/سوال فعال کافی برای شروع دوئل وجود ندارد؛ هزینه پرداخت‌شده برگردانده شد.")
        if duel['invite_token']:
            await db.change_coins(duel['player1_id'], await db.get_int('friendly_duel_cost', 20), 'duel_cancel_refund', duel_id)
        else:
            cost = await db.get_int('random_duel_cost', 5)
            await db.change_coins(duel['player1_id'], cost, 'duel_cancel_refund', duel_id)
            await db.change_coins(duel['player2_id'], cost, 'duel_cancel_refund', duel_id)
        await db.execute_write("UPDATE duels SET status='cancelled' WHERE id=?", (duel_id,))
        return
    if len(candidates) >= offer_n * 2:
        pool = random.sample(candidates, offer_n * 2)
        offer1 = pool[:offer_n]
        offer2 = pool[offer_n:]
    else:
        # اگر ژانر فعال کافی برای دو لیست کاملاً جدا نیست، فقط در حد اجبار هم‌پوشانی می‌دهیم.
        offer1 = random.sample(candidates, offer_n)
        remaining = [g for g in candidates if g not in offer1]
        needed = offer_n - len(remaining)
        offer2 = remaining + random.sample(offer1, needed)
        random.shuffle(offer2)
    offers = {
        duel['player1_id']: offer1,
        duel['player2_id']: offer2,
    }
    await db.set_offered_genres(duel_id, list(dict.fromkeys(offers[duel['player1_id']] + offers[duel['player2_id']])))
    for uid in [duel['player1_id'], duel['player2_id']]:
        user_genre_temp[(duel_id, uid)] = set()
        user_offer_temp[(duel_id, uid)] = offers[uid]
        await bot.send_message(uid, f"از ژانرهای زیر دقیقاً {choose_n} مورد را انتخاب کن:", reply_markup=genres_keyboard(duel_id, offers[uid], set(), choose_n))


@router.callback_query(F.data.startswith("genre:"))
async def genre_toggle(call: CallbackQuery, db: Database) -> None:
    try:
        _, duel_id_s, genre = call.data.split(":", 2)
        duel_id = int(duel_id_s)
        duel = await db.get_duel(duel_id)
        if not duel or call.from_user.id not in [duel['player1_id'], duel['player2_id']]:
            await call.answer("این دوئل متعلق به شما نیست.", show_alert=True)
            return
        choose_n = await db.get_int('genres_to_choose', 2)
        key = (duel_id, call.from_user.id)
        selected = user_genre_temp.setdefault(key, set())
        if genre in selected:
            selected.remove(genre)
        elif len(selected) < choose_n:
            selected.add(genre)
        else:
            await call.answer(f"حداکثر {choose_n} ژانر انتخاب می‌شود.", show_alert=True)
            return
        offered = user_offer_temp.get((duel_id, call.from_user.id), [])
        if not offered:
            offered = (duel['offered_genres'] or '').split('|')[-await db.get_int('genres_to_offer', 4):]
        await call.message.edit_reply_markup(reply_markup=genres_keyboard(duel_id, offered, selected, choose_n))
        await call.answer()
    except Exception:
        logger.exception("Genre toggle failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("genre_done:"))
async def genre_done(call: CallbackQuery, db: Database, bot: Bot) -> None:
    try:
        duel_id = int(call.data.split(":")[1])
        choose_n = await db.get_int('genres_to_choose', 2)
        selected = user_genre_temp.get((duel_id, call.from_user.id), set())
        if len(selected) != choose_n:
            await call.answer(f"باید دقیقاً {choose_n} ژانر انتخاب کنی.", show_alert=True)
            return
        await db.save_genre_choices(duel_id, call.from_user.id, list(selected))
        await call.message.edit_text("انتخاب شما ثبت شد. منتظر حریف بمانید...")
        duel = await db.get_duel(duel_id)
        choices = await db.duel_choices(duel_id)
        if duel and duel['player1_id'] in choices and duel['player2_id'] in choices:
            selected_genres = list(dict.fromkeys(list(choices[duel['player1_id']]) + list(choices[duel['player2_id']])))
            count = await db.get_int('duel_question_count', 7)
            qs = await db.start_duel_questions(duel_id, selected_genres, count)
            if not qs:
                await bot.send_message(duel['player1_id'], "در ژانرهای انتخاب‌شده سوال فعالی پیدا نشد؛ هزینه پرداخت‌شده برگردانده شد.")
                await bot.send_message(duel['player2_id'], "در ژانرهای انتخاب‌شده سوال فعالی پیدا نشد؛ هزینه پرداخت‌شده برگردانده شد.")
                if duel['invite_token']:
                    await db.change_coins(duel['player1_id'], await db.get_int('friendly_duel_cost', 20), 'duel_cancel_refund', duel_id)
                else:
                    cost = await db.get_int('random_duel_cost', 5)
                    await db.change_coins(duel['player1_id'], cost, 'duel_cancel_refund', duel_id)
                    await db.change_coins(duel['player2_id'], cost, 'duel_cancel_refund', duel_id)
                await db.execute_write("UPDATE duels SET status='cancelled' WHERE id=?", (duel_id,))
            else:
                await bot.send_message(duel['player1_id'], f"دوئل شروع شد! ژانرهای بازی: {', '.join(selected_genres)}")
                await bot.send_message(duel['player2_id'], f"دوئل شروع شد! ژانرهای بازی: {', '.join(selected_genres)}")
                await send_current_question(duel_id, db, bot)
        await call.answer()
    except Exception:
        logger.exception("Genre done failed")
        await call.answer("خطا", show_alert=True)


async def send_current_question(duel_id: int, db: Database, bot: Bot) -> None:
    rt = runtime(duel_id)
    async with rt.lock:
        duel = await db.get_duel(duel_id)
        if not duel or duel['status'] != 'playing':
            return
        seq = duel['current_index']
        q = await db.duel_question_by_seq(duel_id, seq)
        if not q:
            await finish_and_notify(duel_id, db, bot)
            return
        timer = await db.get_int('question_timer_seconds', 15)
        rt.question_started_at = time.monotonic()
        text = f"سوال {seq + 1}\nID: <code>{q['id']}</code>\n\n{q['text']}\n⏱ {timer} ثانیه"
        for uid in [duel['player1_id'], duel['player2_id']]:
            costs = await db.powerup_costs_for_user(duel_id, uid)
            await bot.send_message(uid, text, reply_markup=question_keyboard(duel_id, q['id'], options_from_question(q), cost_5050=costs['5050'], cost_hint=costs['hint']))
        if rt.timeout_task and not rt.timeout_task.done():
            rt.timeout_task.cancel()
        rt.timeout_task = asyncio.create_task(timeout_question(duel_id, q['id'], timer, db, bot))


async def timeout_question(duel_id: int, qid: int, seconds: int, db: Database, bot: Bot) -> None:
    try:
        await asyncio.sleep(seconds)
        duel = await db.get_duel(duel_id)
        q = await db.fetchone("SELECT * FROM questions WHERE id=?", (qid,))
        if not duel or not q or duel['status'] != 'playing':
            return
        for uid in [duel['player1_id'], duel['player2_id']]:
            await db.record_answer(duel_id, qid, uid, None, q['correct_option'], None)
        await bot.send_message(duel['player1_id'], "زمان سوال تمام شد.")
        await bot.send_message(duel['player2_id'], "زمان سوال تمام شد.")
        await advance_duel(duel_id, db, bot)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Question timeout failed")


@router.callback_query(F.data.startswith("ans:"))
async def answer_callback(call: CallbackQuery, db: Database, bot: Bot) -> None:
    try:
        _, duel_s, qid_s, opt_s = call.data.split(":")
        duel_id, qid, opt = int(duel_s), int(qid_s), int(opt_s)
        duel = await db.get_duel(duel_id)
        q = await db.fetchone("SELECT * FROM questions WHERE id=?", (qid,))
        if not duel or not q or call.from_user.id not in [duel['player1_id'], duel['player2_id']]:
            await call.answer("نامعتبر", show_alert=True)
            return
        rt = runtime(duel_id)
        ms = int((time.monotonic() - rt.question_started_at) * 1000)
        inserted = await db.record_answer(duel_id, qid, call.from_user.id, opt, q['correct_option'], ms)
        if not inserted:
            await call.answer("قبلاً پاسخ داده‌ای.", show_alert=True)
            return
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("✅ درست" if opt == q['correct_option'] else "❌ نادرست", show_alert=True)
        if await db.answered_count_for_question(duel_id, qid) >= 2:
            rt.timeout_task.cancel() if rt.timeout_task and not rt.timeout_task.done() else None
            await advance_duel(duel_id, db, bot)
    except Exception:
        logger.exception("Answer failed")
        await call.answer("خطا", show_alert=True)


async def advance_duel(duel_id: int, db: Database, bot: Bot) -> None:
    duel = await db.get_duel(duel_id)
    if not duel or duel['status'] != 'playing':
        return
    await db.execute_write("UPDATE duels SET current_index=current_index+1 WHERE id=?", (duel_id,))
    count = await db.duel_questions_count(duel_id)
    updated = await db.get_duel(duel_id)
    if updated and updated['current_index'] >= count:
        await finish_and_notify(duel_id, db, bot)
    else:
        await send_current_question(duel_id, db, bot)


async def finish_and_notify(duel_id: int, db: Database, bot: Bot) -> None:
    result = await db.finish_duel(duel_id)
    duel = await db.get_duel(duel_id)
    if not duel or not result:
        return
    stats = result['stats']
    winner = result['winner']
    for uid in [duel['player1_id'], duel['player2_id']]:
        if winner is None:
            line = "نتیجه: مساوی"
        elif winner == uid:
            line = "🎉 شما برنده شدید!"
        else:
            line = "شما بازنده شدید."
        rewards = result.get('transitions', {}).get(uid, {}).get('rewards', {})
        reward_text = (
            f"\n\nدریافتی این بازی:\n"
            f"🪙 سکه: {rewards.get('coins', 0):+}\n"
            f"⭐ XP: {rewards.get('xp', 0):+}\n"
            f"🏆 جام: {rewards.get('cups', 0):+}"
        )
        await bot.send_message(uid, f"🏁 دوئل تمام شد.\n{line}\n\nامتیاز شما: {stats[uid]['correct']} پاسخ صحیح\nامتیاز حریف: {stats[duel['player1_id' if uid==duel['player2_id'] else 'player2_id']]['correct']} پاسخ صحیح{reward_text}", reply_markup=main_menu(await db.is_admin(uid)))
    for uid in [duel['player1_id'], duel['player2_id']]:
        await send_duel_transition_notifications(bot, db, uid, result.get('transitions', {}).get(uid, {}))
        reward = await db.claim_streak_reward(uid)
        await send_streak_notification(bot, uid, reward)
    for uid in [duel['player1_id'], duel['player2_id']]:
        user_offer_temp.pop((duel_id, uid), None)
        user_genre_temp.pop((duel_id, uid), None)
    for key in list(hidden_options_temp.keys()):
        if key[0] == duel_id:
            hidden_options_temp.pop(key, None)
    runtimes.pop(duel_id, None)


@router.callback_query(F.data.startswith("power:"))
async def powerup_callback(call: CallbackQuery, db: Database) -> None:
    try:
        _, ptype, duel_s, qid_s = call.data.split(":")
        duel_id, qid = int(duel_s), int(qid_s)
        costs = await db.powerup_costs_for_user(duel_id, call.from_user.id)
        if costs['uses'] >= costs['max']:
            await call.answer(f"در هر دست بیشتر از {costs['max']} بار نمی‌توانی از پاورآپ استفاده کنی.", show_alert=True); return
        cost = costs['5050'] if ptype == '5050' else costs['hint']
        user = await db.get_user(call.from_user.id)
        q = await db.fetchone("SELECT * FROM questions WHERE id=?", (qid,))
        if not user or not q:
            await call.answer("نامعتبر", show_alert=True); return
        if user['coins'] < cost:
            await call.answer(f"سکه کافی نداری. هزینه فعلی: {cost} سکه", show_alert=True); return
        if await db.has_powerup(duel_id, qid, call.from_user.id, ptype):
            await call.answer("این پاورآپ را برای این سوال استفاده کرده‌ای.", show_alert=True); return
        ok = await db.mark_powerup(duel_id, qid, call.from_user.id, ptype)
        if not ok:
            await call.answer("امکان استفاده نیست.", show_alert=True); return
        await db.change_coins(call.from_user.id, -cost, f"powerup_{ptype}", duel_id)
        wrong = [i for i in range(1,5) if i != q['correct_option']]
        new_costs = await db.powerup_costs_for_user(duel_id, call.from_user.id)
        hidden_key = (duel_id, call.from_user.id, qid)
        if ptype == '5050':
            hidden = set(random.sample(wrong, 2))
            hidden_options_temp[hidden_key] = hidden
            await call.message.edit_reply_markup(reply_markup=question_keyboard(duel_id, qid, options_from_question(q), hidden, cost_5050=new_costs['5050'], cost_hint=new_costs['hint']))
            await call.answer(f"دو گزینه حذف شد. هزینه: {cost} سکه", show_alert=True)
        else:
            candidate = random.choice(wrong)
            hidden = hidden_options_temp.get(hidden_key, set())
            await call.message.edit_reply_markup(reply_markup=question_keyboard(duel_id, qid, options_from_question(q), hidden, cost_5050=new_costs['5050'], cost_hint=new_costs['hint']))
            await call.answer(f"راهنمایی: پاسخ بین گزینه‌های {q['correct_option']} و {candidate} است. هزینه: {cost} سکه", show_alert=True)
    except Exception:
        logger.exception("Powerup failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("report:"))
async def report_question(call: CallbackQuery, state: FSMContext) -> None:
    try:
        _, duel_s, qid_s = call.data.split(":")
        await state.set_state(ReportQuestion.reason)
        await state.update_data(report_duel_id=int(duel_s), report_qid=int(qid_s))
        await call.message.answer("دلیل گزارش را بنویس یا /skip بزن تا بدون دلیل ثبت شود.")
        await call.answer()
    except Exception:
        logger.exception("Report start failed")
        await call.answer("خطا", show_alert=True)


@router.message(ReportQuestion.reason, F.text)
async def report_reason(message: Message, state: FSMContext, db: Database, bot: Bot, reports_channel_id: int | None) -> None:
    try:
        data = await state.get_data()
        reason = None if message.text == '/skip' else message.text
        report_id = await db.add_report(data['report_qid'], message.from_user.id, data['report_duel_id'], reason)
        if reports_channel_id:
            await bot.send_message(reports_channel_id, f"🚩 گزارش سوال #{report_id}\nQuestion ID: <code>{data['report_qid']}</code>\nDuel ID: {data['report_duel_id']}\nReporter: <code>{message.from_user.id}</code>\nReason: {reason or 'بدون دلیل'}")
        await state.clear()
        await message.answer("گزارش ثبت شد. ممنون.")
    except Exception:
        logger.exception("Report save failed")
        await message.answer("خطا در ثبت گزارش.")
