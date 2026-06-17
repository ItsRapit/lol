from __future__ import annotations
import secrets
from aiogram.types import User
from .db import Database

CANONICAL_GENRES = [
    "فوتبال", "ورزش", "لوگو و سرگرمی", "غذا و نوشیدنی", "تکنولوژی", "تاریخ",
    "جغرافیا", "علم و دانش", "ادبیات", "سینما", "موسیقی", "هنر",
    "طبیعت و جاندار", "معما و هوش", "ادیان", "خودرو و وسایل نقلیه",
    "زبان انگلیسی", "بازی‌های ویدیویی",
]

GENRE_ALIASES = {
    "اطلاعات عمومی": "علم و دانش",
    "🎲 اطلاعات عمومی": "علم و دانش",
    "عمومی": "علم و دانش",
    "دانش": "علم و دانش",
    "علوم": "علم و دانش",
    "طبیعت": "طبیعت و جاندار",
    "حیوانات": "طبیعت و جاندار",
    "جانداران": "طبیعت و جاندار",
    "ماشین": "خودرو و وسایل نقلیه",
    "خودرو": "خودرو و وسایل نقلیه",
    "سرگرمی": "لوگو و سرگرمی",
    "لوگو": "لوگو و سرگرمی",
    "خوراکی": "غذا و نوشیدنی",
    "غذا": "غذا و نوشیدنی",
    "فناوری": "تکنولوژی",
    "مذهبی": "ادیان",
    "هوش": "معما و هوش",
    "معما": "معما و هوش",
    "انگلیسی": "زبان انگلیسی",
    "زبان": "زبان انگلیسی",
    "گیم": "بازی‌های ویدیویی",
    "بازی ویدیویی": "بازی‌های ویدیویی",
    "ویدیوگیم": "بازی‌های ویدیویی",
}


def display_name(user_row) -> str:
    name = user_row['first_name'] or user_row['username'] or str(user_row['telegram_id'])
    return f"@{user_row['username']}" if user_row['username'] else name


def rtl_line(text: str) -> str:
    """Wrap mixed RTL/LTR text for stable right-to-left display in Telegram lists."""
    return "\u202B" + text + "\u202C"


def invite_token() -> str:
    return secrets.token_urlsafe(8)


def options_from_question(q) -> list[str]:
    return [q['option1'], q['option2'], q['option3'], q['option4']]


async def ensure_user(db: Database, user: User, start_payload: str | None = None):
    referrer = None
    if start_payload and start_payload.startswith('ref_'):
        try:
            referrer = int(start_payload.removeprefix('ref_'))
        except ValueError:
            referrer = None
    return await db.upsert_user(user.id, user.username, user.first_name, referrer)


def xp_progress_text(xp: int, current_level_required: int = 0, next_level_required: int = 100) -> str:
    span = max(1, next_level_required - current_level_required)
    got = max(0, xp - current_level_required)
    return f"{got}/{span}"


def to_english_digits(value: object) -> str:
    return str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def league_with_emoji(name: str | None) -> str:
    name = name or "بدون لیگ"
    if "برنزی" in name:
        return f"🥉 {name}"
    if "نقره" in name:
        return f"🥈 {name}"
    if "طلایی" in name:
        return f"🥇 {name}"
    if "الماسی" in name:
        return f"💎 {name}"
    if "اسطوره" in name:
        return f"👑 {name}"
    return f"🏅 {name}"


def rank_with_emoji(title: str | None) -> str:
    title = title or "بدون رتبه"
    mapping = {
        "تازه‌کار": "🌱",
        "دانشجو": "📘",
        "استاد": "🎓",
        "قهرمان": "🏆",
        "اسطوره": "👑",
        "افسانه‌ای": "🔥",
        "مکس لول": "💫",
    }
    return f"{mapping.get(title, '🏅')} {title}"


def normalize_genre(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "علم و دانش"
    if raw in CANONICAL_GENRES:
        return raw
    clean = raw.replace("‌", " ").strip()
    return GENRE_ALIASES.get(clean, clean if clean in CANONICAL_GENRES else "علم و دانش")
