from __future__ import annotations

import json
import logging
import re
from typing import Any
from app.utils import CANONICAL_GENRES

logger = logging.getLogger(__name__)

CORRECT_MAP = {"A": 1, "B": 2, "C": 3, "D": 4, "1": 1, "2": 2, "3": 3, "4": 4, "الف": 1, "ب": 2, "ج": 3, "د": 4}
QUESTION_KEYS = ("question", "text", "q", "title", "prompt")
GENRE_KEYS = ("category", "genre", "cat", "subject", "topic")
CORRECT_KEYS = ("correct", "answer", "right", "correct_option", "correctAnswer")
OPTIONS_KEYS = ("options", "choices", "answers")
IGNORED_KEYS = {"added_by", "approved", "approved_by", "created_at"}


def extract_json_text(text: str) -> str:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()


def looks_like_json(text: str) -> bool:
    stripped = extract_json_text(text).lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def looks_like_bulk_text(text: str) -> bool:
    text = extract_json_text(text)
    return bool(re.search(r"(?im)^\s*(سوال|question)\s*\d*\s*[:：]", text))


def is_json_balanced(text: str) -> bool:
    text = extract_json_text(text)
    braces = brackets = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1
        if braces < 0 or brackets < 0:
            return False
    return braces == 0 and brackets == 0 and not in_string


def bulk_help_text(genres: list[str] | None = None) -> str:
    genres = genres or CANONICAL_GENRES
    sample = '''[
  {
    "category": "فوتبال",
    "question": "کدام کشور قهرمان جام جهانی 2022 شد؟",
    "options": {"A": "فرانسه", "B": "آرژانتین", "C": "برزیل", "D": "آلمان"},
    "correct": "B"
  },
  {
    "category": "تکنولوژی",
    "question": "HTML مخفف چیست؟",
    "options": {"A": "HyperText Markup Language", "B": "HighText Machine Language", "C": "Hyper Tool Multi Language", "D": "Home Tool Markup Language"},
    "correct": "A"
  }
]'''
    return (
        "📥 افزودن Bulk سوال\n\n"
        "ژانرهای معتبر دقیقاً یکی از این موارد هستند:\n"
        + "، ".join(genres)
        + "\n\nنمونه JSON قابل قبول:\n"
        + f"<pre>{sample}</pre>\n"
        + "\nنمونه فرم متنی داخل خود تلگرام:\n"
        + "<pre>ژانر: فوتبال\nسوال: کدام کشور قهرمان جام جهانی 2022 شد؟\nA) فرانسه\nB) آرژانتین\nC) برزیل\nD) آلمان\nجواب: B\n---\nژانر: تکنولوژی\nسوال: HTML مخفف چیست؟\nA) HyperText Markup Language\nB) HighText Machine Language\nC) Hyper Tool Multi Language\nD) Home Tool Markup Language\nجواب: A</pre>\n"
        + "لازم نیست q_0001 یا شماره سوال بنویسی؛ اگر آرایه یا فرم متنی بفرستی، بات شماره/شناسه سوال‌ها را خودش می‌سازد. "
        + "می‌توانی JSON یا فرم متنی را در چند پیام پشت‌سرهم بفرستی یا فایل .json/.txt ارسال کنی. "
        + "وقتی تمام شد، دستور /done را بفرست. برای لغو /cancel یا دکمه انصراف."
    )


