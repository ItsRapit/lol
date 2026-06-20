from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent, ChosenInlineResult,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.db import Database

logger = logging.getLogger(__name__)
router = Router()


@dataclass
class GroupLobby:
    lobby_id: str
    starter_id: int
    chat_id: int | None = None
    message_id: int | None = None
    inline_message_id: str | None = None
    players: dict[int, str] = field(default_factory=dict)
    usernames: dict[int, str | None] = field(default_factory=dict)
    started: bool = False


@dataclass
class GroupGame:
    lobby: GroupLobby
    questions: list
    scores: dict[int, int] = field(default_factory=dict)
    answered: dict[int, dict[int, int]] = field(default_factory=dict)  # q_index -> user_id -> option
    resolved: set[int] = field(default_factory=set)
    remaining: dict[int, int] = field(default_factory=dict)
    timer_tasks: dict[int, asyncio.Task] = field(default_factory=dict)
    current_idx: int = 0
    question_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    question_ended: bool = False
    game_ended: bool = False


@dataclass
class GroupDuelGame:
    lobby: GroupLobby
    genres: dict[int, str]
    questions: list
    scores: dict[int, int] = field(default_factory=dict)
    answered: dict[int, dict[int, int]] = field(default_factory=dict)
    resolved: set[int] = field(default_factory=set)
    remaining: dict[int, int] = field(default_factory=dict)
    current_idx: int = 0


lobbies: dict[str, GroupLobby] = {}
games: dict[str, GroupGame] = {}
group_duels: dict[str, GroupDuelGame] = {}
group_duel_genres: dict[str, dict[int, str]] = {}
group_duel_offers: dict[str, list[str]] = {}


def trim_name(name: str, max_len: int = 20) -> str:
    return name if len(name) <= max_len else name[:max_len] + "..."


def bar(remaining: int, total: int) -> str:
    filled = max(0, min(10, round((remaining / max(1, total)) * 10)))
    return "▰" * filled + "▱" * (10 - filled)


def player_progress_lines(game: GroupGame, current_idx: int) -> str:
    lines = []
    for uid, name in game.lobby.players.items():
        marks = []
        for i in range(len(game.questions)):
            if i > current_idx:
                marks.append("⬜")
                continue
            ans = game.answered.get(i, {}).get(uid)
            if ans is None:
                marks.append("⬜")
            else:
                marks.append("✅" if ans == int(game.questions[i]['correct_option']) else "❌")
        lines.append(f"\u200f{trim_name(name)}\n\u200e{''.join(marks)}")
    return "\n\n".join(lines)


def group_question_text(game: GroupGame, idx: int, remaining: int, total_seconds: int, resolved: bool = False) -> str:
    q = game.questions[idx]
    answered_count = len(game.answered.get(idx, {}))
    total = len(game.lobby.players)
    if resolved:
        opts = [q['option1'], q['option2'], q['option3'], q['option4']]
        correct = opts[int(q['correct_option']) - 1]
        return (
            f"❓ سوال {idx+1} از {len(game.questions)}\n"
            f"━━━━━━━━━━━━━━\n{q['text']}\n━━━━━━━━━━━━━━\n"
            f"✅ جواب درست: {correct}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{player_progress_lines(game, idx)}"
        )
    return (
        f"❓ سوال {idx+1} از {len(game.questions)}\n"
        f"━━━━━━━━━━━━━━\n{q['text']}\n━━━━━━━━━━━━━━\n"
        f"⏱️ {bar(remaining, total_seconds)} {remaining}s\n"
        f"{player_progress_lines(game, idx)}\n"
        f"✅ {answered_count}/{total} نفر جواب دادن"
    )

def group_duel_genre_keyboard(lobby_id: str, genres: list[str], selected: dict[int, str] | None = None) -> InlineKeyboardMarkup:
    selected_values = set((selected or {}).values())
    b = InlineKeyboardBuilder()
    for idx, genre in enumerate(genres[:10]):
        taken = genre in selected_values
        b.button(
            text=(f"✅ {genre}" if taken else genre),
            callback_data=f"gduelgenre:{lobby_id}:{idx}",
        )
    b.adjust(2)
    return b.as_markup()


async def check_channel_membership(bot: Bot, user_id: int, channel_id: str) -> bool:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status not in {"left", "kicked", "banned"}
    except Exception:
        logger.exception("Force join check failed; allowing user")
        return True


