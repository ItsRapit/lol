from __future__ import annotations

import asyncio
import logging
import random
import secrets
from dataclasses import dataclass, field
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent, ChosenInlineResult,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.db import Database, now_iso
from app.time_utils import jalali_datetime
from app.keyboards import group_duel_lobby_keyboard, group_finished_keyboard, group_replay_keyboard, group_report_questions_keyboard, report_admin_keyboard, group_finished_keyboard, group_report_questions_keyboard

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
    timeout_task: asyncio.Task | None = None
    stage: str = "genre"
    offered_genres: list[str] = field(default_factory=list)
    selected_genres: list[str] = field(default_factory=list)
    question_count: int = 5
    timer_seconds: int = 30


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
    auto_answer_uses: dict[int, int] = field(default_factory=dict)  # user_id -> uses


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
    auto_answer_uses: dict[int, int] = field(default_factory=dict)  # user_id -> uses


lobbies: dict[str, GroupLobby] = {}
games: dict[str, GroupGame] = {}
group_duels: dict[str, GroupDuelGame] = {}
group_duel_genres: dict[str, dict[int, str]] = {}
group_duel_offers: dict[str, list[str]] = {}
inline_group_pending: dict[str, dict] = {}
completed_group_games: dict[str, GroupGame] = {}
completed_group_duels: dict[str, GroupDuelGame] = {}


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
            f"❓ سوال {idx+1} از {len(game.questions)}\n\n"
            f"{q['text']}\n\n"
            f"✅ جواب درست {correct}\n\n"
            f"{player_progress_lines(game, idx)}"
        )
    return (
        f"❓ سوال {idx+1} از {len(game.questions)}\n\n"
        f"{q['text']}\n\n"
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


async def ensure_user_started_callback(call: CallbackQuery, db: Database, referrer_id: int | None = None) -> bool:
    user_exists = await db.fetchone("SELECT id FROM users WHERE telegram_id = ?", (call.from_user.id,))
    if not user_exists:
        payload = f"ref_{referrer_id}" if referrer_id else "from_group_game"
        await call.answer(
            text="چالشینو\n\nاول ربات رو استارت کن",
            show_alert=True,
            url=f"https://t.me/ChalleshinoBot?start={payload}",
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
    await call.answer(
        text="ربات رو دوباره استارت کن",
        show_alert=True,
        url="https://t.me/ChalleshinoBot?start=force_join",
    )
    return False


async def require_registered_and_join(call: CallbackQuery, db: Database, bot: Bot) -> bool:
    user = await db.get_user(call.from_user.id)
    if not user:
        await call.answer(
            text="برای شرکت تو بازی اول ربات رو استارت کن 👇 @ChalleshinoBot",
            show_alert=True,
        )
        return False
    return await require_force_join(call, db, bot)


def active_lobbies_in_chat(chat_id: int) -> list[GroupLobby]:
    return [l for l in lobbies.values() if l.chat_id == chat_id and not l.started]


def is_user_in_active_group_game(user_id: int) -> bool:
    for lobby in lobbies.values():
        if user_id in lobby.players:
            return True
    for game in games.values():
        if user_id in game.lobby.players:
            return True
    for game in group_duels.values():
        if user_id in game.lobby.players:
            return True
    return False


async def lobby_timeout(lobby_id: str, bot: Bot) -> None:
    try:
        await asyncio.sleep(600)
        lobby = lobbies.get(lobby_id)
        if not lobby or lobby.started:
            return
        lobbies.pop(lobby_id, None)
        try:
            if lobby.inline_message_id:
                await bot.edit_message_text("⏰ لابی بازی به دلیل عدم شروع تو ۱۰ دقیقه بسته شد", inline_message_id=lobby.inline_message_id, reply_markup=group_replay_keyboard())
            elif lobby.chat_id and lobby.message_id:
                await bot.edit_message_text("⏰ لابی بازی به دلیل عدم شروع تو ۱۰ دقیقه بسته شد", chat_id=lobby.chat_id, message_id=lobby.message_id, reply_markup=group_replay_keyboard())
        except Exception:
            logger.warning("Lobby timeout edit failed", exc_info=True)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Lobby timeout failed")


def inline_group_genre_text(data: dict) -> str:
    selected = set(data.get("selected", []))
    lines = []
    for g in data.get("genres", [])[:8]:
        lines.append(("☑️ " if g in selected else "⬜ ") + g)
    return "🎮 بازی گروهی چالشینو\n\nمرحله اول: انتخاب ژانر\n\n" + "\n".join(lines)


def inline_group_genre_keyboard(token: str) -> InlineKeyboardMarkup:
    data = inline_group_pending.get(token, {})
    selected = set(data.get("selected", []))
    b = InlineKeyboardBuilder()
    for idx, genre in enumerate(data.get("genres", [])[:8]):
        b.button(text=("☑️ " if genre in selected else "⬜ ") + genre, callback_data=f"gqgenrei:{token}:{idx}")
    if len(selected) == 2:
        b.button(text="ادامه", callback_data=f"gqcontinuei:{token}")
    else:
        b.button(text="ادامه (2 ژانر انتخاب کن)", callback_data="noop")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()


async def ensure_inline_group_lobby(token: str, inline_message_id: str | None) -> GroupLobby | None:
    if not inline_message_id:
        return None
    existing = next((l for l in lobbies.values() if l.inline_message_id == inline_message_id and l.lobby_id.startswith("inline_")), None)
    if existing:
        return existing
    data = inline_group_pending.get(token)
    if not data:
        return None
    lobby_id = f"inline_{abs(hash(inline_message_id))}"
    lobby = GroupLobby(lobby_id=lobby_id, starter_id=data["starter_id"], inline_message_id=inline_message_id)
    lobby.players[data["starter_id"]] = data["name"]
    lobby.usernames[data["starter_id"]] = data.get("username")
    lobby.offered_genres = list(data.get("genres", []))
    lobby.selected_genres = list(data.get("selected", []))
    lobby.question_count = int(data.get("question_count", 5))
    lobby.timer_seconds = int(data.get("timer_seconds", 30))
    lobbies[lobby_id] = lobby
    return lobby


def group_genre_keyboard(lobby: GroupLobby) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    selected = set(lobby.selected_genres)
    for idx, genre in enumerate(lobby.offered_genres[:8]):
        mark = "☑️" if genre in selected else "⬜"
        b.button(text=f"{mark} {genre}", callback_data=f"gqgenre:{lobby.lobby_id}:{idx}")
    if len(lobby.selected_genres) == 2:
        b.button(text="ادامه", callback_data=f"gqcontinue:{lobby.lobby_id}")
    else:
        b.button(text="ادامه (2 ژانر انتخاب کن)", callback_data="noop")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()


def lobby_keyboard(lobby_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✋ پایه‌ام", callback_data=f"gquiz:join:{lobby_id}")
    b.button(text="🚀 شروع بازی", callback_data=f"gquiz:start:{lobby_id}")
    b.button(text="🚪 خروج", callback_data=f"gquiz:leave:{lobby_id}")
    b.adjust(3)
    return b.as_markup()


def group_settings_keyboard(lobby: GroupLobby) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for sec in (10, 20, 30):
        b.button(text=(f"☑️ {sec}s" if lobby.timer_seconds == sec else f"{sec}s"), callback_data=f"gqtime:{lobby.lobby_id}:{sec}")
    for cnt in (5, 10, 15):
        b.button(text=(f"☑️ {cnt}" if lobby.question_count == cnt else str(cnt)), callback_data=f"gqcount:{lobby.lobby_id}:{cnt}")
    b.button(text="✋ پایه‌ام", callback_data=f"gquiz:join:{lobby.lobby_id}")
    b.button(text="🚀 شروع بازی", callback_data=f"gquiz:start:{lobby.lobby_id}")
    b.button(text="🚪 خروج", callback_data=f"gquiz:leave:{lobby.lobby_id}")
    b.adjust(3, 3, 2, 1)
    return b.as_markup()


def answer_keyboard(lobby_id: str, q_index: int, q, auto_cost: int = 10) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, label in enumerate(["الف", "ب", "ج", "د"], 1):
        b.button(text=f"{label}) {q[f'option{i}']}", callback_data=f"gquiz:ans:{lobby_id}:{q_index}:{i}")
    b.button(text=f"🎯 جواب خودکار — {auto_cost}🪙", callback_data=f"gquiz:auto:{lobby_id}:{q_index}")
    b.adjust(2, 2, 1)
    return b.as_markup()


def lobby_text(lobby: GroupLobby, max_players: int) -> str:
    if lobby.stage == "genre":
        genres = "\n".join(("☑️ " if g in lobby.selected_genres else "⬜ ") + g for g in lobby.offered_genres[:8])
        return f"🎮 بازی گروهی چالشینو\n\nمرحله اول: انتخاب ژانر\n\n{genres}"
    names = "\n".join(f"✅ {trim_name(n)}" for n in lobby.players.values())
    return (
        "🎮 بازی گروهی چالشینو\n\n"
        "⚙️ تنظیمات بازی\n"
        f"زمان پاسخ به هر سوال: {lobby.timer_seconds}s\n"
        f"تعداد سوال: {lobby.question_count}\n"
        f"ژانرها: {', '.join(lobby.selected_genres) if lobby.selected_genres else '-'}\n\n"
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
        await message.answer("این دستور برای گروه‌هاست")
        return
    if not await db.get_user(message.from_user.id):
        me = await bot.get_me()
        await message.answer(
            "برای شروع بازی گروهی اول باید ربات رو تو پی‌وی استارت کنی 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 شروع ربات", url=f"https://t.me/{me.username}")]])
        )
        return
    if is_user_in_active_group_game(message.from_user.id):
        await message.answer("تا بازی فعلیت تموم نشده نمی‌تونی بازی جدید شروع کنی")
        return
    if len(active_lobbies_in_chat(message.chat.id)) >= 3:
        await message.answer("تو این گروه ۳ لابی فعال هست، صبر کن یکی تموم بشه")
        return
    lobby_id = f"chat_{abs(message.chat.id)}_{message.message_id}"
    lobby = GroupLobby(lobby_id=lobby_id, starter_id=message.from_user.id, chat_id=message.chat.id)
    lobby.players[message.from_user.id] = message.from_user.full_name
    lobby.usernames[message.from_user.id] = message.from_user.username
    genres = await db.all_genres()
    lobby.offered_genres = random.sample(genres, min(8, len(genres))) if genres else []
    lobby.question_count = await db.get_int("group_quiz_question_count", 5)
    lobby.timer_seconds = await db.get_int("group_quiz_timer_seconds", 30)
    lobbies[lobby_id] = lobby
    max_players = await db.get_int("group_quiz_max_players", 8)
    msg = await message.answer(lobby_text(lobby, max_players), reply_markup=group_genre_keyboard(lobby))
    lobby.message_id = msg.message_id
    lobby.timeout_task = asyncio.create_task(lobby_timeout(lobby_id, message.bot))