def _pick(obj: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in obj:
            return obj[k]
    lowered = {str(k).lower(): v for k, v in obj.items()}
    for k in keys:
        if k.lower() in lowered:
            return lowered[k.lower()]
    return None


def _extract_options(raw: Any) -> list[str] | None:
    if isinstance(raw, dict):
        vals = []
        for key in ("A", "B", "C", "D"):
            val = raw.get(key) or raw.get(key.lower())
            if val is None:
                return None
            vals.append(str(val).strip())
        return vals
    if isinstance(raw, list) and len(raw) >= 4:
        return [str(x).strip() for x in raw[:4]]
    return None


def _extract_correct(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int) and raw in (1, 2, 3, 4):
        return raw
    text = str(raw).strip().upper()
    return CORRECT_MAP.get(text)


def _iter_question_objects(data: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(data, list):
        pairs = []
        for i, x in enumerate(data):
            if isinstance(x, dict):
                pairs.append((f"item_{i+1}", x))
            else:
                pairs.append((f"item_{i+1}", {"__invalid__": x}))
        return pairs
    if isinstance(data, dict):
        if all(isinstance(v, dict) for v in data.values()):
            return [(str(k), v) for k, v in data.items()]
        return [("item_1", data)]
    return []


def _quote_unquoted_keys(payload: str) -> str:
    # Lenient support for AI-generated JS-like objects: {q_1: {...}, options: {A: "..."}}
    return re.sub(r'([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', payload)


def _line_value(block: str, labels: tuple[str, ...]) -> str | None:
    pattern = r"(?im)^\s*(?:" + "|".join(re.escape(x) for x in labels) + r")\s*\d*\s*[:：]\s*(.+?)\s*$"
    m = re.search(pattern, block)
    return m.group(1).strip() if m else None


def _option_value(block: str, label: str) -> str | None:
    aliases = {"A": "A|a|الف", "B": "B|b|ب", "C": "C|c|ج", "D": "D|d|د"}[label]
    m = re.search(rf"(?im)^\s*(?:{aliases})\s*[\)\].:：-]\s*(.+?)\s*$", block)
    return m.group(1).strip() if m else None


def _split_text_blocks(payload: str) -> list[tuple[str, str]]:
    text = extract_json_text(payload).strip()
    delimiter_parts = [p.strip() for p in re.split(r"(?m)^\s*-{3,}\s*$", text) if p.strip()]
    if len(delimiter_parts) > 1:
        return [(f"item_{i+1}", p) for i, p in enumerate(delimiter_parts)]
    starts = [m.start() for m in re.finditer(r"(?im)^\s*(?:سوال|question)\s*\d*\s*[:：]", text)]
    if len(starts) > 1:
        block_starts: list[int] = []
        for start in starts:
            prev_newline = text.rfind("\n", 0, start)
            prev_prev_newline = text.rfind("\n", 0, prev_newline) if prev_newline != -1 else -1
            maybe_genre = text[prev_prev_newline + 1:prev_newline].strip() if prev_newline != -1 else ""
            block_starts.append(prev_prev_newline + 1 if re.match(r"(?i)^\s*(ژانر|دسته|category|genre)\s*[:：]", maybe_genre) else start)
        blocks: list[tuple[str, str]] = []
        for i, block_start in enumerate(block_starts):
            end = block_starts[i + 1] if i + 1 < len(block_starts) else len(text)
            blocks.append((f"item_{i+1}", text[block_start:end].strip("\n- \t")))
        return blocks
    return [("item_1", text)] if text else []


def parse_text_questions(payload: str, valid_genres: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    blocks = _split_text_blocks(payload)
    accepted: list[dict[str, Any]] = []
    rejected: list[str] = []
    if not blocks or not any(looks_like_bulk_text(block) for _, block in blocks):
        return [], ["فرم متنی قابل تشخیص نیست؛ هر سوال باید خط «سوال: ...» داشته باشد."]
    for key, block in blocks:
        question = _line_value(block, ("سوال", "question", "پرسش"))
        genre = _line_value(block, ("ژانر", "دسته", "category", "genre"))
        correct_raw = _line_value(block, ("جواب", "پاسخ", "correct", "answer"))
        options = [_option_value(block, x) for x in ("A", "B", "C", "D")]
        if not question:
            rejected.append(f"{key}: خط سوال پیدا نشد. نمونه: سوال: متن سوال")
            continue
        if not genre or genre not in valid_genres:
            rejected.append(f"{key}: ژانر نامعتبر است: {genre!r}. ژانر باید دقیقاً یکی از لیست رسمی باشد")
            continue
        if any(not x for x in options):
            rejected.append(f"{key}: هر چهار گزینه باید با A) B) C) D) یا الف) ب) ج) د) نوشته شوند")
            continue
        correct = _extract_correct(correct_raw)
        if correct is None:
            rejected.append(f"{key}: جواب/پاسخ صحیح نامعتبر است؛ A/B/C/D یا 1..4 قابل قبول است")
            continue
        accepted.append({"question": question, "options": [str(x) for x in options], "correct": correct, "genre": genre})
    return ([], rejected) if rejected else (accepted, [])


def parse_bulk_questions(payload: str, valid_genres: list[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    valid = set(valid_genres or CANONICAL_GENRES)
    accepted: list[dict[str, Any]] = []
    rejected: list[str] = []
    payload = extract_json_text(payload)
    try:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = json.loads(_quote_unquoted_keys(payload))
    except json.JSONDecodeError as exc:
        text_items, text_errors = parse_text_questions(payload, valid)
        if text_items:
            return text_items, []
        logger.exception("Bulk JSON parse failed")
        if looks_like_bulk_text(payload):
            return [], text_errors
        return [], [f"JSON/فرم متنی خراب است: خط {exc.lineno} ستون {exc.colno} — {exc.msg}"]

    items = _iter_question_objects(data)
    if not items:
        return [], ["ساختار JSON قابل تشخیص نیست؛ باید آرایه‌ای از سوال‌ها یا آبجکت q_xxx باشد."]

    for key, obj in items:
        if "__invalid__" in obj:
            rejected.append(f"{key}: آیتم باید آبجکت سوال باشد")
            continue
        question = _pick(obj, QUESTION_KEYS)
        genre = _pick(obj, GENRE_KEYS)
        options_raw = _pick(obj, OPTIONS_KEYS)
        correct_raw = _pick(obj, CORRECT_KEYS)
        if not question or not str(question).strip():
            rejected.append(f"{key}: متن سوال خالی یا نامعتبر است")
            continue
        if not genre or str(genre).strip() not in valid:
            rejected.append(f"{key}: ژانر نامعتبر است: {genre!r}. ژانر باید دقیقاً یکی از لیست رسمی باشد")
            continue
        options = _extract_options(options_raw)
        if not options or any(not o for o in options):
            rejected.append(f"{key}: گزینه‌ها باید چهار مورد A/B/C/D یا لیست 4تایی باشند")
            continue
        correct = _extract_correct(correct_raw)
        if correct is None:
            rejected.append(f"{key}: گزینه‌ی صحیح نامعتبر است؛ A/B/C/D یا 1..4 قابل قبول است")
            continue
        accepted.append({
            "question": str(question).strip(),
            "options": options,
            "correct": correct,
            "genre": str(genre).strip(),
        })
    if rejected:
        return [], rejected
    return accepted, []


def format_bulk_report(success: int, rejected: list[str]) -> str:
    if rejected:
        text = f"📥 گزارش افزودن Bulk سوال\n\n❌ کل batch رد شد و هیچ سوالی درج نشد.\nتعداد خطا: {len(rejected)}"
        text += "\n\nدلایل رد:\n" + "\n".join(f"- {r}" for r in rejected[:60])
        if len(rejected) > 60:
            text += f"\n... و {len(rejected) - 60} مورد دیگر"
        return text
    return f"📥 گزارش افزودن Bulk سوال\n\n✅ همه سوالات معتبر بودند و {success} سوال با موفقیت اضافه شد."
