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
from app.keyboards import duel_menu, genres_keyboard, question_keyboard, main_menu, waiting_queue_keyboard, issue_report_reasons_keyboard, report_admin_keyboard, result_report_keyboard, duel_finished_keyboard, rematch_keyboard
from app.utils import invite_token, options_from_question
from app.states import ReportQuestion
from app.notifications import send_duel_transition_notifications, send_streak_notification
from app.time_utils import jalali_datetime

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
second_chance_pending: set[tuple[int, int, int]] = set()
second_chance_question: dict[tuple[int, int], int] = {}
question_message_ids: dict[tuple[int, int, int], int] = {}
duel_main_message_ids: dict[tuple[int, int], int] = {}
rematch_timeout_tasks: dict[tuple[int, int], asyncio.Task] = {}
queue_timeout_tasks: dict[int, asyncio.Task] = {}
genre_timeout_tasks: dict[tuple[int, int], asyncio.Task] = {}
unanswered_streaks: dict[tuple[int, int], int] = {}


async def safe_edit_reply_markup(message, reply_markup) -> None:
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except Exception:
        logger.exception("Safe edit reply markup failed")


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
    await message.answer("⚔️ دوئل\nیکی را انتخاب کن:", reply_markup=duel_menu(random_cost, friendly_cost))


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
            await call.message.answer("منوی پایین بسته شد.", reply_markup=ReplyKeyboardRemove())
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
    timeout_seconds = await db.get_int('genre_selection_timeout_seconds', 60)
    for uid in [duel['player1_id'], duel['player2_id']]:
        user_genre_temp[(duel_id, uid)] = set()
        user_offer_temp[(duel_id, uid)] = offers[uid]
        await bot.send_message(uid, f"از ژانرهای زیر دقیقاً {choose_n} مورد را انتخاب کن:", reply_markup=genres_keyboard(duel_id, offers[uid], set(), choose_n))
        old_task = genre_timeout_tasks.pop((duel_id, uid), None)
        if old_task and not old_task.done():
            old_task.cancel()
        genre_timeout_tasks[(duel_id, uid)] = asyncio.create_task(genre_selection_timeout(duel_id, uid, timeout_seconds, db, bot))


async def genre_selection_timeout(duel_id: int, user_id: int, seconds: int, db: Database, bot: Bot) -> None:
    try:
        await asyncio.sleep(seconds)
        duel = await db.get_duel(duel_id)
        if not duel or duel['status'] != 'genre_selection':
            return
        choices = await db.duel_choices(duel_id)
        if user_id in choices:
            return
        other_id = duel['player2_id'] if user_id == duel['player1_id'] else duel['player1_id']
        await db.execute_write("UPDATE duels SET status='cancelled', finished_at=? WHERE id=?", (now_iso(), duel_id))
        if duel['invite_token']:
            # Creator already paid. If invitee times out, creator is refunded; if creator times out, no refund.
            if user_id != duel['player1_id']:
                await db.change_coins(duel['player1_id'], await db.get_int('friendly_duel_cost', 20), 'genre_timeout_other_refund', duel_id)
        else:
            cost = await db.get_int('random_duel_cost', 5)
            if other_id:
                await db.change_coins(other_id, cost, 'genre_timeout_other_refund', duel_id)
        for uid in [duel['player1_id'], duel['player2_id']]:
            if not uid:
                continue
            if uid == user_id:
                await bot.send_message(uid, "⏱ زمان انتخاب ژانر تمام شد؛ چون انتخاب نکردی دوئل بسته شد و هزینه ورودت برنگشت.", reply_markup=main_menu(await db.is_admin(uid)))
            else:
                await bot.send_message(uid, "⏱ حریف ژانر را انتخاب نکرد؛ دوئل بسته شد و هزینه ورودت برگشت.", reply_markup=main_menu(await db.is_admin(uid)))
        for key, task in list(genre_timeout_tasks.items()):
            if key[0] == duel_id and not task.done():
                task.cancel()
            if key[0] == duel_id:
                genre_timeout_tasks.pop(key, None)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Genre selection timeout failed")


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
        my_gtask = genre_timeout_tasks.pop((duel_id, call.from_user.id), None)
        if my_gtask and not my_gtask.done():
            my_gtask.cancel()
        await call.message.edit_text("انتخاب شما ثبت شد. منتظر حریف بمانید...")
        duel = await db.get_duel(duel_id)
        choices = await db.duel_choices(duel_id)
        if duel and duel['player1_id'] in choices and duel['player2_id'] in choices:
            for key, task in list(genre_timeout_tasks.items()):
                if key[0] == duel_id:
                    if not task.done():
                        task.cancel()
                    genre_timeout_tasks.pop(key, None)
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
        text = f"سوال {seq + 1}\nID: <code>{q['id']}</code>\n\n{q['text']}"
        for uid in [duel['player1_id'], duel['player2_id']]:
            costs = await db.powerup_costs_for_user(duel_id, uid)
            markup = question_keyboard(duel_id, q['id'], options_from_question(q), cost_auto=costs['auto'])
            old_message_id = duel_main_message_ids.get((duel_id, uid))
            if old_message_id:
                try:
                    await bot.edit_message_text(text, chat_id=uid, message_id=old_message_id, reply_markup=markup)
                    question_message_ids[(duel_id, uid, q['id'])] = old_message_id
                    continue
                except Exception:
                    logger.debug("Could not edit duel question message; sending new", exc_info=True)
            msg = await bot.send_message(uid, text, reply_markup=markup)
            duel_main_message_ids[(duel_id, uid)] = msg.message_id
            question_message_ids[(duel_id, uid, q['id'])] = msg.message_id
        if rt.timeout_task and not rt.timeout_task.done():
            rt.timeout_task.cancel()
        rt.timeout_task = asyncio.create_task(timeout_question(duel_id, q['id'], timer, db, bot))