@router.inline_query()
async def inline_handler(query: InlineQuery, db: Database) -> None:
    try:
        name = trim_name(query.from_user.first_name or "بازیکن")
        max_players = await db.get_int("group_quiz_max_players", 8)
        genres = await db.all_genres()
        token = secrets.token_urlsafe(6)
        inline_group_pending[token] = {
            "starter_id": query.from_user.id,
            "name": query.from_user.full_name,
            "username": query.from_user.username,
            "genres": random.sample(genres, min(8, len(genres))) if genres else [],
            "selected": [],
            "question_count": await db.get_int("group_quiz_question_count", 5),
            "timer_seconds": await db.get_int("group_quiz_timer_seconds", 30),
            "max_players": max_players,
        }
        result = InlineQueryResultArticle(
            id=f"group_quiz:{token}",
            title="🎮 بازی گروهی",
            description="همه با هم یه سوال می‌بینن، هر جواب درست = یه امتیاز، هرکی آخر بازی امتیاز بیشتری داشت برنده‌ست",
            input_message_content=InputTextMessageContent(message_text=inline_group_genre_text(inline_group_pending[token])),
            reply_markup=inline_group_genre_keyboard(token),
        )
        duel_result = InlineQueryResultArticle(
            id="group_duel",
            title="⚔️ دوئل",
            description="دو نفر با هم دوئل می‌کنن، هر کدوم ژانر انتخاب می‌کنن",
            input_message_content=InputTextMessageContent(
                message_text=f"⚔️ دوئل چالشینو\n\n👤 چالش‌دهنده: {name}\n\nمنتظر حریف..."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⚔️ قبول می‌کنم", callback_data="group_duel_accept"),
            ]]),
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
    if chosen.result_id.startswith("group_quiz"):
        token = chosen.result_id.split(":", 1)[1] if ":" in chosen.result_id else ""
        lobby = await ensure_inline_group_lobby(token, chosen.inline_message_id)
        if lobby:
            lobby.timeout_task = asyncio.create_task(lobby_timeout(lobby.lobby_id, bot))
            try:
                await edit_lobby(bot, lobby, lobby_text(lobby, await db.get_int("group_quiz_max_players", 8)), group_genre_keyboard(lobby))
            except Exception:
                logger.exception("Could not normalize inline quiz lobby")
    elif chosen.result_id == "group_duel":
        # Minimal inline-duel state stored as a lobby with only challenger.
        lobby_id = f"gduel_{abs(hash(chosen.inline_message_id))}"
        lobby = GroupLobby(lobby_id=lobby_id, starter_id=chosen.from_user.id, inline_message_id=chosen.inline_message_id)
        lobby.players[chosen.from_user.id] = chosen.from_user.full_name
        lobby.usernames[chosen.from_user.id] = chosen.from_user.username
        lobbies[lobby_id] = lobby
        lobby.timeout_task = asyncio.create_task(lobby_timeout(lobby_id, bot))
        try:
            await bot.edit_message_reply_markup(
                inline_message_id=chosen.inline_message_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⚔️ قبول می‌کنم", callback_data="group_duel_accept"),
                ]]),
            )
        except Exception:
            logger.exception("Could not ensure group duel lobby leave button")


