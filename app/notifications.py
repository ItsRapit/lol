from __future__ import annotations

import asyncio
import logging
import random
from aiogram import Bot
from app.db import Database
from app.utils import league_with_emoji

logger = logging.getLogger(__name__)


async def run_edit_animation(bot: Bot, user_id: int, steps: list[str], delay: float = 0.6) -> None:
    if not steps:
        return
    try:
        msg = await bot.send_message(user_id, steps[0])
        for step in steps[1:]:
            await asyncio.sleep(delay)
            try:
                await msg.edit_text(step)
            except Exception:
                logger.debug("Animation edit skipped", exc_info=True)
    except Exception:
        logger.exception("Animation failed")
        try:
            await bot.send_message(user_id, steps[-1])
        except Exception:
            logger.exception("Animation fallback failed")


async def levelup_steps(old_level: int, new_level: int) -> list[str]:
    if random.choice([1, 2]) == 1:
        return [
            "⬆️ ...",
            "⬆️⬆️ ...",
            "⬆️⬆️⬆️ ...",
            f"🎉 لول آپ!\nلول {old_level} ← لول {new_level}",
        ]
    return [
        "💫",
        "💫✨💫",
        "💫✨🌟✨💫",
        f"🚀 ارتقا!\nلول {old_level} ← لول {new_level}",
    ]


async def rankup_steps(old_rank: str, new_rank: str, old_level: int, new_level: int, include_level: bool) -> list[str]:
    level_line = f"\nلول {old_level} ← لول {new_level}" if include_level else ""
    return [
        "🏆 ...",
        "🏆🏆 ...",
        "🏆🏆🏆 ...",
        f"👑 ترفیع!\n{old_rank} ← {new_rank}{level_line}",
    ]


async def title_steps(db: Database, old_title: str, new_title: str, old_rank: str, new_rank: str, old_level: int, new_level: int, level_up: bool, rank_up: bool) -> list[str]:
    level_line = f"لول {old_level} ← لول {new_level}" if level_up else ""
    rank_line = f"{old_rank} ← {new_rank}" if rank_up else ""
    return [
        "⬛⬛⬛⬛⬛⬛⬛\n⬛⬛⬛⬛⬛⬛⬛\n⬛⬛⬛⬛⬛⬛⬛",
        "🟡⬛⬛⬛⬛⬛🟡\n⬛⬛⬛⬛⬛⬛⬛\n🟡⬛⬛⬛⬛⬛🟡",
        "🟡⬛🟡⬛🟡⬛🟡\n⬛⬛⬛⬛⬛⬛⬛\n🟡⬛🟡⬛🟡⬛🟡",
        "✨🟡✨🟡✨🟡✨\n🟡⬛⬛⬛⬛⬛🟡\n✨🟡✨🟡✨🟡✨",
        "🌟✨🌟✨🌟✨🌟\n✨⬛⬛⬛⬛⬛✨\n🌟✨🌟✨🌟✨🌟",
        "💫🌟💫🌟💫🌟💫\n🌟✨⬛⬛⬛✨🌟\n💫🌟💫🌟💫🌟💫",
        "⚡️💫⚡️💫⚡️💫⚡️\n💫🌟💥💥💥🌟💫\n⚡️💫⚡️💫⚡️💫⚡️",
        "🎊🎉🎊🎉🎊🎉🎊\n🎉✨💥💥💥✨🎉\n🎊🎉🎊🎉🎊🎉🎊",
        await db.get_setting("title_anim_step9", "🎊🎉🎊🎉🎊🎉🎊\n🎉🏆 تبریک! 🏆🎉\n🎊🎉🎊🎉🎊🎉🎊"),
        (await db.get_setting("title_anim_step10", "🏅 لقب جدید 🏅\n⚔️ {new_title} ⚔️\n{level_line}\n{rank_line}"))
        .format(new_title=new_title, old_title=old_title, level_line=level_line, rank_line=rank_line),
    ]


async def demotion_steps(old_rank: str, new_rank: str) -> list[str]:
    return [
        "😔 این بار نشد...",
        "😔 این بار نشد...",
        f"📉 سقوط رنک\n{old_rank} ← {new_rank}\nولی هنوز وقت هست 💪",
    ]


async def send_duel_transition_notifications(bot: Bot, db: Database, user_id: int, transition: dict) -> None:
    before = transition.get("before", {})
    after = transition.get("after", {})
    old_level = int(before.get("level", 1))
    new_level = int(after.get("level", old_level))
    level_up = bool(transition.get("level_up"))
    rank_up = bool(transition.get("league_promoted"))
    rank_down = bool(transition.get("league_demoted"))
    new_title = bool(transition.get("new_title"))
    old_rank = league_with_emoji(str(before.get("league_name", "لیگ قبلی")))
    new_rank = league_with_emoji(str(after.get("league_name", "لیگ جدید")))
    if new_title:
        await run_edit_animation(bot, user_id, await title_steps(db, before.get("title_name", "بدون لقب"), after.get("title_name", "لقب جدید"), old_rank, new_rank, old_level, new_level, level_up, rank_up), 0.6)
    elif rank_up:
        await run_edit_animation(bot, user_id, await rankup_steps(old_rank, new_rank, old_level, new_level, level_up), 0.6)
    elif level_up:
        await run_edit_animation(bot, user_id, await levelup_steps(old_level, new_level), 0.6)
    elif rank_down:
        await run_edit_animation(bot, user_id, await demotion_steps(old_rank, new_rank), 0.6)


async def send_streak_notification(bot: Bot, user_id: int, reward: dict | None) -> None:
    if not reward:
        return
    try:
        day = int(reward.get("day", 0))
        coins = int(reward.get("coins", 0))
        balance = int(reward.get("balance", 0))
        await bot.send_message(user_id, f"🎁 کمک روزانه روز {day}\n<b>{coins} سکه</b> به حسابت اضافه شد\nموجودی فعلی <b>{balance} سکه</b>")
    except Exception:
        logger.exception("Daily aid notification failed")