async def forfeit_inactive_duel(duel_id: int, inactive_users: list[int], db: Database, bot: Bot) -> None:
    try:
        duel = await db.get_duel(duel_id)
        if not duel or duel['status'] != 'playing':
            return
        penalty = await db.get_int('inactive_forfeit_penalty_coins', 10)
        players = [duel['player1_id'], duel['player2_id']]
        inactive = [u for u in inactive_users if u in players]
        active = [u for u in players if u not in inactive]
        winner = active[0] if len(active) == 1 else None
        await db.execute_write("UPDATE duels SET status='cancelled', finished_at=?, winner_id=? WHERE id=?", (now_iso(), winner, duel_id))
        for uid in inactive:
            await db.change_coins(uid, -penalty, 'inactive_forfeit_penalty', duel_id)
        if winner:
            await db.change_coins(winner, penalty, 'inactive_forfeit_reward', duel_id)
        for uid in inactive:
            await bot.send_message(uid, f"⚠️ به دلیل پاسخ ندادن به 3 سوال پشت‌سرهم، دوئل بسته شد و {penalty} سکه جریمه شدی.", reply_markup=main_menu(await db.is_admin(uid)))
        for uid in active:
            await bot.send_message(uid, f"✅ حریف 3 سوال پشت‌سرهم جواب نداد؛ دوئل بسته شد و {penalty} سکه جریمه حریف به حسابت اضافه شد.", reply_markup=main_menu(await db.is_admin(uid)))
        channel = await db.get_setting('reports_channel_id', '')
        if channel:
            try:
                await bot.send_message(
                    int(channel),
                    f"⚠️ ثبت غیرفعالی در دوئل #{duel_id}\n"
                    f"Inactive: {', '.join(map(str, inactive))}\n"
                    f"Winner: {winner or 'ندارد'}\n"
                    f"Penalty: {penalty} سکه\n"
                    f"📅 {jalali_datetime(now_iso())}",
                )
            except Exception:
                logger.exception("Could not send inactivity log")
        for key in list(unanswered_streaks.keys()):
            if key[0] == duel_id:
                unanswered_streaks.pop(key, None)
        rt = runtimes.pop(duel_id, None)
        if rt:
            if rt.timeout_task and not rt.timeout_task.done():
                rt.timeout_task.cancel()
    except Exception:
        logger.exception("Forfeit inactive duel failed")