async def ensure_user_started_callback(call: CallbackQuery, db: Database) -> bool:
    user_exists = await db.fetchone("SELECT id FROM users WHERE telegram_id = ?", (call.from_user.id,))
    if not user_exists:
        await call.answer(
            text="چالشینو\n\nابتدا ربات را استارت کنید 👇\n@ChalleshinoBot",
            show_alert=True,
        )
        return False
    return True


async def require_force_join(call: CallbackQuery, db: Database, bot: Bot) -> bool:
    enabled = await db.get_int("force_join_enabled", 0)
    channel = await db.get_setting("force_join_channel", "")
    if not enabled or not channel:
        return True
    ok = await check_channel_membership(bot, call.from_user.id, channel)
    if ok:
        return True
    await call.answer(f"برای بازی باید عضو کانال {channel} باشی", show_alert=True)
    url = f"https://t.me/{channel.lstrip('@')}" if channel.startswith('@') else None
    if url and call.message:
        await call.message.answer(
            "برای استفاده از چالشینو باید عضو کانال ما باشی 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="عضویت در کانال", url=url)]])
        )
    return False


async def require_registered_and_join(call: CallbackQuery, db: Database, bot: Bot) -> bool:
    user = await db.get_user(call.from_user.id)
    if not user:
        await call.answer(
            text="برای شرکت در بازی، ابتدا ربات را استارت کنید 👇 @ChalleshinoBot",
            show_alert=True,
        )
        return False
    return await require_force_join(call, db, bot)


def lobby_keyboard(lobby_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✋ پایه‌ام", callback_data=f"gquiz:join:{lobby_id}")
    b.button(text="🚀 شروع بازی", callback_data=f"gquiz:start:{lobby_id}")
    b.adjust(2)
    return b.as_markup()


def answer_keyboard(lobby_id: str, q_index: int, q) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, label in enumerate(["الف", "ب", "ج", "د"], 1):
        b.button(text=f"{label}) {q[f'option{i}']}", callback_data=f"gquiz:ans:{lobby_id}:{q_index}:{i}")
    b.adjust(1)
    return b.as_markup()


def lobby_text(lobby: GroupLobby, max_players: int) -> str:
    names = "\n".join(f"✅ {trim_name(n)}" for n in lobby.players.values())
    return (
        "🎮 بازی گروهی چالشینو\n\n"
        f"👤 سازنده: {trim_name(lobby.players.get(lobby.starter_id, 'شروع‌کننده'))}\n"
        f"👥 شرکت‌کنندگان: {len(lobby.players)}/{max_players}\n\n"
        f"{names}"
    )


async def edit_lobby(bot: Bot, lobby: GroupLobby, text: str, reply_markup=None) -> None:
    try:
        if lobby.inline_message_id:
            await bot.edit_message_text(text, inline_message_id=lobby.inline_message_id, reply_markup=reply_markup)
        elif lobby.chat_id and lobby.message_id:
            await bot.edit_message_text(text, chat_id=lobby.chat_id, message_id=lobby.message_id, reply_markup=reply_markup)
    except Exception:
        logger.exception("Edit group lobby failed")


@router.message(Command("quiz"))
async def group_quiz_start(message: Message, db: Database, bot: Bot) -> None:
    if message.chat.type == "private":
        await message.answer("این دستور برای گروه‌هاست.")
        return
    if not await db.get_user(message.from_user.id):
        me = await bot.get_me()
        await message.answer(
            "برای شروع بازی گروهی اول باید ربات را در پیوی استارت کنید 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 شروع ربات", url=f"https://t.me/{me.username}")]])
        )
        return
    key = f"chat:{message.chat.id}"
    if any(l.chat_id == message.chat.id and not l.started for l in lobbies.values()):
        await message.answer("در این گروه یک لابی فعال وجود دارد.")
        return
    lobby_id = f"chat_{abs(message.chat.id)}_{message.message_id}"
    lobby = GroupLobby(lobby_id=lobby_id, starter_id=message.from_user.id, chat_id=message.chat.id)
    lobby.players[message.from_user.id] = message.from_user.full_name
    lobby.usernames[message.from_user.id] = message.from_user.username
    lobbies[lobby_id] = lobby
    max_players = await db.get_int("group_quiz_max_players", 8)
    msg = await message.answer(lobby_text(lobby, max_players), reply_markup=lobby_keyboard(lobby_id))
    lobby.message_id = msg.message_id


