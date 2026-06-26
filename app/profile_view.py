from __future__ import annotations

from app.db import Database
from app.utils import xp_progress_text, league_with_emoji, rank_with_emoji
from app.time_utils import jalali_date, jalali_datetime


def xp_bar(current_xp: int, required_xp: int) -> str:
    filled = max(0, min(10, int((current_xp / max(1, required_xp)) * 10)))
    return "▰" * filled + "▱" * (10 - filled)


async def build_profile_text(db: Database, telegram_id: int) -> str:
    u = await db.get_user(telegram_id)
    if not u:
        return "پروفایل پیدا نشد."

    title = await db.user_title(telegram_id)
    if title:
        title_text = f"{title['emoji'] or ''} {title['name']}".strip()
    else:
        title_text = rank_with_emoji(await db.get_rank_title(u['level']))

    league = await db.get_user_league(u['cups'])
    league_name = league_with_emoji(league['name'] if league else 'بدون لیگ')
    cur, nxt = await db.level_bounds(u['level'])
    current_xp = max(0, int(u['xp']) - int(cur))
    required_xp = max(1, int(nxt) - int(cur))
    username = f"@{u['username']}" if u['username'] else ""
    total_duels = int(u['wins']) + int(u['losses']) + int(u['draws'])
    wrong = max(0, int(u['total_answers']) - int(u['correct_answers']))

    level_pos = await db.leaderboard_user_position(telegram_id, "level", "all")
    league_pos = await db.leaderboard_user_position(telegram_id, "league", "all")
    positions = ""
    if level_pos or league_pos:
        positions = "\n" + " | ".join(
            part for part in [
                f"📍 رتبه سطح: #{level_pos['rank']}" if level_pos else "",
                f"🏆 رتبه لیگ: #{league_pos['rank']}" if league_pos else "",
            ] if part
        )

    analysis = await db.user_strengths_weaknesses(telegram_id)
    genre_analysis = ""
    if analysis['strengths']:
        strengths = "\n".join(
            f"🥇 {r['genre']} — {int(r['pct'])}%" if i == 0 else f"🥈 {r['genre']} — {int(r['pct'])}%"
            for i, r in enumerate(analysis['strengths'])
        )
        weaknesses = "\n".join(f"📉 {r['genre']} — {int(r['pct'])}%" for r in analysis['weaknesses'])
        genre_analysis = f"\n\n💪 نقاط قوت:\n{strengths}"
        if weaknesses:
            genre_analysis += f"\n\n⚠️ نقاط ضعف:\n{weaknesses}"

    return (
        f"👤 <b>{u['first_name'] or 'کاربر'}</b> {username}\n"
        f"{title_text} | لول {u['level']}\n"
        f"ایکس‌پی {current_xp}/{required_xp} {xp_bar(current_xp, required_xp)}\n"
        f"🏆 {league_name} — {u['cups']} جام\n"
        f"🪙 سکه: {u['coins']}"
        f"{positions}\n\n"
        f"⚔️ دوئل‌ها: {total_duels} | برد {u['wins']} / مساوی {u['draws']} / شکست {u['losses']}\n"
        f"✅ پاسخ صحیح: {u['correct_answers']} | ❌ پاسخ غلط: {wrong}"
        f"{genre_analysis}"
    )