async def timeout_question(duel_id: int, qid: int, seconds: int, db: Database, bot: Bot) -> None:
    try:
        await asyncio.sleep(seconds)
        duel = await db.get_duel(duel_id)
        q = await db.fetchone("SELECT * FROM questions WHERE id=?", (qid,))
        if not duel or not q or duel['status'] != 'playing':
            return
        inactive_now: list[int] = []
        for uid in [duel['player1_id'], duel['player2_id']]:
            if await db.has_answered(duel_id, qid, uid):
                continue
            await db.record_answer(duel_id, qid, uid, None, q['correct_option'], None)
            unanswered_streaks[(duel_id, uid)] = unanswered_streaks.get((duel_id, uid), 0) + 1
            inactive_now.append(uid)
        if any(unanswered_streaks.get((duel_id, uid), 0) >= 3 for uid in [duel['player1_id'], duel['player2_id']]):
            inactive_users = [uid for uid in [duel['player1_id'], duel['player2_id']] if unanswered_streaks.get((duel_id, uid), 0) >= 3]
            await forfeit_inactive_duel(duel_id, inactive_users, db, bot)
            return
        await advance_duel(duel_id, db, bot, qid)
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
        pending_key = (duel_id, call.from_user.id, qid)
        correct_text = options_from_question(q)[q['correct_option'] - 1]
        explanation = f"\n\n{q['explanation']}" if 'explanation' in q.keys() and q['explanation'] else ""
        attempt = 1
        score = 1.0 if opt == q['correct_option'] else 0.0
        inserted = await db.record_answer(duel_id, qid, call.from_user.id, opt, q['correct_option'], ms, answer_score=score, attempt=attempt)
        if not inserted:
            await call.answer("قبلاً پاسخ داده‌ای.", show_alert=True)
            return
        unanswered_streaks[(duel_id, call.from_user.id)] = 0
        second_chance_pending.discard(pending_key)
        base_result_text = f"سوال {duel['current_index'] + 1}\nID: <code>{qid}</code>\n\n{q['text']}"
        result_text = f"{base_result_text}\n\n✅ پاورآپ جواب خودکار فعال شد.\nجواب درست: {options_from_question(q)[correct_option - 1]} ✅"
        try:
            await call.message.edit_text(result_text, reply_markup=result_report_keyboard(duel_id, qid))
        except Exception:
            logger.exception("Could not edit answered question message; sending fallback")
            await call.message.answer(result_text, reply_markup=result_report_keyboard(duel_id, qid))
        await call.answer()
        if await db.answered_count_for_question(duel_id, qid) >= 2:
            rt.timeout_task.cancel() if rt.timeout_task and not rt.timeout_task.done() else None
            await asyncio.sleep(1.5)
            await advance_duel(duel_id, db, bot, qid)
    except Exception:
        logger.exception("Answer failed")
        await call.answer("خطا", show_alert=True)


