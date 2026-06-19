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
    started: bool = False


@dataclass
class GroupGame:
    lobby: GroupLobby
    questions: list
    scores: dict[int, int] = field(default_factory=dict)
    answered: dict[int, dict[int, int]] = field(default_factory=dict)  # q_index -> user_id -> option


lobbies: dict[str, GroupLobby] = {}
games: dict[str, GroupGame] = {}


def trim_name(name: str, max_len: int = 20) -> str:
    return name if len(name) <= max_len else name[:max_len] + "..."


def lobby_keyboard(lobby_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✋ پایه‌ام!", callback_data=f"gquiz:join:{lobby_id}")
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
        "🎮 بازی کوییز گروهی!\n\n"
        f"👤 {trim_name(lobby.players.get(lobby.starter_id, 'شروع‌کننده'))} یه بازی شروع کرده\n"
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
async def group_quiz_start(message: Message, db: Database) -> None:
    if message.chat.type == "private":
        await message.answer("این دستور برای گروه‌هاست.")
        return
    key = f"chat:{message.chat.id}"
    if any(l.chat_id == message.chat.id and not l.started for l in lobbies.values()):
        await message.answer("در این گروه یک لابی فعال وجود دارد.")
        return
    lobby_id = f"chat_{abs(message.chat.id)}_{message.message_id}"
    lobby = GroupLobby(lobby_id=lobby_id, starter_id=message.from_user.id, chat_id=message.chat.id)
    lobby.players[message.from_user.id] = message.from_user.full_name
    lobbies[lobby_id] = lobby
    max_players = await db.get_int("group_quiz_max_players", 8)
    msg = await message.answer(lobby_text(lobby, max_players), reply_markup=lobby_keyboard(lobby_id))
    lobby.message_id = msg.message_id


@router.inline_query()
async def inline_handler(query: InlineQuery) -> None:
    text = (query.query or "").lower()
    if "بازی" not in text and "quiz" not in text:
        await query.answer([], cache_time=1)
        return
    result = InlineQueryResultArticle(
        id="group_quiz",
        title="🎮 شروع بازی گروهی",
        description="یه بازی کوییز گروهی توی این چت شروع کن!",
        input_message_content=InputTextMessageContent(message_text="🎮 یه بازی گروهی شروع شد!\nبرای شرکت دکمه رو بزن 👇"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ پایه‌ام!", callback_data="group_quiz_join_inline")]]),
    )
    await query.answer([result], cache_time=1)


@router.chosen_inline_result()
async def chosen_result_handler(chosen: ChosenInlineResult) -> None:
    if chosen.result_id != "group_quiz" or not chosen.inline_message_id:
        return
    lobby_id = f"inline_{abs(hash(chosen.inline_message_id))}"
    lobby = GroupLobby(lobby_id=lobby_id, starter_id=chosen.from_user.id, inline_message_id=chosen.inline_message_id)
    lobby.players[chosen.from_user.id] = chosen.from_user.full_name
    lobbies[lobby_id] = lobby


@router.callback_query(F.data == "group_quiz_join_inline")
async def inline_join_redirect(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    inline_id = call.inline_message_id
    if not inline_id:
        return
    lobby = next((l for l in lobbies.values() if l.inline_message_id == inline_id), None)
    if not lobby:
        lobby_id = f"inline_{abs(hash(inline_id))}"
        lobby = GroupLobby(lobby_id=lobby_id, starter_id=call.from_user.id, inline_message_id=inline_id)
        lobbies[lobby_id] = lobby
    await join_lobby(call, db, bot, lobby)


@router.callback_query(F.data.startswith("gquiz:join:"))
async def group_join(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    lobby_id = call.data.split(":", 2)[2]
    lobby = lobbies.get(lobby_id)
    if lobby:
        await join_lobby(call, db, bot, lobby)


async def join_lobby(call: CallbackQuery, db: Database, bot: Bot, lobby: GroupLobby) -> None:
    max_players = await db.get_int("group_quiz_max_players", 8)
    if len(lobby.players) >= max_players and call.from_user.id not in lobby.players:
        await call.answer("ظرفیت تکمیل است.", show_alert=True)
        return
    lobby.players[call.from_user.id] = call.from_user.full_name
    await edit_lobby(bot, lobby, lobby_text(lobby, max_players), lobby_keyboard(lobby.lobby_id))


@router.callback_query(F.data.startswith("gquiz:start:"))
async def group_start_game(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    lobby_id = call.data.split(":", 2)[2]
    lobby = lobbies.get(lobby_id)
    if not lobby:
        return
    if call.from_user.id != lobby.starter_id:
        await call.answer("فقط شروع‌کننده می‌تواند بازی را شروع کند.", show_alert=True)
        return
    if len(lobby.players) < 2:
        await call.answer("حداقل 2 نفر لازم است.", show_alert=True)
        return
    lobby.started = True
    await edit_lobby(bot, lobby, "⏳ بازی در حال شروع...", None)
    count = await db.get_int("group_quiz_question_count", 5)
    rows = await db.fetchall("SELECT * FROM questions WHERE status='active' ORDER BY RANDOM() LIMIT ?", (count,))
    if not rows:
        await edit_lobby(bot, lobby, "سوال فعالی برای بازی گروهی وجود ندارد.", None)
        return
    game = GroupGame(lobby=lobby, questions=rows, scores={uid: 0 for uid in lobby.players})
    games[lobby_id] = game
    await send_group_question(bot, db, game, 0)


async def send_group_question(bot: Bot, db: Database, game: GroupGame, idx: int) -> None:
    if idx >= len(game.questions):
        await finish_group_game(bot, db, game)
        return
    q = game.questions[idx]
    game.answered[idx] = {}
    total = len(game.players)
    text = f"❓ سوال {idx+1} از {len(game.questions)}\n━━━━━━━━━━━━━━\n{q['text']}\n━━━━━━━━━━━━━━\n⏱ ▰▰▰▰▰▰▰▰▰▰ {await db.get_int('group_quiz_timer_seconds', 30)}s\n\n✅ 0/{total} نفر جواب دادن"
    if game.lobby.inline_message_id:
        await bot.edit_message_text(text, inline_message_id=game.lobby.inline_message_id, reply_markup=answer_keyboard(game.lobby.lobby_id, idx, q))
    else:
        await bot.send_message(game.lobby.chat_id, text, reply_markup=answer_keyboard(game.lobby.lobby_id, idx, q))
    asyncio.create_task(group_question_timeout(bot, db, game, idx, await db.get_int('group_quiz_timer_seconds', 30)))


@router.callback_query(F.data.startswith("gquiz:ans:"))
async def group_answer(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    _, _, lobby_id, idx_s, opt_s = call.data.split(":")
    game = games.get(lobby_id)
    if not game:
        return
    idx, opt = int(idx_s), int(opt_s)
    if call.from_user.id not in game.lobby.players:
        await call.answer("شما عضو این بازی نیستید.", show_alert=True)
        return
    if call.from_user.id in game.answered.setdefault(idx, {}):
        await call.answer("قبلاً پاسخ دادی", show_alert=False)
        return
    game.answered[idx][call.from_user.id] = opt
    total = len(game.lobby.players)
    q = game.questions[idx]
    text = f"❓ سوال {idx+1} از {len(game.questions)}\n━━━━━━━━━━━━━━\n{q['text']}\n━━━━━━━━━━━━━━\n✅ {len(game.answered[idx])}/{total} نفر جواب دادن"
    await edit_lobby(bot, game.lobby, text, answer_keyboard(lobby_id, idx, q))
    if len(game.answered[idx]) >= total:
        await resolve_group_question(bot, db, game, idx)


async def group_question_timeout(bot: Bot, db: Database, game: GroupGame, idx: int, seconds: int) -> None:
    await asyncio.sleep(seconds)
    if idx in game.answered and len(game.answered[idx]) < len(game.lobby.players):
        await resolve_group_question(bot, db, game, idx)


async def resolve_group_question(bot: Bot, db: Database, game: GroupGame, idx: int) -> None:
    q = game.questions[idx]
    correct = int(q['correct_option'])
    lines = []
    for uid, name in game.lobby.players.items():
        ok = game.answered.get(idx, {}).get(uid) == correct
        if ok:
            game.scores[uid] = game.scores.get(uid, 0) + 1
        mark = "✅" if ok else "❌"
        lines.append(f"{mark} \u200f{trim_name(name)}\n▰⬜⬜⬜⬜")
    opts = [q['option1'], q['option2'], q['option3'], q['option4']]
    text = f"❓ سوال {idx+1} از {len(game.questions)}\n{q['text']}\n━━━━━━━━━━━━━━\n✅ جواب درست: {opts[correct-1]}\n━━━━━━━━━━━━━━\n\n" + "\n\n".join(lines)
    await edit_lobby(bot, game.lobby, text, None)
    await asyncio.sleep(2)
    await send_group_question(bot, db, game, idx + 1)


async def finish_group_game(bot: Bot, db: Database, game: GroupGame) -> None:
    max_score = max(game.scores.values() or [0])
    sorted_players = sorted(game.lobby.players.items(), key=lambda kv: game.scores.get(kv[0], 0), reverse=True)
    lines = []
    for pos, (uid, name) in enumerate(sorted_players, 1):
        score = game.scores.get(uid, 0)
        xp = 20 if score == max_score and score > 0 else score * 5
        if xp:
            await db.change_xp(uid, xp, "group_quiz")
        lines.append(f"{pos}. {trim_name(name)} — {score}/{len(game.questions)} ✅ (+{xp} XP)")
    text = "🏆 نتیجه‌ی بازی\n━━━━━━━━━━━━━━\n" + "\n".join(lines) + "\n━━━━━━━━━━━━━━"
    await edit_lobby(bot, game.lobby, text, None)
    games.pop(game.lobby.lobby_id, None)
    lobbies.pop(game.lobby.lobby_id, None)