@router.callback_query(F.data.in_({"group_quiz_join_inline", "group_quiz_join"}))
async def inline_join_redirect(call: CallbackQuery, db: Database, bot: Bot) -> None:
    inline_id = call.inline_message_id
    if not inline_id:
        return
    lobby = next((l for l in lobbies.values() if l.inline_message_id == inline_id), None)
    if not lobby:
        await call.answer("بازی پیدا نشد، Inline Feedback رو تو BotFather روی ۱۰۰٪ بذار و دوباره امتحان کن", show_alert=False)
        return
    if not await ensure_user_started_callback(call, db, lobby.starter_id):
        return
    await join_lobby(call, db, bot, lobby)


async def close_or_update_group_lobby_after_leave(bot: Bot, lobby: GroupLobby, user_id: int) -> None:
    if user_id == lobby.starter_id:
        await edit_lobby(bot, lobby, "❌ بازی به دلیل خروج سازنده بسته شد", group_replay_keyboard())
        lobbies.pop(lobby.lobby_id, None)
        return
    lobby.players.pop(user_id, None)
    lobby.usernames.pop(user_id, None)
    await edit_lobby(bot, lobby, lobby_text(lobby, 8), group_settings_keyboard(lobby) if lobby.stage == "settings" else group_genre_keyboard(lobby))


@router.callback_query(F.data == "group_quiz_leave")
async def inline_group_quiz_leave(call: CallbackQuery, bot: Bot) -> None:
    await call.answer()
    lobby = next((l for l in lobbies.values() if l.inline_message_id == call.inline_message_id and not l.started), None)
    if not lobby:
        await call.answer("لابی پیدا نشد", show_alert=False)
        return
    if call.from_user.id not in lobby.players:
        await call.answer("تو این لابی نیستی", show_alert=False)
        return
    await close_or_update_group_lobby_after_leave(bot, lobby, call.from_user.id)


@router.callback_query(F.data.startswith("gquiz:leave:"))
async def group_quiz_leave(call: CallbackQuery, bot: Bot) -> None:
    await call.answer()
    lobby_id = call.data.split(":", 2)[2]
    lobby = lobbies.get(lobby_id)
    if not lobby or lobby.started:
        await call.answer("لابی پیدا نشد", show_alert=False)
        return
    if call.from_user.id not in lobby.players:
        await call.answer("تو این لابی نیستی", show_alert=False)
        return
    await close_or_update_group_lobby_after_leave(bot, lobby, call.from_user.id)


