from __future__ import annotations

from app.db import Database
from app.utils import league_with_emoji, rank_with_emoji
from app.time_utils import jalali_date_diff_days


def xp_bar(current_xp: int, required_xp: int) -> str:
    filled = max(0, min(10, int((current_xp / max(1, required_xp)) * 10)))
    return "▰" * filled + "▱" * (10 - filled)


async def build_profile_text(
    db: Database,
    telegram_id: int,
    show_username: bool = True,
    show_xp: bool = True,
    show_coins: bool = True,
) -> str:
    u = await db.get_user(telegram_id)
    if not u:
        return "پروفایل پیدا نشد"

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
    username = f"@{u['username']}" if (show_username and u['username']) else ""
    total_duels = int(u['wins']) + int(u['losses']) + int(u['draws'])
    wrong = max(0, int(u['total_answers']) - int(u['correct_answers']))

    level_pos = await db.leaderboard_user_position(telegram_id, "level", "all")
    league_pos = await db.leaderboard_user_position(telegram_id, "league", "all")
    positions = ""
    if level_pos or league_pos:
        positions = " | ".join(
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

    avg_response = await db.user_avg_response_seconds(telegram_id)
    joined_days = jalali_date_diff_days(u['created_at']) or 0

    lines = [
        f"👤 <b>{u['first_name'] or 'کاربر'}</b> {username}".rstrip(),
        f"{title_text} | لول {u['level']}",
    ]
    if show_xp:
        lines.append(f"ایکس‌پی {current_xp}/{required_xp} {xp_bar(current_xp, required_xp)}")
    lines.append(f"🏆 {league_name} — {u['cups']} جام")
    if show_coins:
        lines.append(f"🪙 سکه {u['coins']}")
    if positions:
        lines.append(positions)
    lines.extend([
        "",
        f"⚔️ دوئل‌ها {total_duels} | برد {u['wins']} / مساوی {u['draws']} / شکست {u['losses']}",
        f"✅ پاسخ صحیح {u['correct_answers']} | ❌ پاسخ غلط {wrong}",
    ])
    if avg_response:
        lines.append(f"⏱ میانگین زمان پاسخ {avg_response} ثانیه")
    lines.append(f"📅 {joined_days} روزه عضو ربات هستی")
    return "\n".join(lines) + genre_analysis