@router.inline_query()
async def inline_handler(query: InlineQuery, db: Database) -> None:
    try:
        name = trim_name(query.from_user.first_name or "بازیکن")
        max_players = await db.get_int("group_quiz_max_players", 8)
        result = InlineQueryResultArticle(
        id="group_quiz",
        title="🎮 بازی گروهی",
        description="همه با هم یه سوال می‌بینن، هر جواب درست = یه امتیاز، هرکی آخر بازی امتیاز بیشتری داشت برنده‌ست",
        input_message_content=InputTextMessageContent(
            message_text=f"🎮 بازی گروهی چالشینو\n\n👤 سازنده: {name}\n👥 شرکت‌کنندگان: 1/{max_players}\n\n✅ {name}"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✋ پایه‌ام", callback_data="group_quiz_join")],
            [InlineKeyboardButton(text="🚀 شروع بازی", callback_data="group_quiz_start")],
        ]),
    )
        duel_result = InlineQueryResultArticle(
            id="group_duel",
            title="⚔️ دوئل",
            description="دو نفر با هم دوئل می‌کنن، هر کدوم ژانر انتخاب می‌کنن",
            input_message_content=InputTextMessageContent(
                message_text=f"⚔️ دوئل چالشینو\n\n👤 چالش‌دهنده: {name}\n\nمنتظر حریف..."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚔️ قبول می‌کنم", callback_data="group_duel_accept")],
            ]),
        )
        await query.answer([result, duel_result], cache_time=0)
    except Exception as e:
        logger.exception("Inline query error: %s", e)
        try:
            await query.answer([], cache_time=0)
        except Exception:
            logger.exception("Inline query empty answer failed")


@router.chosen_inline_result()
async def chosen_result_handler(chosen: ChosenInlineResult, bot: Bot, db: Database) -> None:
    if not chosen.inline_message_id:
        return
    if chosen.result_id == "group_quiz":
        lobby_id = f"inline_{abs(hash(chosen.inline_message_id))}"
        lobby = GroupLobby(lobby_id=lobby_id, starter_id=chosen.from_user.id, inline_message_id=chosen.inline_message_id)
        lobby.players[chosen.from_user.id] = chosen.from_user.full_name
        lobby.usernames[chosen.from_user.id] = chosen.from_user.username
        lobbies[lobby_id] = lobby
        try:
            await edit_lobby(bot, lobby, lobby_text(lobby, await db.get_int("group_quiz_max_players", 8)), lobby_keyboard(lobby_id))
        except Exception:
            logger.exception("Could not normalize inline quiz lobby")
    elif chosen.result_id == "group_duel":
        # Minimal inline-duel state stored as a lobby with only challenger.
        lobby_id = f"gduel_{abs(hash(chosen.inline_message_id))}"
        lobby = GroupLobby(lobby_id=lobby_id, starter_id=chosen.from_user.id, inline_message_id=chosen.inline_message_id)
        lobby.players[chosen.from_user.id] = chosen.from_user.full_name
        lobby.usernames[chosen.from_user.id] = chosen.from_user.username
        lobbies[lobby_id] = lobby