async def advance_duel(duel_id: int, db: Database, bot: Bot, expected_qid: int | None = None) -> None:
    rt = runtime(duel_id)
    async with rt.lock:
        duel = await db.get_duel(duel_id)
        if not duel or duel['status'] != 'playing':
            return
        if expected_qid is not None:
            current_q = await db.duel_question_by_seq(duel_id, duel['current_index'])
            if not current_q or int(current_q['id']) != int(expected_qid):
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
            line = "🤝 نتیجه: مساوی"
        elif winner == uid:
            line = "🎉 شما برنده شدید"
        else:
            line = "😔 شما بازنده شدید"
        rewards = result.get('transitions', {}).get(uid, {}).get('rewards', {})
        reward_text = (
            f"\n\n💰 سکه: {rewards.get('coins', 0):+}\n"
            f"⭐ ایکس‌پی: {rewards.get('xp', 0):+}\n"
            f"🏆 جام: {rewards.get('cups', 0):+}"
        )
        summary = await db.duel_user_summary(duel_id, uid)
        wrong_lines = "\n".join(f"• {x['genre']} — جواب درست: {x['correct']}" for x in summary['wrong_items']) or "—"
        opponent_id = duel['player1_id'] if uid == duel['player2_id'] else duel['player2_id']
        final_text = (
            f"🏁 دوئل تمام شد\n{line}{reward_text}\n\n"
            f"امتیاز شما: {stats[uid]['correct']} پاسخ صحیح\n"
            f"امتیاز حریف: {stats[opponent_id]['correct']} پاسخ صحیح\n\n"
            f"📊 خلاصه‌ی دوئل تو:\n\n"
            f"✅ درست: {summary['correct']} سوال\n"
            f"❌ غلط: {summary['wrong']} سوال\n"
            f"⏱ میانگین زمان پاسخ: {summary['avg_seconds']:.1f} ثانیه\n"
            f"🎯 دقت: {summary['accuracy']}%\n\n"
            f"📌 سوالاتی که غلط زدی:\n{wrong_lines}"
        )
        old_message_id = duel_main_message_ids.get((duel_id, uid))
        if old_message_id:
            try:
                await bot.edit_message_text(final_text, chat_id=uid, message_id=old_message_id, reply_markup=duel_finished_keyboard(duel_id, opponent_id))
            except Exception:
                await bot.send_message(uid, final_text, reply_markup=duel_finished_keyboard(duel_id, opponent_id))
        else:
            await bot.send_message(uid, final_text, reply_markup=duel_finished_keyboard(duel_id, opponent_id))
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
    for key in list(second_chance_pending):
        if key[0] == duel_id:
            second_chance_pending.discard(key)
    for key in list(second_chance_question.keys()):
        if key[0] == duel_id:
            second_chance_question.pop(key, None)
    for key in list(question_message_ids.keys()):
        if key[0] == duel_id:
            question_message_ids.pop(key, None)
    for key in list(duel_main_message_ids.keys()):
        if key[0] == duel_id:
            duel_main_message_ids.pop(key, None)
    for key in list(unanswered_streaks.keys()):
        if key[0] == duel_id:
            unanswered_streaks.pop(key, None)
    runtimes.pop(duel_id, None)


