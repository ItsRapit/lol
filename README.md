# Telegram Duel Quiz Bot (Railway-ready)

ربات کوییز دوئلی ۱ به ۱ با Python، فریم‌ورک async `aiogram 3`، دیتابیس SQLite/WAL و Dockerfile آماده Railway.

## معماری

- `app/main.py`: نقطه ورود، اتصال دیتابیس، راه‌اندازی aiogram و تزریق dependencyها.
- `app/db.py`: تنها درگاه دیتابیس؛ همه queryها، migrationها و عملیات write از این ماژول عبور می‌کند. SQLite با WAL، `busy_timeout` و lock نوشتن async پیکربندی شده است.
- `app/handlers/*`: handlerهای جدا برای قابلیت‌ها: دوئل، فروشگاه، سوالات، ادمین، عمومی.
- `app/states.py`: همه State Machineها در یک فایل و با الگوی واحد aiogram FSM.
- `app/keyboards.py`: همه کیبوردهای inline/reply.
- `app/config.py`: خواندن تنظیمات حساس از env.

## قابلیت‌ها

- دوئل تصادفی و دعوتی با deep-link.
- انتخاب ژانر بدون تکرار در دست فعال؛ اگر اشتراک ژانر صفر باشد، گزینه‌های جدید بدون تکرار نمایش داده می‌شود.
- انتخاب سوال تصادفی بدون جایگزینی و ذخیره ID سوالات در `duel_questions`؛ سوال تکراری در همان دوئل تکرار نمی‌شود.
- تایمر هر سوال، ثبت پاسخ/عدم پاسخ، تعیین برنده با تعداد پاسخ صحیح و سرعت.
- پاداش سکه و XP به‌صورت per-question-correct، نه پاداش ثابت شرکت.
- پاورآپ پولی 50:50 و راهنمایی با قیمت‌های قابل ویرایش ادمین.
- فروشگاه با ارسال رسید به کانال خصوصی و تایید/رد inline توسط ادمین.
- XP/Level/Rank، لیدربورد هفتگی/ماهانه/کلی با جدول رویدادهای XP.
- ثبت سوال کاربر و تایید/رد ادمین.
- گزارش سوال با Question ID دقیق، Duel ID و Reporter ID.
- پنل ادمین، تنظیمات runtime، مدیریت کاربر، مدیریت ادمین، بک‌آپ `/backup`.

## نصب لوکال

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env را با مقدارهای واقعی پر کنید
python -m app.main
```

حداقل یک سوال فعال در چند ژانر لازم است تا دوئل شروع شود. سوال‌ها می‌توانند توسط کاربران پیشنهاد و توسط ادمین تایید شوند؛ یا مستقیماً در دیتابیس اضافه شوند.

## متغیرهای محیطی

```env
BOT_TOKEN=توکن BotFather
BOT_USERNAME=نام کاربری ربات بدون @
OWNER_ADMIN_IDS=123456789,987654321
ADMIN_REVIEW_CHANNEL_ID=-100...
REPORTS_CHANNEL_ID=-100...
DATABASE_PATH=/data/quiz_duel.sqlite3
LOG_LEVEL=INFO
```

ربات باید در کانال‌های `ADMIN_REVIEW_CHANNEL_ID` و `REPORTS_CHANNEL_ID` ادمین باشد تا بتواند پیام ارسال کند.

## Deploy روی Railway

1. پروژه را در GitHub قرار دهید.
2. در Railway یک **New Project → Deploy from GitHub** بسازید.
3. متغیرهای محیطی بالا را در بخش Variables تنظیم کنید.
4. یک **Volume** بسازید و آن را روی مسیر `/data` mount کنید.
5. مقدار `DATABASE_PATH` را `/data/quiz_duel.sqlite3` بگذارید.
6. Railway با Dockerfile پروژه را build و اجرا می‌کند.

> اگر Volume نسازید، دیتابیس SQLite بعد از redeploy/restart ممکن است از بین برود.

## نکته طراحی پنل ادمین

Callbackهای پنل ادمین در هر state قابل اجرا هستند و در ابتدای اجرای خود `state.clear()` انجام می‌دهند؛ بنابراین اگر ادمین وسط وارد کردن عدد باشد و روی یک دکمه دیگر بزند، آن دکمه واقعاً اجرا می‌شود و بی‌صدا نادیده گرفته نمی‌شود.

## بک‌آپ

ادمین‌ها می‌توانند هر زمان دستور زیر را بزنند:

```text
/backup
```

ربات یک checkpoint از WAL می‌گیرد، فایل SQLite را کپی می‌کند و همان فایل را برای ادمین ارسال می‌کند.