@router.callback_query(F.data.in_({"group_quiz_join_inline", "group_quiz_join"}))
async def inline_join_redirect(call: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await ensure_user_started_callback(call, db):
        return
    inline_id = call.inline_message_id
    if not inline_id:
        return
    lobby = next((l for l in lobbies.values() if l.inline_message_id == inline_id), None)
    if not lobby:
        await call.answer("بازی پیدا نشد. Inline Feedback را در BotFather روی 100% بگذار و دوباره تلاش کن.", show_alert=False)
        return
    await join_lobby(call, db, bot, lobby)


@router.callback_query(F.data == "group_quiz_start")
async def inline_start_game(call: CallbackQuery, db: Database, bot: Bot) -> None:
    inline_id = call.inline_message_id
    if not inline_id:
        await call.answer("بازی پیدا نشد", show_alert=False)
        return
    lobby = next((l for l in lobbies.values() if l.inline_message_id == inline_id), None)
    if not lobby:
        await call.answer("بازی پیدا نشد. Inline Feedback را در BotFather روی 100% بگذار و دوباره تلاش کن.", show_alert=False)
        return
    if call.from_user.id != lobby.starter_id:
        await call.answer("فقط کسی که بازی رو شروع کرده می‌تونه از این دکمه استفاده کنه", show_alert=False)
        return
    if not await require_registered_and_join(call, db, bot):
        return
    if len(lobby.players) < 2:
        await call.answer("حداقل 2 نفر لازم است.", show_alert=False)
        return
    await call.answer()
    await start_lobby_game(call, db, bot, lobby)


@router.callback_query(F.data.startswith("gquiz:join:"))
async def group_join(call: CallbackQuery, db: Database, bot: Bot) -> None:
    if not await ensure_user_started_callback(call, db):
        return
    lobby_id = call.data.split(":", 2)[2]
    lobby = lobbies.get(lobby_id)
    if lobby:
        await join_lobby(call, db, bot, lobby)


async def join_lobby(call: CallbackQuery, db: Database, bot: Bot, lobby: GroupLobby) -> None:
    if not await require_registered_and_join(call, db, bot):
        return
    await call.answer()
    max_players = await db.get_int("group_quiz_max_players", 8)
    if len(lobby.players) >= max_players and call.from_user.id not in lobby.players:
        await call.answer("ظرفیت تکمیل است.", show_alert=True)
        return
    lobby.players[call.from_user.id] = call.from_user.full_name
    lobby.usernames[call.from_user.id] = call.from_user.username
    await edit_lobby(bot, lobby, lobby_text(lobby, max_players), lobby_keyboard(lobby.lobby_id))


@router.callback_query(F.data.startswith("gquiz:start:"))
async def group_start_game(call: CallbackQuery, db: Database, bot: Bot) -> None:
    lobby_id = call.data.split(":", 2)[2]
    lobby = lobbies.get(lobby_id)
    if not lobby:
        await call.answer("بازی پیدا نشد", show_alert=False)
        return
    if call.from_user.id != lobby.starter_id:
        await call.answer("فقط کسی که بازی رو شروع کرده می‌تونه از این دکمه استفاده کنه", show_alert=False)
        return
    if not await require_registered_and_join(call, db, bot):
        return
    if len(lobby.players) < 2:
        await call.answer("حداقل 2 نفر لازم است.", show_alert=False)
        return
    await call.answer()
    await start_lobby_game(call, db, bot, lobby)


async def start_lobby_game(call: CallbackQuery, db: Database, bot: Bot, lobby: GroupLobby) -> None:
    try:
        lobby.started = True
        await edit_lobby(bot, lobby, "⏳ بازی در حال شروع...", None)
        count = await db.get_int("group_quiz_question_count", 5)
        rows = await db.fetchall("SELECT * FROM questions WHERE status='active' ORDER BY RANDOM() LIMIT ?", (count,))
        if not rows:
            await edit_lobby(bot, lobby, "سوال فعالی برای بازی گروهی وجود ندارد.", None)
            return
        game = GroupGame(lobby=lobby, questions=rows, scores={uid: 0 for uid in lobby.players})
        games[lobby.lobby_id] = game
        await send_group_question(bot, db, game, 0)
    except Exception as e:
        logger.exception("Group quiz start error: %s", e)
        await edit_lobby(bot, lobby, "❌ خطا در شروع بازی. دوباره امتحان کن.", None)


async def send_group_question(bot: Bot, db: Database, game: GroupGame, idx: int) -> None:
    if idx >= len(game.questions):
        await finish_group_game(bot, db, game)
        return
    q = game.questions[idx]
    game.current_idx = idx
    game.question_ended = False
    game.answered[idx] = {}
    total_seconds = await db.get_int('group_quiz_timer_seconds', 30)
    game.remaining[idx] = total_seconds
    text = group_question_text(game, idx, total_seconds, total_seconds)
    await edit_lobby(bot, game.lobby, text, answer_keyboard(game.lobby.lobby_id, idx, q))
    old_task = game.timer_tasks.get(idx)
    if old_task and not old_task.done():
        old_task.cancel()
    game.timer_tasks[idx] = asyncio.create_task(group_question_timeout(bot, db, game, idx, total_seconds))


@router.callback_query(F.data.startswith("gquiz:ans:"))
async def group_answer(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    _, _, lobby_id, idx_s, opt_s = call.data.split(":")
    game = games.get(lobby_id)
    if not game:
        return
    idx, opt = int(idx_s), int(opt_s)
    if idx in game.resolved:
        await call.answer("این سوال تمام شده", show_alert=False)
        return
    if call.from_user.id not in game.lobby.players:
        await call.answer("شما عضو این بازی نیستید", show_alert=True)
        return
    if call.from_user.id in game.answered.setdefault(idx, {}):
        await call.answer("قبلاً پاسخ دادی", show_alert=False)
        return
    game.answered[idx][call.from_user.id] = opt
    total = len(game.lobby.players)
    q = game.questions[idx]
    remaining = game.remaining.get(idx, await db.get_int('group_quiz_timer_seconds', 30))
    total_seconds = await db.get_int('group_quiz_timer_seconds', 30)
    await edit_lobby(bot, game.lobby, group_question_text(game, idx, remaining, total_seconds), answer_keyboard(lobby_id, idx, q))
    if len(game.answered[idx]) >= total:
        task = game.timer_tasks.get(idx)
        if task and not task.done():
            task.cancel()
        await resolve_group_question(bot, db, game, idx)


async def group_question_timeout(bot: Bot, db: Database, game: GroupGame, idx: int, seconds: int) -> None:
    try:
        step = 6
        remaining = seconds
        while remaining > 0:
            await asyncio.sleep(min(step, remaining))
            if idx in game.resolved or game.current_idx != idx:
                return
            remaining = max(0, remaining - step)
            game.remaining[idx] = remaining
            await edit_lobby(bot, game.lobby, group_question_text(game, idx, remaining, seconds), answer_keyboard(game.lobby.lobby_id, idx, game.questions[idx]))
            if len(game.answered.get(idx, {})) >= len(game.lobby.players):
                return
        if idx not in game.resolved:
            await resolve_group_question(bot, db, game, idx)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Group question timeout failed")


async def resolve_group_question(bot: Bot, db: Database, game: GroupGame, idx: int) -> None:
    async with game.question_lock:
        if game.question_ended or idx in game.resolved or game.current_idx != idx:
            return
        game.question_ended = True
        game.resolved.add(idx)
        task = game.timer_tasks.get(idx)
        if task and not task.done():
            task.cancel()
        q = game.questions[idx]
        correct = int(q['correct_option'])
        for uid, name in game.lobby.players.items():
            ok = game.answered.get(idx, {}).get(uid) == correct
            if ok:
                game.scores[uid] = game.scores.get(uid, 0) + 1
        text = group_question_text(game, idx, game.remaining.get(idx, 0), await db.get_int('group_quiz_timer_seconds', 30), resolved=True)
        await edit_lobby(bot, game.lobby, text, None)
        await asyncio.sleep(2)
        await send_group_question(bot, db, game, idx + 1)


async def notify_levelup_in_group(bot: Bot, chat_id: int, username: str, old_level: int, new_level: int, new_title: str) -> None:
    frames = [
        "⬆️ ...",
        "⬆️⬆️ ...",
        "⬆️⬆️⬆️ ...",
        f"🎉 تبریک {username}\n━━━━━━━━━━━\nلول {old_level} ← لول {new_level}\n{new_title}\n━━━━━━━━━━━",
    ]
    try:
        msg = await bot.send_message(chat_id, frames[0])
        for frame in frames[1:]:
            await asyncio.sleep(0.6)
            try:
                await msg.edit_text(frame)
            except Exception:
                logger.debug("Group levelup animation edit skipped", exc_info=True)
    except Exception:
        logger.exception("Group levelup notification failed")


async def finish_group_game(bot: Bot, db: Database, game: GroupGame) -> None:
    if game.game_ended:
        return
    game.game_ended = True
    max_score = max(game.scores.values() or [0])
    sorted_players = sorted(game.lobby.players.items(), key=lambda kv: game.scores.get(kv[0], 0), reverse=True)
    lines = []
    levelups: list[tuple[int, str, int, int, str]] = []
    for pos, (uid, name) in enumerate(sorted_players, 1):
        score = game.scores.get(uid, 0)
        xp = 20 if score == max_score and score > 0 else score * 5
        old_user = await db.get_user(uid)
        if not old_user:
            await db.upsert_user(uid, game.lobby.usernames.get(uid), name)
            old_user = await db.get_user(uid)
        old_level = int(old_user['level']) if old_user else 1
        if xp:
            await db.change_xp(uid, xp, "group_quiz")
            await db.sync_user_title(uid)
        new_user = await db.get_user(uid)
        new_level = int(new_user['level']) if new_user else old_level
        if new_level > old_level:
            title = await db.user_title(uid)
            title_text = f"{title['emoji'] or ''} {title['name']}".strip() if title else await db.get_level_display(new_level)
            mention = f"@{game.lobby.usernames.get(uid)}" if game.lobby.usernames.get(uid) else trim_name(name)
            levelups.append((uid, mention, old_level, new_level, title_text))
        lines.append(f"{pos}. {trim_name(name)} — {score}/{len(game.questions)} ✅ (+{xp} XP)")
    text = "🏆 نتیجه‌ی بازی\n━━━━━━━━━━━━━━\n" + "\n".join(lines) + "\n━━━━━━━━━━━━━━"
    await edit_lobby(bot, game.lobby, text, None)
    await db.log_group_game('quiz', game.lobby.chat_id, game.lobby.inline_message_id, len(game.lobby.players), len(game.questions))
    if game.lobby.chat_id:
        for _, mention, old_level, new_level, title_text in levelups:
            await notify_levelup_in_group(bot, game.lobby.chat_id, mention, old_level, new_level, title_text)
            await asyncio.sleep(1)
    games.pop(game.lobby.lobby_id, None)
    lobbies.pop(game.lobby.lobby_id, None)


@router.callback_query(F.data == "group_duel_accept")
async def group_duel_accept(call: CallbackQuery, bot: Bot, db: Database) -> None:
    try:
        if not await ensure_user_started_callback(call, db):
            return
        if not await require_registered_and_join(call, db, bot):
            return
        await call.answer()
        inline_id = call.inline_message_id
        if not inline_id:
            await call.answer("این دکمه فقط برای inline duel است", show_alert=False)
            return
        lobby = next((l for l in lobbies.values() if l.inline_message_id == inline_id and l.lobby_id.startswith("gduel_")), None)
        if not lobby:
            lobby_id = f"gduel_{abs(hash(inline_id))}"
            lobby = GroupLobby(lobby_id=lobby_id, starter_id=call.from_user.id, inline_message_id=inline_id)
            lobby.players[call.from_user.id] = call.from_user.full_name
            lobby.usernames[call.from_user.id] = call.from_user.username
            lobbies[lobby_id] = lobby
        if call.from_user.id == lobby.starter_id:
            await call.answer("خودت نمی‌تونی حریف خودت بشی", show_alert=False)
            return
        if len(lobby.players) >= 2:
            await call.answer("این دوئل حریف دارد", show_alert=False)
            return
        lobby.players[call.from_user.id] = call.from_user.full_name
        lobby.usernames[call.from_user.id] = call.from_user.username
        names = list(lobby.players.values())
        group_duel_genres[lobby.lobby_id] = {}
        all_genres = await db.all_genres()
        offers = random.sample(all_genres, min(8, len(all_genres))) if all_genres else []
        group_duel_offers[lobby.lobby_id] = offers
        await bot.edit_message_text(
            f"⚔️ دوئل چالشینو\n\n👤 {trim_name(names[0])} vs {trim_name(names[1])}\n\nهر دو نفر ژانر مورد نظرشون رو همین‌جا انتخاب کنن 👇\n⏳ {trim_name(names[0])}: در حال انتخاب...\n⏳ {trim_name(names[1])}: در حال انتخاب...",
            inline_message_id=inline_id,
            reply_markup=group_duel_genre_keyboard(lobby.lobby_id, offers, group_duel_genres[lobby.lobby_id]),
        )
    except Exception:
        logger.exception("Group duel accept failed")
        try:
            await call.answer("خطا در قبول دوئل", show_alert=False)
        except Exception:
            pass


@router.callback_query(F.data.startswith("gduelgenre:"))
async def group_duel_genre_selected(call: CallbackQuery, bot: Bot, db: Database) -> None:
    await call.answer()
    try:
        _, lobby_id, idx_s = call.data.split(":", 2)
        lobby = lobbies.get(lobby_id)
        offers = group_duel_offers.get(lobby_id, [])
        if not lobby or call.from_user.id not in lobby.players:
            await call.answer("دوئل پیدا نشد یا شما عضو آن نیستید", show_alert=False)
            return
        try:
            genre = offers[int(idx_s)]
        except Exception:
            await call.answer("ژانر نامعتبر است", show_alert=False)
            return
        current = group_duel_genres.setdefault(lobby_id, {})
        taken_by = next((uid for uid, g in current.items() if g == genre and uid != call.from_user.id), None)
        if taken_by:
            await call.answer("این ژانر قبلاً توسط حریف انتخاب شده", show_alert=False)
            return
        current[call.from_user.id] = genre
        lines = []
        for uid, name in lobby.players.items():
            g = current.get(uid)
            lines.append((f"✅ {trim_name(name)}: {g}" if g else f"⏳ {trim_name(name)}: در حال انتخاب..."))
        if lobby.inline_message_id:
            await bot.edit_message_text(
                "⚔️ دوئل چالشینو\n\n" + "\n".join(lines),
                inline_message_id=lobby.inline_message_id,
                reply_markup=group_duel_genre_keyboard(lobby_id, offers, current),
            )
        if len(current) >= 2:
            genres = [current[uid] for uid in lobby.players]
            if lobby.inline_message_id:
                await bot.edit_message_text(
                    f"⚔️ دوئل چالشینو\n\nژانرها انتخاب شدند:\n{genres[0]} + {genres[1]}\n\n⏳ دوئل در حال شروع...",
                    inline_message_id=lobby.inline_message_id,
                    reply_markup=None,
                )
            await start_group_duel(bot, db, lobby)
    except Exception:
        logger.exception("Group duel genre select failed")
        try:
            await call.answer("خطا در ثبت ژانر دوئل", show_alert=False)
        except Exception:
            pass


def duel_answer_keyboard(lobby_id: str, q_index: int, q) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, label in enumerate(["الف", "ب", "ج", "د"], 1):
        b.button(text=f"{label}) {q[f'option{i}']}", callback_data=f"gduelans:{lobby_id}:{q_index}:{i}")
    b.adjust(1)
    return b.as_markup()


def group_duel_question_text(game: GroupDuelGame, idx: int, remaining: int, total_seconds: int, resolved: bool = False) -> str:
    q = game.questions[idx]
    if resolved:
        opts = [q['option1'], q['option2'], q['option3'], q['option4']]
        lines = []
        for uid, name in game.lobby.players.items():
            ans = game.answered.get(idx, {}).get(uid)
            ok = ans == int(q['correct_option'])
            lines.append(f"{trim_name(name)}: {'✅' if ok else '❌'}" + (f" (+1)" if ok else ""))
        return f"⚔️ دوئل | سوال {idx+1}/{len(game.questions)}\n━━━━━━━━━━━━━━\n{q['text']}\n✅ جواب درست: {opts[int(q['correct_option'])-1]}\n━━━━━━━━━━━━━━\n" + "\n".join(lines)
    return f"⚔️ دوئل | سوال {idx+1}/{len(game.questions)}\n━━━━━━━━━━━━━━\n{q['text']}\n━━━━━━━━━━━━━━\n⏱️ {bar(remaining, total_seconds)} {remaining}s"


async def start_group_duel(bot: Bot, db: Database, lobby: GroupLobby) -> None:
    genres = list(group_duel_genres.get(lobby.lobby_id, {}).values())
    total = await db.get_int("duel_question_count", 7)
    first_n = (total + 1) // 2
    second_n = total - first_n
    q1 = await db.select_questions_for_duel([genres[0]], first_n, set())
    q2 = await db.select_questions_for_duel([genres[1]], second_n, {q['id'] for q in q1})
    questions = q1 + q2
    random.shuffle(questions)
    if not questions:
        await edit_lobby(bot, lobby, "برای ژانرهای انتخاب‌شده سوال فعالی پیدا نشد.", None)
        return
    game = GroupDuelGame(lobby=lobby, genres=group_duel_genres[lobby.lobby_id], questions=questions, scores={uid: 0 for uid in lobby.players})
    group_duels[lobby.lobby_id] = game
    await send_group_duel_question(bot, db, game, 0)


async def send_group_duel_question(bot: Bot, db: Database, game: GroupDuelGame, idx: int) -> None:
    if idx >= len(game.questions):
        await finish_group_duel(bot, db, game)
        return
    game.current_idx = idx
    game.answered[idx] = {}
    total_seconds = await db.get_int('group_quiz_timer_seconds', 30)
    game.remaining[idx] = total_seconds
    await edit_lobby(bot, game.lobby, group_duel_question_text(game, idx, total_seconds, total_seconds), duel_answer_keyboard(game.lobby.lobby_id, idx, game.questions[idx]))
    asyncio.create_task(group_duel_timeout(bot, db, game, idx, total_seconds))


@router.callback_query(F.data.startswith("gduelans:"))
async def group_duel_answer(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    _, lobby_id, idx_s, opt_s = call.data.split(":")
    game = group_duels.get(lobby_id)
    if not game:
        return
    idx, opt = int(idx_s), int(opt_s)
    if call.from_user.id not in game.lobby.players:
        await call.answer("شما عضو این دوئل نیستید", show_alert=True)
        return
    if call.from_user.id in game.answered.setdefault(idx, {}):
        await call.answer("قبلاً پاسخ دادی", show_alert=False)
        return
    game.answered[idx][call.from_user.id] = opt
    if len(game.answered[idx]) >= len(game.lobby.players):
        await resolve_group_duel_question(bot, db, game, idx)


async def group_duel_timeout(bot: Bot, db: Database, game: GroupDuelGame, idx: int, seconds: int) -> None:
    step = 6
    remaining = seconds
    try:
        while remaining > 0:
            await asyncio.sleep(min(step, remaining))
            if idx in game.resolved or game.current_idx != idx:
                return
            remaining = max(0, remaining - step)
            game.remaining[idx] = remaining
            await edit_lobby(bot, game.lobby, group_duel_question_text(game, idx, remaining, seconds), duel_answer_keyboard(game.lobby.lobby_id, idx, game.questions[idx]))
        await resolve_group_duel_question(bot, db, game, idx)
    except Exception:
        logger.exception("Group duel timeout failed")


async def resolve_group_duel_question(bot: Bot, db: Database, game: GroupDuelGame, idx: int) -> None:
    if idx in game.resolved or game.current_idx != idx:
        return
    game.resolved.add(idx)
    q = game.questions[idx]
    for uid, ans in game.answered.get(idx, {}).items():
        if ans == int(q['correct_option']):
            game.scores[uid] = game.scores.get(uid, 0) + 1
    await edit_lobby(bot, game.lobby, group_duel_question_text(game, idx, 0, await db.get_int('group_quiz_timer_seconds', 30), True), None)
    await asyncio.sleep(2)
    await send_group_duel_question(bot, db, game, idx + 1)


async def finish_group_duel(bot: Bot, db: Database, game: GroupDuelGame) -> None:
    players = list(game.lobby.players.items())
    s1 = game.scores.get(players[0][0], 0)
    s2 = game.scores.get(players[1][0], 0)
    if s1 > s2:
        winner_id, winner_name = players[0]
    elif s2 > s1:
        winner_id, winner_name = players[1]
    else:
        winner_id, winner_name = None, "مساوی"
    xp_per = await db.get_int("reward_xp_per_correct", 15)
    for uid, name in players:
        score = game.scores.get(uid, 0)
        xp_amount = score * xp_per
        if xp_amount:
            await db.change_xp(uid, xp_amount, "group_duel_correct")
        try:
            await bot.send_message(uid, f"🎁 جوایز دوئل\nدرست: {score}\nایکس‌پی: +{xp_amount}\nسکه و جام در دوئل گروهی داده نمی‌شود.")
        except Exception:
            logger.exception("Could not send group duel reward PM")
    text = f"⚔️ نتیجه‌ی دوئل\n━━━━━━━━━━━━━━\n🏆 {trim_name(winner_name)} برد\n{s1} درست vs {s2} درست\n━━━━━━━━━━━━━━\n🎁 جوایز در پیوی ارسال شد"
    await edit_lobby(bot, game.lobby, text, None)
    await db.log_group_game('duel', game.lobby.chat_id, game.lobby.inline_message_id, len(game.lobby.players), len(game.questions))
    group_duels.pop(game.lobby.lobby_id, None)
    lobbies.pop(game.lobby.lobby_id, None)
    group_duel_genres.pop(game.lobby.lobby_id, None)
    group_duel_offers.pop(game.lobby.lobby_id, None)