@router.callback_query(F.data.startswith("power:"))
async def powerup_callback(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        _, ptype, duel_s, qid_s = call.data.split(":")
        duel_id, qid = int(duel_s), int(qid_s)
        if ptype != 'auto':
            await call.message.answer("این پاورآپ دیگر فعال نیست.")
            return
        costs = await db.powerup_costs_for_user(duel_id, call.from_user.id)
        cost = costs['auto']
        if cost < 0:
            await call.message.answer("❌ سقف استفاده از این پاورآپ در این دوئل پر شده است.")
            return
        user = await db.get_user(call.from_user.id)
        q = await db.fetchone("SELECT * FROM questions WHERE id=?", (qid,))
        duel = await db.get_duel(duel_id)
        if not user or not q or not duel:
            await call.message.answer("پاورآپ نامعتبر است.")
            return
        if await db.has_answered(duel_id, qid, call.from_user.id):
            await call.message.answer("بعد از پاسخ دادن نمی‌توانی پاورآپ فعال کنی.")
            return
        if user['coins'] < cost:
            await call.message.answer(f"سکه کافی نداری. هزینه فعلی: {cost} سکه")
            return
        if await db.has_powerup(duel_id, qid, call.from_user.id, ptype):
            await call.message.answer("این پاورآپ را برای این سوال قبلاً استفاده کرده‌ای.")
            return
        ok = await db.mark_powerup(duel_id, qid, call.from_user.id, ptype)
        if not ok:
            await call.message.answer("امکان استفاده از این پاورآپ نیست.")
            return
        await db.change_coins(call.from_user.id, -cost, f"powerup_{ptype}", duel_id)
        rt = runtime(duel_id)
        ms = int((time.monotonic() - rt.question_started_at) * 1000)
        correct_option = int(q['correct_option'])
        inserted = await db.record_answer(duel_id, qid, call.from_user.id, correct_option, correct_option, ms, answer_score=1.0, attempt=1)
        if not inserted:
            await call.message.answer("قبلاً پاسخ داده‌ای.")
            return
        unanswered_streaks[(duel_id, call.from_user.id)] = 0
        base_result_text = f"سوال {duel['current_index'] + 1}\nID: <code>{qid}</code>\n\n{q['text']}"
        result_text = f"{base_result_text}\n\n✅ پاورآپ جواب خودکار فعال شد.\nجواب درست: {options_from_question(q)[correct_option - 1]} ✅"
        try:
            await call.message.edit_text(result_text, reply_markup=result_report_keyboard(duel_id, qid))
        except Exception:
            await call.message.answer(result_text, reply_markup=result_report_keyboard(duel_id, qid))
        if await db.answered_count_for_question(duel_id, qid) >= 2:
            rt.timeout_task.cancel() if rt.timeout_task and not rt.timeout_task.done() else None
            await asyncio.sleep(1.0)
            await advance_duel(duel_id, db, bot, qid)
    except Exception:
        logger.exception("Powerup failed")
        try:
            await call.message.answer("خطا در فعال‌سازی پاورآپ.")
        except Exception:
            logger.exception("Powerup error notify failed")


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


@router.callback_query(F.data.startswith("issue_report:"))
async def issue_report_start(call: CallbackQuery) -> None:
    try:
        _, duel_s, qid_s = call.data.split(":")
        await call.message.answer("دلیل گزارش را انتخاب کن:", reply_markup=issue_report_reasons_keyboard(int(duel_s), int(qid_s)))
        await call.answer()
    except Exception:
        logger.exception("Issue report start failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("issue_reason:"))
async def issue_report_reason(call: CallbackQuery, db: Database, bot: Bot, reports_channel_id: int | None) -> None:
    try:
        _, reason_code, duel_s, qid_s = call.data.split(":")
        qid = int(qid_s)
        duel_id = int(duel_s)
        if await db.report_exists(qid, call.from_user.id):
            await call.answer("شما قبلاً این سوال را گزارش کرده‌اید.", show_alert=True)
            return
        reason_map = {
            "wrong_answer": "جواب اشتباه است ❌",
            "unclear": "سوال نامفهوم است ❓",
            "duplicate_options": "گزینه‌ها تکراری‌اند 🔁",
            "other": "سایر 📝",
        }
        reason = reason_map.get(reason_code, reason_code)
        report_id = await db.add_report(qid, call.from_user.id, duel_id, reason)
        q = await db.get_question(qid)
        count = await db.report_count(qid)
        if reports_channel_id and q:
            await bot.send_message(
                reports_channel_id,
                f"⚠️ گزارش سوال مشکل‌دار\n"
                f"❓ سوال #{qid}: {q['text']}\n"
                f"👤 گزارش‌دهنده: {call.from_user.full_name} | ID: <code>{call.from_user.id}</code>\n"
                f"📋 دلیل: {reason}\n"
                f"📅 {jalali_datetime(now_iso())}\n"
                f"تعداد گزارش این سوال: {count}",
                reply_markup=report_admin_keyboard(qid, report_id),
            )
        if count >= await db.get_int("question_auto_disable_reports", 3):
            await db.deactivate_question(qid)
            if reports_channel_id:
                await bot.send_message(reports_channel_id, f"⏸ سوال #{qid} به دلیل {count} گزارش، خودکار غیرفعال شد.")
        await call.message.answer("گزارش ثبت شد. ممنون بابت کمک‌ات.")
        await call.answer()
    except Exception:
        logger.exception("Issue report reason failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("duel_report_answers:"))
async def duel_report_answers_callback(call: CallbackQuery, db: Database) -> None:
    await call.answer()
    try:
        duel_id = int(call.data.split(":")[1])
        rows = await db.fetchall("""SELECT q.*, a.selected_option, a.is_correct
                                  FROM duel_questions dq
                                  JOIN questions q ON q.id=dq.question_id
                                  LEFT JOIN duel_answers a ON a.duel_id=dq.duel_id AND a.question_id=q.id AND a.user_id=?
                                  WHERE dq.duel_id=? ORDER BY dq.seq""", (call.from_user.id, duel_id))
        if not rows:
            await call.message.answer("سوالات این دوئل پیدا نشد.")
            return
        lines = ["📋 سوالات و جواب‌های این دوئل:"]
        for i, q in enumerate(rows, 1):
            opts = options_from_question(q)
            correct_idx = int(q['correct_option'])
            selected = q['selected_option']
            selected_text = opts[int(selected) - 1] if selected else "بدون پاسخ"
            mark = "✅" if q['is_correct'] else "❌"
            lines.append(
                f"\n{i}. {q['text']}\n"
                f"✅ جواب صحیح: {opts[correct_idx-1]}\n"
                f"{mark} پاسخ شما: {selected_text}"
            )
        await call.message.answer("\n".join(lines))
    except Exception:
        logger.exception("Duel report answers failed")
        await call.message.answer("خطا در نمایش گزارش و جواب‌ها.")


@router.callback_query(F.data.startswith("opponent_profile:"))
async def opponent_profile_callback(call: CallbackQuery, db: Database) -> None:
    await call.answer()
    try:
        uid = int(call.data.split(":")[1])
        u = await db.get_user(uid)
        if not u:
            await call.message.answer("پروفایل حریف پیدا نشد.")
            return
        league = await db.get_user_league(u['cups'])
        await call.message.answer(
            f"👤 {u['first_name'] or 'کاربر'}\n"
            f"Level: {u['level']}\n"
            f"ایکس‌پی: {u['xp']}\n"
            f"لیگ: {league['name'] if league else '-'}\n"
            f"جام: {u['cups']}\n"
            f"برد/مساوی/شکست: {u['wins']}/{u['draws']}/{u['losses']}"
        )
    except Exception:
        logger.exception("Opponent profile failed")
        await call.message.answer("خطا در نمایش پروفایل حریف.")


async def rematch_timeout(requester_id: int, opponent_id: int, bot: Bot) -> None:
    try:
        await asyncio.sleep(60)
        key = (requester_id, opponent_id)
        task = rematch_timeout_tasks.pop(key, None)
        if task:
            await bot.send_message(requester_id, "⏱ درخواست بازی مجدد منقضی شد.")
            await bot.send_message(opponent_id, "⏱ زمان پاسخ به درخواست بازی مجدد تمام شد.")
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Rematch timeout failed")


@router.callback_query(F.data.startswith("rematch_request:"))
async def rematch_request_callback(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        opponent_id = int(call.data.split(":")[1])
        await bot.send_message(opponent_id, "حریف شما درخواست بازی مجدد ارسال کرده است.", reply_markup=rematch_keyboard(call.from_user.id))
        key = (call.from_user.id, opponent_id)
        old = rematch_timeout_tasks.pop(key, None)
        if old and not old.done():
            old.cancel()
        rematch_timeout_tasks[key] = asyncio.create_task(rematch_timeout(call.from_user.id, opponent_id, bot))
        await call.message.answer("درخواست بازی مجدد برای حریف ارسال شد.")
    except Exception:
        logger.exception("Rematch request failed")
        await call.message.answer("امکان ارسال درخواست بازی مجدد نبود.")


@router.callback_query(F.data.startswith("rematch_decline:"))
async def rematch_decline_callback(call: CallbackQuery, bot: Bot) -> None:
    await call.answer("رد شد", show_alert=False)
    try:
        requester_id = int(call.data.split(":")[1])
        for key, task in list(rematch_timeout_tasks.items()):
            if key[0] == requester_id and key[1] == call.from_user.id:
                rematch_timeout_tasks.pop(key, None)
                if not task.done():
                    task.cancel()
        await bot.send_message(requester_id, "❌ حریف درخواست بازی مجدد را رد کرد.")
        await call.message.edit_text("درخواست بازی مجدد رد شد.")
    except Exception:
        logger.debug("Could not edit rematch decline", exc_info=True)


@router.callback_query(F.data.startswith("rematch_accept:"))
async def rematch_accept_callback(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        requester_id = int(call.data.split(":")[1])
        for key, task in list(rematch_timeout_tasks.items()):
            if key[0] == requester_id and key[1] == call.from_user.id:
                rematch_timeout_tasks.pop(key, None)
                if not task.done():
                    task.cancel()
        token = invite_token()
        duel_id = await db.create_invite_duel(requester_id, token)
        await db.join_duel(duel_id, call.from_user.id)
        await bot.send_message(requester_id, "✅ حریف درخواست بازی مجدد را قبول کرد. انتخاب ژانر شروع شد.")
        await call.message.edit_text("درخواست پذیرفته شد. انتخاب ژانر شروع شد.")
        await offer_genres(duel_id, db, bot)
    except Exception:
        logger.exception("Rematch accept failed")
        await call.message.answer("خطا در قبول بازی مجدد.")