@router.callback_query(F.data.startswith("gqgenre:"))
async def group_quiz_genre_select(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        _, lobby_id, idx_s = call.data.split(":", 2)
        lobby = lobbies.get(lobby_id)
        if not lobby or lobby.started:
            await call.answer("لابی پیدا نشد", show_alert=False)
            return
        if call.from_user.id != lobby.starter_id:
            await call.answer("فقط سازنده ژانرهای بازی رو انتخاب می‌کنه", show_alert=False)
            return
        try:
            genre = lobby.offered_genres[int(idx_s)]
        except Exception:
            await call.answer("ژانر نامعتبره", show_alert=False)
            return
        if genre in lobby.selected_genres:
            lobby.selected_genres.remove(genre)
        elif len(lobby.selected_genres) < 2:
            lobby.selected_genres.append(genre)
        else:
            await call.answer("دقیقاً ۲ ژانر انتخاب کن", show_alert=False)
            return
        await edit_lobby(bot, lobby, lobby_text(lobby, await db.get_int("group_quiz_max_players", 8)), group_genre_keyboard(lobby))
    except Exception:
        logger.exception("Group quiz genre select failed")


@router.callback_query(F.data.startswith("gqcontinue:"))
async def group_quiz_continue_settings(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        lobby_id = call.data.split(":", 1)[1]
        lobby = lobbies.get(lobby_id)
        if not lobby:
            await call.answer("لابی پیدا نشد", show_alert=False)
            return
        if call.from_user.id != lobby.starter_id:
            await call.answer("فقط سازنده می‌تونه ادامه بده", show_alert=False)
            return
        if len(lobby.selected_genres) != 2:
            await call.answer("اول ۲ ژانر انتخاب کن", show_alert=False)
            return
        lobby.stage = "settings"
        await edit_lobby(bot, lobby, lobby_text(lobby, await db.get_int("group_quiz_max_players", 8)), group_settings_keyboard(lobby))
    except Exception:
        logger.exception("Group quiz continue failed")


@router.callback_query(F.data.startswith("gqtime:"))
async def group_quiz_set_time(call: CallbackQuery, db: Database, bot: Bot) -> None:
    try:
        _, lobby_id, sec_s = call.data.split(":")
        lobby = lobbies.get(lobby_id)
        if not lobby:
            await call.answer("لابی پیدا نشد", show_alert=False)
            return
        if call.from_user.id != lobby.starter_id:
            await call.answer("فقط سازنده بازی می‌تونه این گزینه رو تغییر بده", show_alert=False)
            return
        await call.answer()
        lobby.timer_seconds = int(sec_s)
        await edit_lobby(bot, lobby, lobby_text(lobby, await db.get_int("group_quiz_max_players", 8)), group_settings_keyboard(lobby))
    except Exception:
        logger.exception("Group quiz set time failed")


@router.callback_query(F.data.startswith("gqcount:"))
async def group_quiz_set_count(call: CallbackQuery, db: Database, bot: Bot) -> None:
    try:
        _, lobby_id, cnt_s = call.data.split(":")
        lobby = lobbies.get(lobby_id)
        if not lobby:
            await call.answer("لابی پیدا نشد", show_alert=False)
            return
        if call.from_user.id != lobby.starter_id:
            await call.answer("فقط سازنده بازی می‌تونه این گزینه رو تغییر بده", show_alert=False)
            return
        await call.answer()
        lobby.question_count = int(cnt_s)
        await edit_lobby(bot, lobby, lobby_text(lobby, await db.get_int("group_quiz_max_players", 8)), group_settings_keyboard(lobby))
    except Exception:
        logger.exception("Group quiz set count failed")


@router.callback_query(F.data.startswith("gqgenrei:"))
async def inline_group_quiz_genre_select(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        _, token, idx_s = call.data.split(":", 2)
        lobby = await ensure_inline_group_lobby(token, call.inline_message_id)
        data = inline_group_pending.get(token)
        if not lobby or not data:
            await call.answer("لابی پیدا نشد، دوباره بازی رو بساز", show_alert=False)
            return
        if call.from_user.id != lobby.starter_id:
            await call.answer("فقط سازنده ژانرهای بازی رو انتخاب می‌کنه", show_alert=False)
            return
        try:
            genre = data["genres"][int(idx_s)]
        except Exception:
            await call.answer("ژانر نامعتبره", show_alert=False)
            return
        if genre in data["selected"]:
            data["selected"].remove(genre)
        elif len(data["selected"]) < 2:
            data["selected"].append(genre)
        else:
            await call.answer("دقیقاً ۲ ژانر انتخاب کن", show_alert=False)
            return
        lobby.selected_genres = list(data["selected"])
        await edit_lobby(bot, lobby, inline_group_genre_text(data), inline_group_genre_keyboard(token))
    except Exception:
        logger.exception("Inline group genre select failed")


@router.callback_query(F.data.startswith("gqcontinuei:"))
async def inline_group_quiz_continue_settings(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    try:
        token = call.data.split(":", 1)[1]
        lobby = await ensure_inline_group_lobby(token, call.inline_message_id)
        data = inline_group_pending.get(token)
        if not lobby or not data:
            await call.answer("لابی پیدا نشد", show_alert=False)
            return
        if call.from_user.id != lobby.starter_id:
            await call.answer("فقط سازنده می‌تونه ادامه بده", show_alert=False)
            return
        if len(data.get("selected", [])) != 2:
            await call.answer("اول ۲ ژانر انتخاب کن", show_alert=False)
            return
        lobby.selected_genres = list(data["selected"])
        lobby.stage = "settings"
        await edit_lobby(bot, lobby, lobby_text(lobby, await db.get_int("group_quiz_max_players", 8)), group_settings_keyboard(lobby))
    except Exception:
        logger.exception("Inline group continue failed")


@router.callback_query(F.data == "group_quiz_start")
async def inline_start_game(call: CallbackQuery, db: Database, bot: Bot) -> None:
    inline_id = call.inline_message_id
    if not inline_id:
        await call.answer("بازی پیدا نشد", show_alert=False)
        return
    lobby = next((l for l in lobbies.values() if l.inline_message_id == inline_id), None)
    if not lobby:
        await call.answer("بازی پیدا نشد، Inline Feedback رو تو BotFather روی ۱۰۰٪ بذار و دوباره امتحان کن", show_alert=False)
        return
    if call.from_user.id != lobby.starter_id:
        await call.answer("فقط کسی که بازی رو شروع کرده می‌تونه از این دکمه استفاده کنه", show_alert=False)
        return
    if not await require_registered_and_join(call, db, bot):
        return
    if lobby.stage != "settings":
        await call.answer("اول ژانرها و تنظیمات بازی رو کامل کن", show_alert=False)
        return
    if len(lobby.players) < 2:
        await call.answer("حداقل ۲ نفر لازمه", show_alert=False)
        return
    await call.answer()
    await start_lobby_game(call, db, bot, lobby)


@router.callback_query(F.data.startswith("gquiz:join:"))
async def group_join(call: CallbackQuery, db: Database, bot: Bot) -> None:
    lobby_id = call.data.split(":", 2)[2]
    lobby = lobbies.get(lobby_id)
    if lobby:
        if not await ensure_user_started_callback(call, db, lobby.starter_id):
            return
        await join_lobby(call, db, bot, lobby)


async def join_lobby(call: CallbackQuery, db: Database, bot: Bot, lobby: GroupLobby) -> None:
    if not await require_registered_and_join(call, db, bot):
        return
    await call.answer()
    max_players = await db.get_int("group_quiz_max_players", 8)
    if len(lobby.players) >= max_players and call.from_user.id not in lobby.players:
        await call.answer("ظرفیت تکمیله", show_alert=True)
        return
    lobby.players[call.from_user.id] = call.from_user.full_name
    lobby.usernames[call.from_user.id] = call.from_user.username
    keyboard = group_settings_keyboard(lobby) if lobby.stage == "settings" else group_genre_keyboard(lobby)
    await edit_lobby(bot, lobby, lobby_text(lobby, max_players), keyboard)


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
    if lobby.stage != "settings":
        await call.answer("اول ژانرها و تنظیمات بازی رو کامل کن", show_alert=False)
        return
    if len(lobby.players) < 2:
        await call.answer("حداقل ۲ نفر لازمه", show_alert=False)
        return
    await call.answer()
    await start_lobby_game(call, db, bot, lobby)


async def start_lobby_game(call: CallbackQuery, db: Database, bot: Bot, lobby: GroupLobby) -> None:
    try:
        lobby.started = True
        if lobby.timeout_task and not lobby.timeout_task.done():
            lobby.timeout_task.cancel()
        await edit_lobby(bot, lobby, "⏳ بازی داره شروع میشه", None)
        count = lobby.question_count
        if lobby.selected_genres:
            placeholders = ','.join('?' for _ in lobby.selected_genres)
            rows = await db.fetchall(f"SELECT * FROM questions WHERE status='active' AND genre IN ({placeholders}) ORDER BY RANDOM() LIMIT ?", (*lobby.selected_genres, count))
        else:
            rows = await db.fetchall("SELECT * FROM questions WHERE status='active' ORDER BY RANDOM() LIMIT ?", (count,))
        if not rows:
            await edit_lobby(bot, lobby, "سوال فعالی برای بازی گروهی نیست", None)
            return
        game = GroupGame(lobby=lobby, questions=rows, scores={uid: 0 for uid in lobby.players})
        games[lobby.lobby_id] = game
        await send_group_question(bot, db, game, 0)
    except Exception as e:
        logger.exception("Group quiz start error: %s", e)
        await edit_lobby(bot, lobby, "❌ خطا در شروع بازی، دوباره امتحان کن", None)


async def send_group_question(bot: Bot, db: Database, game: GroupGame, idx: int) -> None:
    if idx >= len(game.questions):
        await finish_group_game(bot, db, game)
        return
    q = game.questions[idx]
    game.current_idx = idx
    game.question_ended = False
    game.question_lock = asyncio.Lock()
    game.answered[idx] = {}
    total_seconds = game.lobby.timer_seconds
    game.remaining[idx] = total_seconds
    text = group_question_text(game, idx, total_seconds, total_seconds)
    auto_cost = await db.get_int("group_auto_answer_cost", 10)
    await edit_lobby(bot, game.lobby, text, answer_keyboard(game.lobby.lobby_id, idx, q, auto_cost))
    old_task = game.timer_tasks.get(idx)
    if old_task and not old_task.done():
        old_task.cancel()
    game.timer_tasks[idx] = asyncio.create_task(group_question_timeout(bot, db, game, idx, total_seconds))


@router.callback_query(F.data.startswith("gquiz:ans:"))
async def group_answer(call: CallbackQuery, db: Database, bot: Bot) -> None:
    _, _, lobby_id, idx_s, opt_s = call.data.split(":")
    game = games.get(lobby_id)
    if not game:
        return
    idx, opt = int(idx_s), int(opt_s)
    if idx in game.resolved:
        await call.answer("این سوال تموم شده", show_alert=False)
        return
    if call.from_user.id not in game.lobby.players:
        await call.answer("تو عضو این بازی نیستی", show_alert=True)
        return
    if call.from_user.id in game.answered.setdefault(idx, {}):
        await call.answer("قبلاً جواب دادی", show_alert=False)
        return
    game.answered[idx][call.from_user.id] = opt
    total = len(game.lobby.players)
    q = game.questions[idx]
    result_text = "✅ درست جواب دادی" if opt == int(q['correct_option']) else "❌ اشتباه جواب دادی"
    await call.answer(result_text, show_alert=False)
    if len(game.answered[idx]) >= total:
        task = game.timer_tasks.get(idx)
        if task and not task.done():
            task.cancel()
        await resolve_group_question(bot, db, game, idx)


@router.callback_query(F.data.startswith("gquiz:auto:"))
async def group_auto_answer(call: CallbackQuery, db: Database, bot: Bot) -> None:
    _, _, lobby_id, idx_s = call.data.split(":")
    game = games.get(lobby_id)
    if not game:
        await call.answer()
        return
    idx = int(idx_s)
    if idx in game.resolved:
        await call.answer("این سوال تموم شده", show_alert=False)
        return
    if call.from_user.id not in game.lobby.players:
        await call.answer("تو عضو این بازی نیستی", show_alert=True)
        return
    if call.from_user.id in game.answered.setdefault(idx, {}):
        await call.answer("قبلاً جواب دادی", show_alert=False)
        return
    max_uses = await db.get_int("group_auto_answer_max_uses", 3)
    uses = game.auto_answer_uses.get(call.from_user.id, 0)
    if uses >= max_uses:
        await call.answer(f"سقف استفاده از جواب خودکار ({max_uses} بار) پر شده", show_alert=True)
        return
    cost = await db.get_int("group_auto_answer_cost", 10)
    user = await db.get_user(call.from_user.id)
    if not user or user["coins"] < cost:
        await call.answer(f"برای جواب خودکار {cost} سکه لازم داری", show_alert=True)
        return
    await db.change_coins(call.from_user.id, -cost, "group_auto_answer")
    game.auto_answer_uses[call.from_user.id] = uses + 1
    q = game.questions[idx]
    correct_option = int(q["correct_option"])
    game.answered[idx][call.from_user.id] = correct_option
    await call.answer("✅ جواب درست ثبت شد", show_alert=False)
    total = len(game.lobby.players)
    if len(game.answered[idx]) >= total:
        task = game.timer_tasks.get(idx)
        if task and not task.done():
            task.cancel()
        await resolve_group_question(bot, db, game, idx)


async def group_question_timeout(bot: Bot, db: Database, game: GroupGame, idx: int, seconds: int) -> None:
    try:
        await asyncio.sleep(seconds)
        if idx not in game.resolved and game.current_idx == idx and not game.game_ended:
            await asyncio.shield(resolve_group_question(bot, db, game, idx))
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Group question timeout failed")
        try:
            if idx not in game.resolved and game.current_idx == idx and not game.game_ended:
                await resolve_group_question(bot, db, game, idx)
        except Exception:
            logger.exception("Fallback resolve after timer failure failed")


async def resolve_group_question(bot: Bot, db: Database, game: GroupGame, idx: int) -> None:
    async with game.question_lock:
        if game.question_ended or idx in game.resolved or game.current_idx != idx or game.game_ended:
            return
        game.question_ended = True
        game.resolved.add(idx)
        task = game.timer_tasks.get(idx)
        current = asyncio.current_task()
        if task and task is not current and not task.done():
            task.cancel()
        try:
            q = game.questions[idx]
            correct = int(q['correct_option'])
            for uid, name in game.lobby.players.items():
                ok = game.answered.get(idx, {}).get(uid) == correct
                if ok:
                    game.scores[uid] = game.scores.get(uid, 0) + 1
            game.remaining[idx] = 0
            text = group_question_text(game, idx, 0, game.lobby.timer_seconds, resolved=True)
            await edit_lobby(bot, game.lobby, text, None)
            await asyncio.sleep(2)
            if idx + 1 < len(game.questions):
                await send_group_question(bot, db, game, idx + 1)
            else:
                await finish_group_game(bot, db, game)
        except Exception:
            logger.exception("Resolve group question failed")
            try:
                await edit_lobby(bot, game.lobby, "❌ خطا در پایان سوال، بازی متوقف شد", None)
            except Exception:
                logger.exception("Could not show group question error")


async def notify_levelup_in_group(bot: Bot, chat_id: int, username: str, old_level: int, new_level: int, new_title: str) -> None:
    frames = [
        "⬆️ ...",
        "⬆️⬆️ ...",
        "⬆️⬆️⬆️ ...",
        f"🎉 تبریک {username}\nرسیدی به لول {new_level}\n{new_title}",
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
        await db.bump_quest_progress(uid, "play_group_games", 1, bot=bot)
        if pos == 1 and score > 0:
            await db.bump_quest_progress(uid, "group_first_place", 1, bot=bot)
    text = "🏆 نتیجه بازی\n\n" + "\n".join(lines) + "\n\nبرای گزارش مشکل دکمه گزارش رو بزن"
    completed_group_games[game.lobby.lobby_id] = game
    await edit_lobby(bot, game.lobby, text, group_finished_keyboard(game.lobby.lobby_id, "gqreport"))
    for task in game.timer_tasks.values():
        if task and not task.done():
            task.cancel()
    await db.log_group_game('quiz', game.lobby.chat_id, game.lobby.inline_message_id, len(game.lobby.players), len(game.questions))
    await db.activate_referrals_for_players(list(game.lobby.players.keys()))
    if game.lobby.chat_id:
        for _, mention, old_level, new_level, title_text in levelups:
            await notify_levelup_in_group(bot, game.lobby.chat_id, mention, old_level, new_level, title_text)
            await asyncio.sleep(1)
    games.pop(game.lobby.lobby_id, None)
    lobbies.pop(game.lobby.lobby_id, None)


@router.callback_query(F.data == "group_duel_accept")
async def group_duel_accept(call: CallbackQuery, bot: Bot, db: Database) -> None:
    try:
        inline_id = call.inline_message_id
        if not inline_id:
            await call.answer("این دکمه فقط برای دوئل اینلاینه", show_alert=False)
            return
        lobby = next((l for l in lobbies.values() if l.inline_message_id == inline_id and l.lobby_id.startswith("gduel_")), None)
        if not lobby:
            lobby_id = f"gduel_{abs(hash(inline_id))}"
            lobby = GroupLobby(lobby_id=lobby_id, starter_id=call.from_user.id, inline_message_id=inline_id)
            lobby.players[call.from_user.id] = call.from_user.full_name
            lobby.usernames[call.from_user.id] = call.from_user.username
            lobbies[lobby_id] = lobby
        if not await ensure_user_started_callback(call, db, lobby.starter_id):
            return
        if not await require_registered_and_join(call, db, bot):
            return
        await call.answer()
        if call.from_user.id == lobby.starter_id:
            await call.answer("خودت نمی‌تونی حریف خودت بشی", show_alert=False)
            return
        if len(lobby.players) >= 2:
            await call.answer("این دوئل حریف داره", show_alert=False)
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
            await call.answer("دوئل پیدا نشد یا تو عضوش نیستی", show_alert=False)
            return
        try:
            genre = offers[int(idx_s)]
        except Exception:
            await call.answer("ژانر نامعتبره", show_alert=False)
            return
        current = group_duel_genres.setdefault(lobby_id, {})
        taken_by = next((uid for uid, g in current.items() if g == genre and uid != call.from_user.id), None)
        if taken_by:
            await call.answer("این ژانر رو حریف قبلاً انتخاب کرده", show_alert=False)
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


def duel_answer_keyboard(lobby_id: str, q_index: int, q, auto_cost: int = 10) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, label in enumerate(["الف", "ب", "ج", "د"], 1):
        b.button(text=f"{label}) {q[f'option{i}']}", callback_data=f"gduelans:{lobby_id}:{q_index}:{i}")
    b.button(text=f"🎯 جواب خودکار — {auto_cost}🪙", callback_data=f"gduelauto:{lobby_id}:{q_index}")
    b.adjust(2, 2, 1)
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
        return f"⚔️ دوئل | سوال {idx+1}/{len(game.questions)}\n\n{q['text']}\n✅ جواب درست {opts[int(q['correct_option'])-1]}\n\n" + "\n".join(lines)
    return f"⚔️ دوئل | سوال {idx+1}/{len(game.questions)}\n\n{q['text']}"


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
    auto_cost = await db.get_int("group_auto_answer_cost", 10)
    await edit_lobby(bot, game.lobby, group_duel_question_text(game, idx, total_seconds, total_seconds), duel_answer_keyboard(game.lobby.lobby_id, idx, game.questions[idx], auto_cost))
    asyncio.create_task(group_duel_timeout(bot, db, game, idx, total_seconds))


@router.callback_query(F.data.startswith("gduelans:"))
async def group_duel_answer(call: CallbackQuery, db: Database, bot: Bot) -> None:
    _, lobby_id, idx_s, opt_s = call.data.split(":")
    game = group_duels.get(lobby_id)
    if not game:
        await call.answer()
        return
    idx, opt = int(idx_s), int(opt_s)
    if call.from_user.id not in game.lobby.players:
        await call.answer("تو عضو این دوئل نیستی", show_alert=True)
        return
    if call.from_user.id in game.answered.setdefault(idx, {}):
        await call.answer("قبلاً جواب دادی", show_alert=False)
        return
    game.answered[idx][call.from_user.id] = opt
    q = game.questions[idx]
    if opt == int(q['correct_option']):
        await call.answer("✅ جواب درست بود", show_alert=False)
    else:
        await call.answer("❌ جواب غلط بود", show_alert=False)
    if len(game.answered[idx]) >= len(game.lobby.players):
        await resolve_group_duel_question(bot, db, game, idx)


@router.callback_query(F.data.startswith("gduelauto:"))
async def group_duel_auto_answer(call: CallbackQuery, db: Database, bot: Bot) -> None:
    _, lobby_id, idx_s = call.data.split(":")
    game = group_duels.get(lobby_id)
    if not game:
        await call.answer()
        return
    idx = int(idx_s)
    if call.from_user.id not in game.lobby.players:
        await call.answer("تو عضو این دوئل نیستی", show_alert=True)
        return
    if call.from_user.id in game.answered.setdefault(idx, {}):
        await call.answer("قبلاً جواب دادی", show_alert=False)
        return
    max_uses = await db.get_int("group_auto_answer_max_uses", 3)
    uses = game.auto_answer_uses.get(call.from_user.id, 0)
    if uses >= max_uses:
        await call.answer(f"سقف استفاده از جواب خودکار ({max_uses} بار) پر شده", show_alert=True)
        return
    cost = await db.get_int("group_auto_answer_cost", 10)
    user = await db.get_user(call.from_user.id)
    if not user or user["coins"] < cost:
        await call.answer(f"برای جواب خودکار {cost} سکه لازم داری", show_alert=True)
        return
    await db.change_coins(call.from_user.id, -cost, "group_auto_answer")
    game.auto_answer_uses[call.from_user.id] = uses + 1
    q = game.questions[idx]
    correct_option = int(q["correct_option"])
    game.answered[idx][call.from_user.id] = correct_option
    await call.answer("✅ جواب درست ثبت شد", show_alert=False)
    if len(game.answered[idx]) >= len(game.lobby.players):
        await resolve_group_duel_question(bot, db, game, idx)


async def group_duel_timeout(bot: Bot, db: Database, game: GroupDuelGame, idx: int, seconds: int) -> None:
    try:
        await asyncio.sleep(seconds)
        if idx not in game.resolved and game.current_idx == idx:
            await resolve_group_duel_question(bot, db, game, idx)
    except asyncio.CancelledError:
        return
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
            await bot.send_message(uid, f"🎁 جوایز دوئل\nدرست {score}\nایکس‌پی +{xp_amount}\nسکه و جام تو دوئل گروهی داده نمیشه")
        except Exception:
            logger.exception("Could not send group duel reward PM")
    text = f"⚔️ نتیجه دوئل\n\n🏆 {trim_name(winner_name)} برد\n{s1} درست vs {s2} درست\n\n🎁 جوایز تو پی‌وی فرستاده شد\n\nبرای گزارش مشکل دکمه گزارش رو بزن"
    completed_group_duels[game.lobby.lobby_id] = game
    await edit_lobby(bot, game.lobby, text, group_finished_keyboard(game.lobby.lobby_id, "gdreport"))
    await db.log_group_game('duel', game.lobby.chat_id, game.lobby.inline_message_id, len(game.lobby.players), len(game.questions))
    await db.activate_referrals_for_players(list(game.lobby.players.keys()))
    group_duels.pop(game.lobby.lobby_id, None)
    lobbies.pop(game.lobby.lobby_id, None)
    group_duel_genres.pop(game.lobby.lobby_id, None)
    group_duel_offers.pop(game.lobby.lobby_id, None)


async def find_inline_duel_lobby(inline_id: str | None) -> GroupLobby | None:
    if not inline_id:
        return None
    return next((l for l in lobbies.values() if l.inline_message_id == inline_id and l.lobby_id.startswith("gduel_")), None)


@router.callback_query(F.data == "group_duel_leave")
async def group_duel_leave(call: CallbackQuery, bot: Bot) -> None:
    await call.answer()
    try:
        lobby = await find_inline_duel_lobby(call.inline_message_id)
        if not lobby:
            await call.answer("دوئل پیدا نشد", show_alert=False)
            return
        if call.from_user.id not in lobby.players:
            await call.answer("تو داخل این دوئل نیستی", show_alert=False)
            return
        if call.from_user.id == lobby.starter_id:
            await edit_lobby(bot, lobby, "❌ بازی به دلیل خروج سازنده بسته شد", group_replay_keyboard())
            lobbies.pop(lobby.lobby_id, None)
            group_duel_genres.pop(lobby.lobby_id, None)
            group_duel_offers.pop(lobby.lobby_id, None)
            group_duels.pop(lobby.lobby_id, None)
            return
        lobby.players.pop(call.from_user.id, None)
        lobby.usernames.pop(call.from_user.id, None)
        group_duel_genres.get(lobby.lobby_id, {}).pop(call.from_user.id, None)
        names = list(lobby.players.values())
        await edit_lobby(
            bot,
            lobby,
            f"⚔️ دوئل چالشینو\n\n👤 چالش‌دهنده: {trim_name(names[0]) if names else 'نامشخص'}\n\nمنتظر حریف...",
            group_duel_lobby_keyboard(),
        )
    except Exception:
        logger.exception("Group duel leave failed")


@router.callback_query(F.data == "group_duel_close")
async def group_duel_close(call: CallbackQuery, bot: Bot) -> None:
    # Backward compatibility for old inline messages created before removing this button.
    await call.answer("این دکمه دیگه فعال نیست، برای بستن دوئل سازنده باید خروج از دوئل رو بزنه", show_alert=False)


def _report_game_by_prefix(prefix: str, game_id: str):
    return completed_group_duels.get(game_id) if prefix == "gdreport" else completed_group_games.get(game_id)


@router.callback_query(F.data.startswith(("gqreport:menu:", "gdreport:menu:")))
async def group_report_menu(call: CallbackQuery, bot: Bot) -> None:
    try:
        prefix, _, game_id = call.data.split(":", 2)
        game = _report_game_by_prefix(prefix, game_id)
        if not game:
            await call.answer("گزارش برای این بازی پیدا نشد", show_alert=True)
            return
        lines = ["📋 سوالات و جواب‌های این بازی"]
        for i, q in enumerate(game.questions, 1):
            opts = [q['option1'], q['option2'], q['option3'], q['option4']]
            correct_idx = int(q['correct_option'])
            correct_text = opts[correct_idx - 1]
            selected = game.answered.get(i - 1, {}).get(call.from_user.id)
            if selected:
                selected_text = opts[int(selected) - 1]
                mark = "✅" if int(selected) == correct_idx else "❌"
                answer_line = f"✅ جواب صحیح {correct_text}\n{mark} پاسخ تو {selected_text}"
            else:
                answer_line = f"✅ جواب صحیح {correct_text}\n⏱ پاسخ تو بدون پاسخ"
            lines.append(f"\n{i}. {q['text']}\n{answer_line}")
        lines.append("\nبرای گزارش مشکل شماره سوال رو از دکمه‌های زیر انتخاب کن")
        await bot.send_message(
            call.from_user.id,
            "\n".join(lines),
            reply_markup=group_report_questions_keyboard(game_id, len(game.questions), prefix),
        )
        await call.answer("گزارش رو تو پی‌وی واست فرستادم", show_alert=True)
    except Exception:
        logger.exception("Group report menu failed")
        try:
            await call.answer("برای دریافت گزارش اول ربات رو تو پی‌وی استارت کن", show_alert=True, url="https://t.me/ChalleshinoBot?start=from_report")
        except Exception:
            pass


@router.callback_query(F.data.startswith(("gqreport:q:", "gdreport:q:")))
async def group_report_question(call: CallbackQuery, db: Database, bot: Bot, reports_channel_id: int | None) -> None:
    try:
        prefix, _, game_id, idx_s = call.data.split(":", 3)
        game = _report_game_by_prefix(prefix, game_id)
        if not game:
            await call.answer("گزارش برای این بازی پیدا نشد", show_alert=True)
            return
        idx = int(idx_s)
        if idx < 0 or idx >= len(game.questions):
            await call.answer("شماره سوال نامعتبره", show_alert=True)
            return
        q = game.questions[idx]
        report_id = await db.add_report(q['id'], call.from_user.id, None, f"گزارش از بازی گروهی/دوئل - سوال {idx+1}")
        if reports_channel_id:
            opts = [q['option1'], q['option2'], q['option3'], q['option4']]
            correct = opts[int(q['correct_option']) - 1]
            await bot.send_message(
                reports_channel_id,
                f"⚠️ گزارش سوال از بازی گروهی\n"
                f"❓ سوال #{q['id']}: {q['text']}\n"
                f"✅ جواب درست: {correct}\n"
                f"👤 گزارش‌دهنده: {call.from_user.full_name} | ID: <code>{call.from_user.id}</code>\n"
                f"📋 شماره سوال در بازی: {idx+1}\n"
                f"📅 {jalali_datetime(now_iso())}",
                reply_markup=report_admin_keyboard(q['id'], report_id),
            )
        await call.answer("✅ گزارش سوال ثبت شد", show_alert=True)
    except Exception:
        logger.exception("Group report question failed")
        try:
            await call.answer("خطا در ثبت گزارش", show_alert=True)
        except Exception:
            pass


@router.callback_query(F.data.startswith(("gqreport:cancel:", "gdreport:cancel:")))
async def group_report_cancel(call: CallbackQuery) -> None:
    await call.answer()
    try:
        await call.message.edit_text("گزارش شما لغو شد")
    except Exception:
        logger.exception("Group report cancel edit failed")
