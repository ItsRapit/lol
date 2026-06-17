from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite
from app.time_utils import tehran_now, tehran_date_key, jalali_week_start_key, jalali_date_diff_days, tehran_days_between

logger = logging.getLogger(__name__)
UTC = timezone.utc
CANONICAL_GENRES = [
    "فوتبال", "ورزش", "لوگو و سرگرمی", "غذا و نوشیدنی", "تکنولوژی", "تاریخ",
    "جغرافیا", "علم و دانش", "ادبیات", "سینما", "موسیقی", "هنر",
    "طبیعت و جاندار", "معما و هوش", "ادیان", "خودرو و وسایل نقلیه",
    "زبان انگلیسی", "بازی‌های ویدیویی",
]
GENRE_ALIASES = {"🎲 اطلاعات عمومی": "علم و دانش", "اطلاعات عمومی": "علم و دانش", "عمومی": "علم و دانش", "فناوری": "تکنولوژی", "طبیعت": "طبیعت و جاندار", "حیوانات": "طبیعت و جاندار", "خودرو": "خودرو و وسایل نقلیه", "ماشین": "خودرو و وسایل نقلیه", "سرگرمی": "لوگو و سرگرمی", "لوگو": "لوگو و سرگرمی", "غذا": "غذا و نوشیدنی", "هوش": "معما و هوش", "معما": "معما و هوش", "مذهبی": "ادیان", "انگلیسی": "زبان انگلیسی", "زبان": "زبان انگلیسی", "گیم": "بازی‌های ویدیویی", "بازی ویدیویی": "بازی‌های ویدیویی", "ویدیوگیم": "بازی‌های ویدیویی"}


def normalize_genre_db(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "علم و دانش"
    if raw in CANONICAL_GENRES:
        return raw
    raw = raw.replace("‌", " ").strip()
    return GENRE_ALIASES.get(raw, raw if raw in CANONICAL_GENRES else "علم و دانش")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """Single database gateway. Handlers must not open SQLite connections directly."""

    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, timeout=30)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=30000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.commit()
        await self.migrate()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def execute_write(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        async with self._write_lock:
            cur = await self.conn.execute(sql, tuple(params))
            await self.conn.commit()
            return cur

    async def executemany_write(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        async with self._write_lock:
            await self.conn.executemany(sql, seq)
            await self.conn.commit()

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        cur = await self.conn.execute(sql, tuple(params))
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, tuple(params))
        return await cur.fetchall()

    async def migrate(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT, first_name TEXT,
                coins INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                correct_answers INTEGER NOT NULL DEFAULT 0,
                total_answers INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                referred_by INTEGER,
                referral_activated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS admins(
                telegram_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'admin',
                added_by INTEGER,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS ranks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                min_level INTEGER NOT NULL,
                title TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS questions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                option1 TEXT NOT NULL, option2 TEXT NOT NULL, option3 TEXT NOT NULL, option4 TEXT NOT NULL,
                correct_option INTEGER NOT NULL CHECK(correct_option BETWEEN 1 AND 4),
                genre TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                submitted_by INTEGER,
                created_at TEXT NOT NULL,
                reviewed_by INTEGER,
                reviewed_at TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_questions_active ON questions(status, genre)",
            """CREATE TABLE IF NOT EXISTS duels(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player1_id INTEGER NOT NULL,
                player2_id INTEGER,
                status TEXT NOT NULL,
                invite_token TEXT UNIQUE,
                current_index INTEGER NOT NULL DEFAULT 0,
                offered_genres TEXT NOT NULL DEFAULT '',
                common_genres TEXT NOT NULL DEFAULT '',
                started_at TEXT,
                finished_at TEXT,
                winner_id INTEGER,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS duel_genre_choices(
                duel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                genre TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(duel_id, user_id, genre)
            )""",
            """CREATE TABLE IF NOT EXISTS duel_questions(
                duel_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                PRIMARY KEY(duel_id, question_id),
                UNIQUE(duel_id, seq)
            )""",
            """CREATE TABLE IF NOT EXISTS duel_answers(
                duel_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                selected_option INTEGER,
                is_correct INTEGER NOT NULL DEFAULT 0,
                response_ms INTEGER,
                answered_at TEXT NOT NULL,
                PRIMARY KEY(duel_id, question_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS powerup_usages(
                duel_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                powerup TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(duel_id, question_id, user_id, powerup)
            )""",
            """CREATE TABLE IF NOT EXISTS xp_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                duel_id INTEGER,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS coin_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                duel_id INTEGER,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS shop_packages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                coins INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                price_label TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS shop_transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                package_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                receipt_type TEXT,
                receipt_text TEXT,
                receipt_file_id TEXT,
                admin_id INTEGER,
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS referrals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL UNIQUE,
                activated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                activated_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS question_reports(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                duel_id INTEGER,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS admin_actions_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS genres(
                name TEXT PRIMARY KEY,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS leagues(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                min_cups INTEGER NOT NULL UNIQUE,
                win_cups INTEGER NOT NULL DEFAULT 20,
                loss_cups INTEGER NOT NULL DEFAULT -10,
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS cup_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                duel_id INTEGER,
                league_id INTEGER,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS discount_codes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')),
                value INTEGER NOT NULL,
                max_uses INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )""",
        ]
        async with self._write_lock:
            for sql in statements:
                await self.conn.execute(sql)
            await self.conn.commit()
        await self.migrate_existing_schema()
        await self.seed_defaults()

    async def table_columns(self, table: str) -> set[str]:
        rows = await self.fetchall(f"PRAGMA table_info({table})")
        return {r["name"] for r in rows}

    async def add_column_if_missing(self, table: str, column: str, ddl: str) -> None:
        cols = await self.table_columns(table)
        if column not in cols:
            await self.execute_write(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    async def migrate_existing_schema(self) -> None:
        await self.add_column_if_missing("users", "cups", "cups INTEGER NOT NULL DEFAULT 0")
        await self.add_column_if_missing("users", "streak_day", "streak_day INTEGER NOT NULL DEFAULT 0")
        await self.add_column_if_missing("users", "streak_last_claim", "streak_last_claim TEXT")
        await self.add_column_if_missing("users", "streak_week_start", "streak_week_start TEXT")
        await self.add_column_if_missing("users", "last_duel_at", "last_duel_at TEXT")
        await self.add_column_if_missing("questions", "added_by", "added_by INTEGER")
        await self.add_column_if_missing("questions", "approved", "approved INTEGER NOT NULL DEFAULT 0")
        await self.add_column_if_missing("questions", "approved_by", "approved_by INTEGER")
        await self.add_column_if_missing("shop_packages", "package_type", "package_type TEXT NOT NULL DEFAULT 'coins'")
        await self.add_column_if_missing("shop_packages", "price_amount", "price_amount INTEGER NOT NULL DEFAULT 0")
        await self.add_column_if_missing("shop_transactions", "discount_code_id", "discount_code_id INTEGER")
        await self.add_column_if_missing("shop_transactions", "original_price_label", "original_price_label TEXT")
        await self.add_column_if_missing("shop_transactions", "final_price_label", "final_price_label TEXT")
        await self.add_column_if_missing("shop_transactions", "payment_method", "payment_method TEXT NOT NULL DEFAULT 'card_to_card'")
        await self.add_column_if_missing("leagues", "main_league", "main_league TEXT")
        await self.add_column_if_missing("leagues", "tier", "tier INTEGER")
        await self.add_column_if_missing("leagues", "is_final", "is_final INTEGER NOT NULL DEFAULT 0")
        await self.execute_write("UPDATE shop_packages SET package_type=CASE WHEN xp>0 AND coins=0 THEN 'xp' ELSE 'coins' END WHERE package_type IS NULL OR package_type='' OR package_type='coins'")
        for pkg in await self.fetchall("SELECT id,price_label FROM shop_packages WHERE price_amount=0"):
            amount = self.parse_price_amount(pkg["price_label"])
            if amount:
                await self.execute_write("UPDATE shop_packages SET price_amount=? WHERE id=?", (amount, pkg["id"]))
        await self.execute_write("UPDATE questions SET approved=1, approved_by=reviewed_by WHERE status='active' AND approved=0")
        for old_genre, new_genre in GENRE_ALIASES.items():
            await self.execute_write("UPDATE questions SET genre=? WHERE genre=?", (new_genre, old_genre))
        await self.execute_write("UPDATE questions SET genre=? WHERE genre NOT IN (%s)" % ",".join("?" for _ in CANONICAL_GENRES), tuple(["علم و دانش"] + CANONICAL_GENRES))

    async def seed_defaults(self) -> None:
        defaults = {
            "duel_question_count": ("7", "Number of questions per duel"),
            "question_timer_seconds": ("15", "Seconds per question"),
            "genres_to_offer": ("4", "Genres offered at once"),
            "genres_to_choose": ("2", "Genres each player chooses"),
            "reward_coin_per_correct": ("10", "Coins for each correct answer"),
            "reward_xp_per_correct": ("15", "XP for each correct answer"),
            "winner_bonus_xp": ("20", "XP bonus for duel winner"),
            "powerup_5050_cost": ("5", "Base cost of 50:50; doubles after each powerup use in a duel"),
            "powerup_hint_cost": ("5", "Base cost of hint; doubles after each powerup use in a duel"),
            "powerup_max_uses_per_duel": ("5", "Maximum total powerup uses per user per duel"),
            "question_approval_reward_coins": ("20", "Coins rewarded to user when submitted question is approved"),
            "daily_question_limit": ("5", "Daily user submissions"),
            "referral_referrer_coins": ("50", "Referrer coin reward"),
            "referral_referrer_xp": ("50", "Referrer XP reward"),
            "referral_referred_coins": ("25", "New user coin reward"),
            "referral_referred_xp": ("25", "New user XP reward"),
            "payment_card_number": ("0000-0000-0000-0000", "Shown in shop payment page"),
            "welcome_text": ("سلام! به ربات کوییز دوئلی خوش آمدی. از منوی پایین انتخاب کن:", "Editable /start welcome text"),
            "help_text": ("🎮 راهنمای کامل ربات کوییز دوئلی\n\n━━━━━━━━━━━━━━━\n🕹 بازی\n━━━━━━━━━━━━━━━\n🎲 دوئل شانسی — با یه حریف تصادفی بازی کن (هزینه: {random_duel_cost} سکه)\n🤝 دعوت دوست — لینک دوئل بفرست برای دوستت (هزینه: {friendly_duel_cost} سکه)\nقبل از شروع هر دوئل، ژانر سوالات رو انتخاب می‌کنید. سوالات فقط از ژانرهایی میان که هر دو نفر انتخاب کردن.\n\n━━━━━━━━━━━━━━━\n🏆 رقابت\n━━━━━━━━━━━━━━━\nبا هر برد جام و XP می‌گیری و توی لیگ بالا می‌ری.\nلیگ‌ها: برنزی ← نقره‌ای ← طلایی ← الماسی ← اسطوره‌ای\nهر لیگ 3 تیر داره. هرچی لیگ بالاتر، باخت گرون‌تره!\n\n━━━━━━━━━━━━━━━\n🪙 سکه\n━━━━━━━━━━━━━━━\nبا بازی و برد سکه می‌گیری.\nتوی دوئل می‌تونی از پاورآپ استفاده کنی (با سکه).\nاز فروشگاه هم می‌تونی سکه بخری.\n{initial_signup_coins} سکه هدیه‌ی شروع برای همه!\n\n━━━━━━━━━━━━━━━\n🔥 استریک روزانه\n━━━━━━━━━━━━━━━\nکمک روزانه فقط در هفته اول فعاله. اگر هر روز وارد بشی، روزهای 1 تا 7 سکه می‌گیری.\nروز اول: {streak_day_1_coins} سکه | روز 7: {streak_day_7_coins} سکه\nاگر یک روز جا بندازی، استریک خاموش میشه.\n\n━━━━━━━━━━━━━━━\n👥 رفرال\n━━━━━━━━━━━━━━━\nلینک دعوتت رو از بخش رفرال بگیر.\nهر دوستی که با لینک تو بیاد و اولین دوئلش رو بازی کنه:\n• تو: {referral_referrer_coins} سکه + {referral_referrer_xp} XP\n• اون: {referral_referred_coins} سکه + {referral_referred_xp} XP هدیه\n\n━━━━━━━━━━━━━━━\n📋 سوال بده\n━━━━━━━━━━━━━━━\nمی‌تونی سوال جدید پیشنهاد بدی. بعد از تایید ادمین وارد بازی میشه.", "Editable /help text with placeholders"),
            "max_level": ("100", "Maximum level"),
            "xp_level_curve_factor": ("112", "Quadratic XP curve factor; cumulative XP for level L is factor*(L-1)^2"),
            "start_photo_file_id": ("", "Optional photo file_id for /start"),
            "random_duel_cost": ("5", "Coins charged for random matchmaking entry"),
            "friendly_duel_cost": ("20", "Coins charged from invite duel creator"),
            "matchmaking_timeout_seconds": ("120", "Random matchmaking timeout seconds"),
            "maintenance_mode": ("0", "1 disables bot for non-admin users"),
            "maintenance_text": ("بات موقتاً در حال تعمیر است. لطفاً بعداً دوباره تلاش کنید.", "Shown during maintenance"),
            "payment_method": ("card_to_card", "Active payment method adapter"),
            "initial_signup_coins": ("50", "Coins granted on first /start"),
            "streak_day_1_coins": ("5", "First-week daily aid day 1 coins"),
            "streak_day_2_coins": ("10", "First-week daily aid day 2 coins"),
            "streak_day_3_coins": ("15", "First-week daily aid day 3 coins"),
            "streak_day_4_coins": ("20", "First-week daily aid day 4 coins"),
            "streak_day_5_coins": ("25", "First-week daily aid day 5 coins"),
            "streak_day_6_coins": ("30", "First-week daily aid day 6 coins"),
            "streak_day_7_coins": ("50", "First-week daily aid day 7 coins"),
            "streak_day_7_xp": ("0", "Disabled; daily aid day 7 XP"),
            "weekly_reward_coins": ("20", "Weekly reward coins after first week"),
        }
        for k, (v, d) in defaults.items():
            await self.execute_write("INSERT OR IGNORE INTO settings(key,value,description) VALUES(?,?,?)", (k, v, d))
        old_help = await self.get_setting("help_text", "")
        if old_help.startswith("راهنما:\n⚔️ دوئل") or "Streak روزانه" in old_help:
            await self.set_setting("help_text", defaults["help_text"][0])
        for key, old_value, new_value in [
            ("streak_day_1_coins", "10", "5"),
            ("streak_day_2_coins", "15", "10"),
            ("streak_day_3_coins", "20", "15"),
            ("streak_day_4_coins", "25", "20"),
            ("streak_day_5_coins", "30", "25"),
            ("streak_day_6_coins", "40", "30"),
            ("streak_day_7_xp", "100", "0"),
            ("powerup_5050_cost", "25", "5"),
            ("powerup_hint_cost", "35", "5"),
        ]:
            row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
            if row and row["value"] == old_value:
                await self.set_setting(key, new_value)
        ranks = [(1, "تازه‌کار"), (5, "دانشجو"), (10, "استاد"), (20, "قهرمان"), (35, "اسطوره"), (70, "افسانه‌ای"), (100, "مکس لول")]
        for min_level, title in ranks:
            await self.execute_write("INSERT OR IGNORE INTO ranks(min_level,title) VALUES(?,?)", (min_level, title))
        for i, genre in enumerate(CANONICAL_GENRES):
            await self.execute_write("INSERT OR IGNORE INTO genres(name,is_active,sort_order) VALUES(?,?,?)", (genre, 1, i))
        await self.seed_fixed_leagues()
        count = await self.fetchone("SELECT COUNT(*) c FROM shop_packages")
        if count and count["c"] == 0:
            await self.executemany_write(
                "INSERT INTO shop_packages(title, coins, xp, price_label, package_type, price_amount) VALUES(?,?,?,?,?,?)",
                [("بسته سکه شروع", 200, 0, "50,000 تومان", "coins", 50000), ("بسته XP", 0, 500, "70,000 تومان", "xp", 70000), ("بسته سکه حرفه‌ای", 800, 0, "180,000 تومان", "coins", 180000)],
            )

    async def seed_fixed_leagues(self) -> None:
        defaults = []
        main = [
            ("برنزی", 0, 25, 0),
            ("نقره‌ای", 300, 22, -8),
            ("طلایی", 750, 20, -15),
            ("الماسی", 1350, 18, -25),
        ]
        order = 1
        for league_name, base, win, loss in main:
            for tier in (1, 2, 3):
                defaults.append((f"{league_name} {tier}", league_name, tier, 0, base + (tier - 1) * 100, win - (tier - 1), loss - (tier - 1) * max(3, abs(loss) // 3), order))
                order += 1
        defaults.append(("اسطوره‌ای", "اسطوره‌ای", None, 1, 1800, 15, -40, order))
        existing_structured = await self.fetchone("SELECT COUNT(*) c FROM leagues WHERE main_league IS NOT NULL")
        if existing_structured and existing_structured["c"] == 0:
            old_rows = await self.fetchall("SELECT id FROM leagues ORDER BY min_cups,id")
            for idx, old in enumerate(old_rows[:len(defaults)]):
                name, main_league, tier, is_final, min_cups, win_cups, loss_cups, sort_order = defaults[idx]
                await self.execute_write("UPDATE leagues SET name=?,main_league=?,tier=?,is_final=?,min_cups=?,win_cups=?,loss_cups=?,sort_order=?,is_active=1 WHERE id=?", (name, main_league, tier, is_final, min_cups, win_cups, loss_cups, sort_order, old["id"]))
            for item in defaults[len(old_rows):]:
                name, main_league, tier, is_final, min_cups, win_cups, loss_cups, sort_order = item
                await self.execute_write("INSERT INTO leagues(name,main_league,tier,is_final,min_cups,win_cups,loss_cups,sort_order,is_active) VALUES(?,?,?,?,?,?,?,?,1)", item)
            return
        for name, main_league, tier, is_final, min_cups, win_cups, loss_cups, sort_order in defaults:
            row = await self.fetchone("SELECT id FROM leagues WHERE main_league=? AND ((tier IS NULL AND ? IS NULL) OR tier=?) AND is_final=?", (main_league, tier, tier, is_final))
            if row:
                await self.execute_write("UPDATE leagues SET is_active=1, sort_order=? WHERE id=?", (sort_order, row["id"]))
            else:
                await self.execute_write("INSERT INTO leagues(name,main_league,tier,is_final,min_cups,win_cups,loss_cups,sort_order,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (name, main_league, tier, is_final, min_cups, win_cups, loss_cups, sort_order))

    async def add_owner_admins(self, ids: set[int]) -> None:
        for admin_id in ids:
            await self.execute_write("INSERT OR IGNORE INTO admins(telegram_id, role, created_at) VALUES(?,?,?)", (admin_id, "owner", now_iso()))

    async def is_admin(self, telegram_id: int) -> bool:
        return bool(await self.fetchone("SELECT 1 FROM admins WHERE telegram_id=?", (telegram_id,)))

    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    async def get_int(self, key: str, default: int) -> int:
        try:
            return int(await self.get_setting(key, str(default)))
        except ValueError:
            logger.exception("Invalid integer setting: %s", key)
            return default

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute_write("UPDATE settings SET value=? WHERE key=?", (value, key))

    async def all_settings(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT key,value,description FROM settings ORDER BY key")

    async def render_help_text(self) -> str:
        text = await self.get_setting("help_text", "")
        keys = [
            "random_duel_cost", "friendly_duel_cost", "initial_signup_coins",
            "streak_day_1_coins", "streak_day_7_coins", "streak_day_7_xp",
            "referral_referrer_coins", "referral_referrer_xp",
            "referral_referred_coins", "referral_referred_xp",
        ]
        values = {k: await self.get_setting(k, "0") for k in keys}
        try:
            rendered = text.format(**values)
        except Exception:
            logger.exception("Help text format failed")
            rendered = text
        return rendered.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))

    async def upsert_user(self, tg_id: int, username: str | None, first_name: str | None, referred_by_tg: int | None = None) -> aiosqlite.Row:
        ts = now_iso()
        await self.execute_write(
            """INSERT INTO users(telegram_id,username,first_name,created_at,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, updated_at=excluded.updated_at""",
            (tg_id, username, first_name, ts, ts),
        )
        user = await self.get_user(tg_id)
        if user and referred_by_tg and referred_by_tg != tg_id and not user["referred_by"]:
            ref = await self.get_user(referred_by_tg)
            if ref:
                await self.execute_write("UPDATE users SET referred_by=? WHERE telegram_id=?", (referred_by_tg, tg_id))
                await self.execute_write("INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)", (referred_by_tg, tg_id, ts))
        return await self.get_user(tg_id)  # type: ignore[return-value]

    async def get_user(self, tg_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM users WHERE telegram_id=?", (tg_id,))

    async def change_coins(self, tg_id: int, amount: int, reason: str, duel_id: int | None = None) -> None:
        await self.execute_write("UPDATE users SET coins=MAX(0, coins + ?), updated_at=? WHERE telegram_id=?", (amount, now_iso(), tg_id))
        await self.execute_write("INSERT INTO coin_events(user_id,amount,reason,duel_id,created_at) VALUES(?,?,?,?,?)", (tg_id, amount, reason, duel_id, now_iso()))

    async def change_xp(self, tg_id: int, amount: int, reason: str, duel_id: int | None = None) -> None:
        await self.execute_write("UPDATE users SET xp=MAX(0, xp + ?), updated_at=? WHERE telegram_id=?", (amount, now_iso(), tg_id))
        await self.execute_write("INSERT INTO xp_events(user_id,amount,reason,duel_id,created_at) VALUES(?,?,?,?,?)", (tg_id, amount, reason, duel_id, now_iso()))
        await self.recalculate_level(tg_id)

    async def xp_required_for_level(self, level: int) -> int:
        factor = await self.get_int("xp_level_curve_factor", 112)
        return int(factor * max(0, level - 1) ** 2)

    async def level_bounds(self, level: int) -> tuple[int, int]:
        max_level = await self.get_int("max_level", 100)
        current = await self.xp_required_for_level(max(1, level))
        nxt = await self.xp_required_for_level(min(max_level, level + 1)) if level < max_level else current
        return current, nxt

    async def recalculate_level(self, tg_id: int) -> None:
        user = await self.get_user(tg_id)
        if user:
            max_level = await self.get_int("max_level", 100)
            level = 1
            for candidate in range(1, max_level + 1):
                required = await self.xp_required_for_level(candidate)
                if user["xp"] >= required:
                    level = candidate
                else:
                    break
            await self.execute_write("UPDATE users SET level=? WHERE telegram_id=?", (level, tg_id))

    async def get_rank_title(self, level: int) -> str:
        row = await self.fetchone("SELECT title FROM ranks WHERE min_level<=? ORDER BY min_level DESC LIMIT 1", (level,))
        return row["title"] if row else "بدون رتبه"

    async def get_user_league(self, cups: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM leagues WHERE is_active=1 AND min_cups<=? ORDER BY min_cups DESC LIMIT 1", (cups,))

    async def all_leagues(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM leagues WHERE is_active=1 ORDER BY sort_order ASC, min_cups ASC")

    async def get_league(self, league_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM leagues WHERE id=?", (league_id,))

    async def add_league(self, name: str, min_cups: int, win_cups: int, loss_cups: int) -> int:
        cur = await self.execute_write("INSERT INTO leagues(name,min_cups,win_cups,loss_cups,sort_order) VALUES(?,?,?,?,?)", (name, min_cups, win_cups, loss_cups, min_cups))
        return int(cur.lastrowid)

    async def update_league_field(self, league_id: int, field: str, value: Any) -> None:
        allowed = {"name", "min_cups", "win_cups", "loss_cups"}
        if field not in allowed:
            raise ValueError("Invalid league field")
        await self.execute_write(f"UPDATE leagues SET {field}=? WHERE id=?", (value, league_id))

    async def delete_league(self, league_id: int) -> None:
        await self.execute_write("UPDATE leagues SET is_active=0 WHERE id=?", (league_id,))

    async def change_cups(self, tg_id: int, amount: int, reason: str, duel_id: int | None = None, league_id: int | None = None) -> None:
        await self.execute_write("UPDATE users SET cups=MAX(0, cups + ?), updated_at=? WHERE telegram_id=?", (amount, now_iso(), tg_id))
        await self.execute_write("INSERT INTO cup_events(user_id,amount,reason,duel_id,league_id,created_at) VALUES(?,?,?,?,?,?)", (tg_id, amount, reason, duel_id, league_id, now_iso()))

    async def claim_streak_reward(self, tg_id: int) -> dict[str, Any] | None:
        """One-week daily aid only. If a day is missed, it is silently disabled forever."""
        user = await self.get_user(tg_id)
        if not user:
            return None
        streak_day = int(user["streak_day"] or 0)
        if streak_day < 0 or streak_day >= 7:
            return None
        now = tehran_now()
        now_iso_value = now.astimezone(UTC).isoformat(timespec="seconds")
        week_start = user["streak_week_start"]
        if not week_start:
            week_start = now_iso_value
            await self.execute_write("UPDATE users SET streak_week_start=? WHERE telegram_id=?", (week_start, tg_id))
        first_week_days = tehran_days_between(week_start, now)
        if first_week_days is not None and first_week_days >= 7 and streak_day < 7:
            await self.execute_write("UPDATE users SET streak_day=-1 WHERE telegram_id=?", (tg_id,))
            return None
        last_claim = user["streak_last_claim"]
        last_diff = jalali_date_diff_days(last_claim, now)
        if last_diff == 0:
            return None
        if last_claim is not None and last_diff != 1:
            await self.execute_write("UPDATE users SET streak_day=-1 WHERE telegram_id=?", (tg_id,))
            return None
        new_day = min(7, streak_day + 1 if streak_day > 0 else 1)
        coins = await self.get_int(f"streak_day_{new_day}_coins", 5)
        if coins:
            await self.change_coins(tg_id, coins, "daily_aid")
        await self.execute_write("UPDATE users SET streak_day=?, streak_last_claim=? WHERE telegram_id=?", (new_day, now_iso_value, tg_id))
        updated = await self.get_user(tg_id)
        return {"type": "daily_aid", "day": new_day, "coins": coins, "balance": int(updated["coins"] if updated else 0)}

    async def streak_status(self, tg_id: int) -> str:
        return ""

    async def create_waiting_duel(self, player_id: int) -> int:
        cur = await self.execute_write("INSERT INTO duels(player1_id,status,created_at) VALUES(?,?,?)", (player_id, "waiting", now_iso()))
        return int(cur.lastrowid)

    async def find_waiting_duel(self, exclude_user: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM duels WHERE status='waiting' AND player1_id<>? ORDER BY created_at LIMIT 1", (exclude_user,))

    async def join_duel(self, duel_id: int, player2_id: int) -> None:
        await self.execute_write("UPDATE duels SET player2_id=?, status='genre_selection' WHERE id=? AND status IN ('waiting','invite_waiting')", (player2_id, duel_id))

    async def create_invite_duel(self, player_id: int, token: str) -> int:
        cur = await self.execute_write("INSERT INTO duels(player1_id,status,invite_token,created_at) VALUES(?,?,?,?)", (player_id, "invite_waiting", token, now_iso()))
        return int(cur.lastrowid)

    async def get_duel(self, duel_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM duels WHERE id=?", (duel_id,))

    async def get_invite_duel(self, token: str) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM duels WHERE invite_token=? AND status='invite_waiting'", (token,))

    async def active_duel_for_user(self, tg_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM duels WHERE status IN ('waiting','invite_waiting','genre_selection','playing') AND (player1_id=? OR player2_id=?) ORDER BY id DESC LIMIT 1", (tg_id, tg_id))

    async def clear_other_active_duels_for_users(self, user_ids: list[int], keep_duel_id: int | None = None) -> None:
        if not user_ids:
            return
        placeholders = ",".join("?" for _ in user_ids)
        params: list[Any] = list(user_ids) + list(user_ids)
        sql = f"UPDATE duels SET status='cancelled', finished_at=? WHERE status IN ('waiting','invite_waiting','genre_selection','playing') AND (player1_id IN ({placeholders}) OR player2_id IN ({placeholders}))"
        params = [now_iso()] + params
        if keep_duel_id is not None:
            sql += " AND id<>?"
            params.append(keep_duel_id)
        await self.execute_write(sql, params)

    async def cancel_active_duels_with_refund(self) -> list[dict[str, Any]]:
        rows = await self.fetchall("SELECT * FROM duels WHERE status IN ('waiting','invite_waiting','genre_selection','playing')")
        random_cost = await self.get_int('random_duel_cost', 5)
        friendly_cost = await self.get_int('friendly_duel_cost', 20)
        results: list[dict[str, Any]] = []
        for d in rows:
            refunds: dict[int, int] = {}
            if d['status'] == 'waiting':
                refunds[d['player1_id']] = random_cost
            elif d['status'] == 'invite_waiting':
                refunds[d['player1_id']] = friendly_cost
            elif d['invite_token']:
                refunds[d['player1_id']] = friendly_cost
            else:
                refunds[d['player1_id']] = random_cost
                if d['player2_id']:
                    refunds[d['player2_id']] = random_cost
            await self.execute_write("UPDATE duels SET status='cancelled', finished_at=? WHERE id=?", (now_iso(), d['id']))
            for uid, amount in refunds.items():
                await self.change_coins(uid, amount, 'maintenance_duel_refund', d['id'])
            results.append({'duel_id': d['id'], 'refunds': refunds})
        return results

    async def available_genres(self) -> list[str]:
        rows = await self.fetchall("""SELECT g.name FROM genres g
                                      WHERE g.is_active=1 AND EXISTS(
                                          SELECT 1 FROM questions q WHERE q.status='active' AND q.genre=g.name
                                      )
                                      ORDER BY g.sort_order, g.name""")
        return [r["name"] for r in rows]

    async def all_genres(self) -> list[str]:
        rows = await self.fetchall("SELECT name FROM genres WHERE is_active=1 ORDER BY sort_order,name")
        return [r["name"] for r in rows]

    async def set_offered_genres(self, duel_id: int, genres: list[str]) -> None:
        duel = await self.get_duel(duel_id)
        old = [g for g in (duel["offered_genres"] if duel else "").split("|") if g]
        merged = old + [g for g in genres if g not in old]
        await self.execute_write("UPDATE duels SET offered_genres=? WHERE id=?", ("|".join(merged), duel_id))

    async def save_genre_choices(self, duel_id: int, user_id: int, genres: list[str]) -> None:
        await self.execute_write("DELETE FROM duel_genre_choices WHERE duel_id=? AND user_id=?", (duel_id, user_id))
        await self.executemany_write("INSERT OR IGNORE INTO duel_genre_choices(duel_id,user_id,genre,created_at) VALUES(?,?,?,?)", [(duel_id, user_id, g, now_iso()) for g in genres])

    async def duel_choices(self, duel_id: int) -> dict[int, set[str]]:
        rows = await self.fetchall("SELECT user_id,genre FROM duel_genre_choices WHERE duel_id=?", (duel_id,))
        out: dict[int, set[str]] = {}
        for r in rows:
            out.setdefault(r["user_id"], set()).add(r["genre"])
        return out

    async def select_questions_for_duel(self, genres: list[str], limit: int, exclude: set[int]) -> list[aiosqlite.Row]:
        if not genres:
            return []
        placeholders = ",".join("?" for _ in genres)
        params: list[Any] = list(genres)
        sql = f"SELECT * FROM questions WHERE status='active' AND genre IN ({placeholders})"
        if exclude:
            sql += " AND id NOT IN (" + ",".join("?" for _ in exclude) + ")"
            params += list(exclude)
        sql += " ORDER BY RANDOM() LIMIT ?"
        params.append(limit)
        return await self.fetchall(sql, params)

    async def start_duel_questions(self, duel_id: int, genres: list[str], count: int) -> list[aiosqlite.Row]:
        qs = await self.select_questions_for_duel(genres, count, set())
        await self.executemany_write("INSERT OR IGNORE INTO duel_questions(duel_id,question_id,seq) VALUES(?,?,?)", [(duel_id, q["id"], i) for i, q in enumerate(qs)])
        await self.execute_write("UPDATE duels SET status='playing', started_at=?, common_genres=? WHERE id=?", (now_iso(), "|".join(genres), duel_id))
        return qs

    async def duel_question_by_seq(self, duel_id: int, seq: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT q.* FROM duel_questions dq JOIN questions q ON q.id=dq.question_id WHERE dq.duel_id=? AND dq.seq=?", (duel_id, seq))

    async def duel_questions_count(self, duel_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) c FROM duel_questions WHERE duel_id=?", (duel_id,))
        return int(row["c"] if row else 0)

    async def record_answer(self, duel_id: int, qid: int, user_id: int, selected: int | None, correct_option: int, response_ms: int | None) -> bool:
        is_correct = int(selected == correct_option) if selected is not None else 0
        try:
            await self.execute_write(
                "INSERT INTO duel_answers(duel_id,question_id,user_id,selected_option,is_correct,response_ms,answered_at) VALUES(?,?,?,?,?,?,?)",
                (duel_id, qid, user_id, selected, is_correct, response_ms, now_iso()),
            )
            await self.execute_write("UPDATE users SET correct_answers=correct_answers+?, total_answers=total_answers+? WHERE telegram_id=?", (is_correct, 1 if selected else 0, user_id))
            return True
        except Exception:
            logger.exception("Could not record answer duel=%s q=%s user=%s", duel_id, qid, user_id)
            return False

    async def answered_count_for_question(self, duel_id: int, qid: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) c FROM duel_answers WHERE duel_id=? AND question_id=?", (duel_id, qid))
        return int(row["c"] if row else 0)

    async def mark_powerup(self, duel_id: int, qid: int, user_id: int, powerup: str) -> bool:
        try:
            await self.execute_write("INSERT INTO powerup_usages(duel_id,question_id,user_id,powerup,created_at) VALUES(?,?,?,?,?)", (duel_id, qid, user_id, powerup, now_iso()))
            return True
        except Exception:
            logger.exception("Powerup already used or failed")
            return False

    async def has_powerup(self, duel_id: int, qid: int, user_id: int, powerup: str) -> bool:
        return bool(await self.fetchone("SELECT 1 FROM powerup_usages WHERE duel_id=? AND question_id=? AND user_id=? AND powerup=?", (duel_id, qid, user_id, powerup)))

    async def powerup_uses_in_duel(self, duel_id: int, user_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) c FROM powerup_usages WHERE duel_id=? AND user_id=?", (duel_id, user_id))
        return int(row["c"] if row else 0)

    async def powerup_costs_for_user(self, duel_id: int, user_id: int) -> dict[str, int]:
        uses = await self.powerup_uses_in_duel(duel_id, user_id)
        multiplier = 2 ** uses
        return {
            "uses": uses,
            "5050": await self.get_int("powerup_5050_cost", 5) * multiplier,
            "hint": await self.get_int("powerup_hint_cost", 5) * multiplier,
            "max": await self.get_int("powerup_max_uses_per_duel", 5),
        }

    async def finish_duel(self, duel_id: int) -> dict[str, Any]:
        duel = await self.get_duel(duel_id)
        if not duel:
            return {}
        rows = await self.fetchall("""SELECT user_id, SUM(is_correct) correct, COALESCE(SUM(response_ms), 999999999) speed
                                      FROM duel_answers WHERE duel_id=? GROUP BY user_id""", (duel_id,))
        stats = {r["user_id"]: {"correct": int(r["correct"] or 0), "speed": int(r["speed"] or 999999999)} for r in rows}
        for p in [duel["player1_id"], duel["player2_id"]]:
            stats.setdefault(p, {"correct": 0, "speed": 999999999})
        p1, p2 = duel["player1_id"], duel["player2_id"]
        before: dict[int, dict[str, Any]] = {}
        for uid in [p1, p2]:
            u = await self.get_user(uid)
            lg = await self.get_user_league(int(u["cups"] if u else 0))
            before[uid] = {"level": int(u["level"] if u else 1), "coins": int(u["coins"] if u else 0), "xp": int(u["xp"] if u else 0), "cups": int(u["cups"] if u else 0), "league_id": lg["id"] if lg else None, "league_name": lg["name"] if lg else "بدون لیگ", "league_order": int(lg["sort_order"] if lg else 0)}
        winner = None
        if (stats[p1]["correct"], -stats[p1]["speed"]) > (stats[p2]["correct"], -stats[p2]["speed"]):
            winner = p1
        elif (stats[p2]["correct"], -stats[p2]["speed"]) > (stats[p1]["correct"], -stats[p1]["speed"]):
            winner = p2
        await self.execute_write("UPDATE duels SET status='finished', finished_at=?, winner_id=? WHERE id=?", (now_iso(), winner, duel_id))
        is_random_duel = not bool(duel["invite_token"])
        coin_per = await self.get_int("reward_coin_per_correct", 10)
        xp_per = await self.get_int("reward_xp_per_correct", 15)
        bonus = await self.get_int("winner_bonus_xp", 20)
        for uid, st in stats.items():
            if st["correct"]:
                if is_random_duel:
                    await self.change_coins(uid, st["correct"] * coin_per, "duel_correct", duel_id)
                await self.change_xp(uid, st["correct"] * xp_per, "duel_correct", duel_id)
            urow = await self.get_user(uid)
            league = await self.get_user_league(int(urow["cups"] if urow else 0))
            if winner == uid:
                await self.change_xp(uid, bonus, "winner_bonus", duel_id)
                if league:
                    await self.change_cups(uid, int(league["win_cups"]), "duel_win", duel_id, league["id"])
                await self.execute_write("UPDATE users SET wins=wins+1, last_duel_at=? WHERE telegram_id=?", (now_iso(), uid))
            elif winner is None:
                await self.execute_write("UPDATE users SET draws=draws+1, last_duel_at=? WHERE telegram_id=?", (now_iso(), uid))
            else:
                if league:
                    await self.change_cups(uid, int(league["loss_cups"]), "duel_loss", duel_id, league["id"])
                await self.execute_write("UPDATE users SET losses=losses+1, last_duel_at=? WHERE telegram_id=?", (now_iso(), uid))
        transitions: dict[int, dict[str, Any]] = {}
        for uid in [p1, p2]:
            u = await self.get_user(uid)
            lg = await self.get_user_league(int(u["cups"] if u else 0))
            after = {"level": int(u["level"] if u else 1), "coins": int(u["coins"] if u else 0), "xp": int(u["xp"] if u else 0), "cups": int(u["cups"] if u else 0), "league_id": lg["id"] if lg else None, "league_name": lg["name"] if lg else "بدون لیگ", "league_order": int(lg["sort_order"] if lg else 0)}
            transitions[uid] = {
                "before": before[uid],
                "after": after,
                "rewards": {"coins": after["coins"] - before[uid]["coins"], "xp": after["xp"] - before[uid]["xp"], "cups": after["cups"] - before[uid]["cups"]},
                "level_up": after["level"] > before[uid]["level"],
                "league_promoted": after["league_order"] > before[uid]["league_order"],
                "league_demoted": after["league_order"] < before[uid]["league_order"],
            }
        await self.clear_other_active_duels_for_users([p1, p2], keep_duel_id=duel_id)
        await self.activate_referrals_for_players([p1, p2])
        return {"winner": winner, "stats": stats, "transitions": transitions}

    async def activate_referrals_for_players(self, players: list[int]) -> None:
        for uid in players:
            ref = await self.fetchone("SELECT * FROM referrals WHERE referred_id=? AND activated=0", (uid,))
            if ref:
                rc = await self.get_int("referral_referrer_coins", 50)
                rx = await self.get_int("referral_referrer_xp", 50)
                nc = await self.get_int("referral_referred_coins", 25)
                nx = await self.get_int("referral_referred_xp", 25)
                await self.change_coins(ref["referrer_id"], rc, "referral")
                await self.change_xp(ref["referrer_id"], rx, "referral")
                await self.change_coins(uid, nc, "referral_new_user")
                await self.change_xp(uid, nx, "referral_new_user")
                await self.execute_write("UPDATE referrals SET activated=1, activated_at=? WHERE id=?", (now_iso(), ref["id"]))

    async def leaderboard(self, basis: str = "level", period: str = "all") -> list[aiosqlite.Row]:
        since: str | None = None
        if period == "daily":
            since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        elif period == "monthly":
            since = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
        if basis == "league":
            if since:
                return await self.fetchall("""SELECT u.telegram_id,u.first_name,u.username,u.level,u.cups,
                                           COALESCE(SUM(c.amount),0) score,
                                           COALESCE((SELECT l.name FROM leagues l WHERE l.is_active=1 AND l.min_cups<=u.cups ORDER BY l.min_cups DESC LIMIT 1),'بدون لیگ') league_name
                                           FROM users u LEFT JOIN cup_events c ON c.user_id=u.telegram_id AND c.created_at>=?
                                           GROUP BY u.telegram_id ORDER BY score DESC,u.cups DESC LIMIT 10""", (since,))
            return await self.fetchall("""SELECT u.telegram_id,u.first_name,u.username,u.level,u.cups,u.cups score,
                                       COALESCE((SELECT l.name FROM leagues l WHERE l.is_active=1 AND l.min_cups<=u.cups ORDER BY l.min_cups DESC LIMIT 1),'بدون لیگ') league_name
                                       FROM users u ORDER BY u.cups DESC,u.level DESC LIMIT 10""")
        if since:
            return await self.fetchall("""SELECT u.telegram_id,u.first_name,u.username,u.level,u.cups,
                                       COALESCE(SUM(x.amount),0) score,
                                       COALESCE((SELECT l.name FROM leagues l WHERE l.is_active=1 AND l.min_cups<=u.cups ORDER BY l.min_cups DESC LIMIT 1),'بدون لیگ') league_name
                                       FROM users u LEFT JOIN xp_events x ON x.user_id=u.telegram_id AND x.created_at>=?
                                       GROUP BY u.telegram_id ORDER BY score DESC,u.level DESC LIMIT 10""", (since,))
        return await self.fetchall("""SELECT u.telegram_id,u.first_name,u.username,u.level,u.cups,u.level score,
                                   COALESCE((SELECT l.name FROM leagues l WHERE l.is_active=1 AND l.min_cups<=u.cups ORDER BY l.min_cups DESC LIMIT 1),'بدون لیگ') league_name
                                   FROM users u ORDER BY u.level DESC,u.xp DESC LIMIT 10""")

    async def create_shop_tx(self, user_id: int, package_id: int) -> int:
        pkg = await self.get_package(package_id)
        price = pkg["price_label"] if pkg else ""
        cur = await self.execute_write("INSERT INTO shop_transactions(user_id,package_id,status,created_at,original_price_label,final_price_label,payment_method) VALUES(?,?,?,?,?,?,?)", (user_id, package_id, "awaiting_discount", now_iso(), price, price, await self.get_setting("payment_method", "card_to_card")))
        return int(cur.lastrowid)

    async def shop_packages(self, package_type: str | None = None) -> list[aiosqlite.Row]:
        if package_type:
            return await self.fetchall("SELECT * FROM shop_packages WHERE is_active=1 AND package_type=? ORDER BY id", (package_type,))
        return await self.fetchall("SELECT * FROM shop_packages WHERE is_active=1 ORDER BY package_type,id")

    async def get_package(self, package_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM shop_packages WHERE id=?", (package_id,))

    def parse_price_amount(self, price_label: str) -> int:
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        digits = "".join(ch for ch in price_label.translate(trans) if ch.isdigit())
        return int(digits) if digits else 0

    async def add_shop_package(self, package_type: str, title: str, amount: int, price_label: str) -> int:
        coins = amount if package_type == "coins" else 0
        xp = amount if package_type == "xp" else 0
        price_amount = self.parse_price_amount(price_label)
        cur = await self.execute_write("INSERT INTO shop_packages(title,coins,xp,price_label,package_type,price_amount,is_active) VALUES(?,?,?,?,?,?,1)", (title, coins, xp, price_label, package_type, price_amount))
        return int(cur.lastrowid)

    async def update_shop_package_field(self, package_id: int, field: str, value: Any) -> None:
        pkg = await self.get_package(package_id)
        if not pkg:
            raise ValueError("Package not found")
        if field == "title":
            await self.execute_write("UPDATE shop_packages SET title=? WHERE id=?", (str(value), package_id))
        elif field == "price_label":
            await self.execute_write("UPDATE shop_packages SET price_label=?, price_amount=? WHERE id=?", (str(value), self.parse_price_amount(str(value)), package_id))
        elif field == "amount":
            amount = int(value)
            if pkg["package_type"] == "xp":
                await self.execute_write("UPDATE shop_packages SET xp=?, coins=0 WHERE id=?", (amount, package_id))
            else:
                await self.execute_write("UPDATE shop_packages SET coins=?, xp=0 WHERE id=?", (amount, package_id))
        else:
            raise ValueError("Invalid package field")

    async def delete_shop_package(self, package_id: int) -> None:
        await self.execute_write("UPDATE shop_packages SET is_active=0 WHERE id=?", (package_id,))

    async def save_receipt(self, tx_id: int, rtype: str, text: str | None, file_id: str | None) -> None:
        await self.execute_write("UPDATE shop_transactions SET status='pending_admin', receipt_type=?, receipt_text=?, receipt_file_id=? WHERE id=?", (rtype, text, file_id, tx_id))

    async def mark_tx_ready_to_pay(self, tx_id: int) -> None:
        await self.execute_write("UPDATE shop_transactions SET status='awaiting_receipt' WHERE id=?", (tx_id,))

    async def create_discount(self, admin_id: int, code: str, discount_type: str, value: int, max_uses: int | None, expires_at: str | None) -> int:
        cur = await self.execute_write("INSERT INTO discount_codes(code,discount_type,value,max_uses,expires_at,created_by,created_at) VALUES(?,?,?,?,?,?,?)", (code.upper().strip(), discount_type, value, max_uses, expires_at, admin_id, now_iso()))
        return int(cur.lastrowid)

    async def discounts(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM discount_codes ORDER BY id DESC LIMIT 50")

    async def disable_discount(self, discount_id: int) -> None:
        await self.execute_write("UPDATE discount_codes SET is_active=0 WHERE id=?", (discount_id,))

    async def get_discount_by_code(self, code: str) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM discount_codes WHERE code=? AND is_active=1", (code.upper().strip(),))

    async def apply_discount_to_tx(self, tx_id: int, code: str) -> tuple[bool, str]:
        tx = await self.get_tx(tx_id)
        dc = await self.get_discount_by_code(code)
        if not tx or not dc:
            return False, "کد تخفیف معتبر نیست."
        if dc["max_uses"] is not None and dc["used_count"] >= dc["max_uses"]:
            return False, "ظرفیت استفاده از این کد تمام شده است."
        if dc["expires_at"] and dc["expires_at"] < now_iso():
            return False, "تاریخ انقضای این کد گذشته است."
        amount = int(tx["price_amount"] or 0)
        if amount <= 0:
            final_label = tx["price_label"]
        elif dc["discount_type"] == "percent":
            final_amount = max(0, amount - (amount * int(dc["value"]) // 100))
            final_label = f"{final_amount:,} تومان"
        else:
            final_amount = max(0, amount - int(dc["value"]))
            final_label = f"{final_amount:,} تومان"
        await self.execute_write("UPDATE shop_transactions SET discount_code_id=?, final_price_label=? WHERE id=?", (dc["id"], final_label, tx_id))
        return True, final_label

    async def get_tx(self, tx_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT t.*, p.title,p.coins,p.xp,p.price_label,p.price_amount,p.package_type FROM shop_transactions t JOIN shop_packages p ON p.id=t.package_id WHERE t.id=?", (tx_id,))

    async def review_tx(self, tx_id: int, admin_id: int, approve: bool) -> aiosqlite.Row | None:
        tx = await self.get_tx(tx_id)
        if not tx or tx["status"] != "pending_admin":
            return None
        status = "approved" if approve else "rejected"
        await self.execute_write("UPDATE shop_transactions SET status=?, admin_id=?, reviewed_at=? WHERE id=?", (status, admin_id, now_iso(), tx_id))
        if approve:
            if tx["discount_code_id"]:
                await self.execute_write("UPDATE discount_codes SET used_count=used_count+1 WHERE id=?", (tx["discount_code_id"],))
            if tx["coins"]:
                await self.change_coins(tx["user_id"], tx["coins"], "shop_purchase")
            if tx["xp"]:
                await self.change_xp(tx["user_id"], tx["xp"], "shop_purchase")
        return tx

    async def daily_submissions_count(self, user_id: int) -> int:
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        row = await self.fetchone("SELECT COUNT(*) c FROM questions WHERE submitted_by=? AND created_at>=?", (user_id, since))
        return int(row["c"] if row else 0)

    async def submit_question(self, user_id: int, text: str, opts: list[str], correct: int, genre: str) -> int:
        normalized = normalize_genre_db(genre)
        cur = await self.execute_write("""INSERT INTO questions(text,option1,option2,option3,option4,correct_option,genre,status,submitted_by,created_at,approved)
                                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (text, opts[0], opts[1], opts[2], opts[3], correct, normalized, "pending", user_id, now_iso(), 0))
        return int(cur.lastrowid)

    async def admin_add_question(self, admin_id: int, text: str, opts: list[str], correct: int, genre: str) -> int:
        normalized = normalize_genre_db(genre)
        ts = now_iso()
        cur = await self.execute_write("""INSERT INTO questions(text,option1,option2,option3,option4,correct_option,genre,status,submitted_by,created_at,reviewed_by,reviewed_at,added_by,approved,approved_by)
                                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (text, opts[0], opts[1], opts[2], opts[3], correct, normalized, "active", admin_id, ts, admin_id, ts, admin_id, 1, admin_id))
        return int(cur.lastrowid)

    async def bulk_admin_add_questions(self, admin_id: int, items: list[dict[str, Any]]) -> int:
        ts = now_iso()
        rows = []
        for item in items:
            genre = normalize_genre_db(item["genre"])
            rows.append((item["question"], item["options"][0], item["options"][1], item["options"][2], item["options"][3], item["correct"], genre, "active", admin_id, ts, admin_id, ts, admin_id, 1, admin_id))
        async with self._write_lock:
            await self.conn.executemany("""INSERT INTO questions(text,option1,option2,option3,option4,correct_option,genre,status,submitted_by,created_at,reviewed_by,reviewed_at,added_by,approved,approved_by)
                                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
            await self.conn.commit()
        return len(rows)

    async def question_genre_counts(self, status: str = "pending") -> list[tuple[str, int]]:
        rows = await self.fetchall("""SELECT g.name genre, COUNT(q.id) c
                                      FROM genres g
                                      LEFT JOIN questions q ON q.genre=g.name AND q.status=?
                                      WHERE g.is_active=1
                                      GROUP BY g.name,g.sort_order
                                      ORDER BY g.sort_order,g.name""", (status,))
        return [(r["genre"], int(r["c"])) for r in rows]

    async def pending_question_genre_counts(self) -> list[tuple[str, int]]:
        return await self.question_genre_counts("pending")

    async def questions_by_genre(self, genre: str, status: str = "pending") -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM questions WHERE status=? AND genre=? ORDER BY created_at DESC LIMIT 30", (status, genre))

    async def pending_questions_by_genre(self, genre: str) -> list[aiosqlite.Row]:
        return await self.questions_by_genre(genre, "pending")

    async def get_question(self, qid: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM questions WHERE id=?", (qid,))

    async def invalid_genre_questions(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT id,text,genre,status FROM questions WHERE genre NOT IN (%s) ORDER BY id LIMIT 200" % ",".join("?" for _ in CANONICAL_GENRES), tuple(CANONICAL_GENRES))

    async def delete_invalid_genre_questions(self) -> int:
        rows = await self.invalid_genre_questions()
        ids = [r["id"] for r in rows]
        if not ids:
            return 0
        await self.execute_write("DELETE FROM questions WHERE id IN (%s)" % ",".join("?" for _ in ids), ids)
        return len(ids)

    async def review_question(self, qid: int, admin_id: int, approve: bool) -> aiosqlite.Row | None:
        q = await self.fetchone("SELECT * FROM questions WHERE id=?", (qid,))
        if not q or q["status"] != "pending":
            return None
        await self.execute_write("UPDATE questions SET status=?, reviewed_by=?, reviewed_at=?, approved=?, approved_by=? WHERE id=?", ("active" if approve else "rejected", admin_id, now_iso(), 1 if approve else 0, admin_id if approve else None, qid))
        return q

    async def add_report(self, question_id: int, reporter_id: int, duel_id: int | None, reason: str | None) -> int:
        cur = await self.execute_write("INSERT INTO question_reports(question_id,reporter_id,duel_id,reason,created_at) VALUES(?,?,?,?,?)", (question_id, reporter_id, duel_id, reason, now_iso()))
        return int(cur.lastrowid)

    async def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        now_t = tehran_now()
        today_start = now_t.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat(timespec="seconds")
        week_start = (now_t - timedelta(days=7)).astimezone(UTC).isoformat(timespec="seconds")
        month_start = (now_t - timedelta(days=30)).astimezone(UTC).isoformat(timespec="seconds")
        year_start = (now_t - timedelta(days=365)).astimezone(UTC).isoformat(timespec="seconds")
        for key, sql, params in [
            ("users", "SELECT COUNT(*) c FROM users", ()),
            ("new_users_today", "SELECT COUNT(*) c FROM users WHERE created_at>=?", (today_start,)),
            ("duels", "SELECT COUNT(*) c FROM duels", ()),
            ("finished_duels", "SELECT COUNT(*) c FROM duels WHERE status='finished'", ()),
            ("games_today", "SELECT COUNT(*) c FROM duels WHERE status='finished' AND finished_at>=?", (today_start,)),
            ("approved_transactions", "SELECT COUNT(*) c FROM shop_transactions WHERE status='approved'", ()),
            ("pending_questions", "SELECT COUNT(*) c FROM questions WHERE status='pending'", ()),
            ("total_questions", "SELECT COUNT(*) c FROM questions", ()),
            ("user_questions", "SELECT COUNT(*) c FROM questions WHERE submitted_by IS NOT NULL AND (added_by IS NULL OR added_by<>submitted_by)", ()),
            ("admin_questions", "SELECT COUNT(*) c FROM questions WHERE added_by IS NOT NULL", ()),
            ("coins_generated", "SELECT COALESCE(SUM(amount),0) c FROM coin_events WHERE amount>0", ()),
            ("coins_burned", "SELECT COALESCE(SUM(-amount),0) c FROM coin_events WHERE amount<0", ()),
        ]:
            row = await self.fetchone(sql, params)
            out[key] = int(row["c"] if row else 0)
        tx_rows = await self.fetchall("SELECT final_price_label, original_price_label, created_at, reviewed_at FROM shop_transactions WHERE status='approved'")
        for label, since in [("revenue_week", week_start), ("revenue_month", month_start), ("revenue_year", year_start)]:
            total = 0
            for tx in tx_rows:
                ts = tx["reviewed_at"] or tx["created_at"]
                if ts and ts >= since:
                    total += self.parse_price_amount(tx["final_price_label"] or tx["original_price_label"] or "0")
            out[label] = total
        return out

    async def log_admin(self, admin_id: int, action: str, target: str | None = None, details: str | None = None) -> None:
        await self.execute_write("INSERT INTO admin_actions_log(admin_id,action,target,details,created_at) VALUES(?,?,?,?,?)", (admin_id, action, target, details, now_iso()))

    async def backup_copy(self) -> str:
        dest = f"{self.path}.{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.backup"
        async with self._write_lock:
            await self.conn.execute("PRAGMA wal_checkpoint(FULL)")
            await self.conn.commit()
            shutil.copy2(self.path, dest)
        return dest
