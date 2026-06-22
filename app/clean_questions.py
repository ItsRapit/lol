from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any
from app.db import Database

logger = logging.getLogger(__name__)
SIMILARITY_THRESHOLD = 0.88


async def get_filter_words(db: Database) -> list[str]:
    raw = await db.get_setting("question_filter_words", "ربات,ماشین,بازی")
    return [w.strip() for w in raw.split(",") if w.strip()]


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _correct_answer(row: Any) -> str:
    options = {
        "1": row["option1"],
        "2": row["option2"],
        "3": row["option3"],
        "4": row["option4"],
    }
    return str(options.get(str(row["correct_option"]), "")).strip().lower()


async def _exact_duplicate_delete_ids(db: Database) -> tuple[int, int, set[int]]:
    rows = await db.fetchall("""
        SELECT text, COUNT(*) cnt, MIN(id) keep_id
        FROM questions
        GROUP BY text
        HAVING COUNT(*) > 1
    """)
    delete_ids: set[int] = set()
    for r in rows:
        dups = await db.fetchall("SELECT id FROM questions WHERE text=? AND id<>?", (r["text"], r["keep_id"]))
        delete_ids.update(int(x["id"]) for x in dups)
    return len(rows), len(delete_ids), delete_ids


async def _similar_duplicate_delete_ids(db: Database, exclude_ids: set[int] | None = None) -> tuple[int, int, set[int]]:
    exclude_ids = exclude_ids or set()
    rows = await db.fetchall("SELECT id,text,option1,option2,option3,option4,correct_option FROM questions ORDER BY id ASC")
    questions = [r for r in rows if int(r["id"]) not in exclude_ids]
    visited: set[int] = set()
    delete_ids: set[int] = set()
    groups = 0

    for i, base in enumerate(questions):
        base_id = int(base["id"])
        if base_id in visited or base_id in delete_ids:
            continue
        base_text = _normalized_text(base["text"])
        base_correct = _correct_answer(base)
        group_ids = [base_id]
        for other in questions[i + 1:]:
            other_id = int(other["id"])
            if other_id in visited or other_id in delete_ids:
                continue
            if _correct_answer(other) != base_correct:
                continue
            ratio = SequenceMatcher(None, base_text, _normalized_text(other["text"])).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                group_ids.append(other_id)
        if len(group_ids) > 1:
            groups += 1
            keep_id = min(group_ids)
            for qid in group_ids:
                visited.add(qid)
                if qid != keep_id:
                    delete_ids.add(qid)
    return groups, len(delete_ids), delete_ids


async def get_clean_stats(db: Database) -> dict[str, int]:
    exact_groups, exact_delete_count, exact_delete_ids = await _exact_duplicate_delete_ids(db)
    similar_groups, similar_delete_count, _ = await _similar_duplicate_delete_ids(db, exact_delete_ids)
    return {
        "exact_groups": exact_groups,
        "exact_to_delete": exact_delete_count,
        "similar_groups": similar_groups,
        "similar_to_delete": similar_delete_count,
        "total_to_delete": exact_delete_count + similar_delete_count,
    }


async def get_duplicate_stats(db: Database) -> dict[str, int]:
    stats = await get_clean_stats(db)
    return {"groups": stats["exact_groups"], "to_delete": stats["exact_to_delete"]}


async def get_keyword_stats(db: Database, keywords: list[str]) -> dict[str, Any]:
    # Kept for compatibility with filterword management; /cleanquestions no longer deletes by keywords.
    total = 0
    details = []
    for kw in keywords:
        rows = await db.fetchall("SELECT id FROM questions WHERE text LIKE ? ORDER BY id", (f"%{kw}%",))
        if len(rows) > 1:
            total += len(rows) - 1
            details.append(f"{kw}: {len(rows)} یافت شد، {len(rows)-1} قابل حذف")
    return {"to_delete": total, "details": details}


async def clean_duplicate_questions(db: Database) -> dict[str, int]:
    exact_groups, exact_count, exact_ids = await _exact_duplicate_delete_ids(db)
    exact_deleted = 0
    if exact_ids:
        placeholders = ",".join("?" for _ in exact_ids)
        cur = await db.execute_write(f"DELETE FROM questions WHERE id IN ({placeholders})", list(exact_ids))
        exact_deleted = cur.rowcount if cur.rowcount is not None else len(exact_ids)

    similar_groups, similar_count, similar_ids = await _similar_duplicate_delete_ids(db)
    similar_deleted = 0
    if similar_ids:
        placeholders = ",".join("?" for _ in similar_ids)
        cur = await db.execute_write(f"DELETE FROM questions WHERE id IN ({placeholders})", list(similar_ids))
        similar_deleted = cur.rowcount if cur.rowcount is not None else len(similar_ids)

    return {
        "exact_groups_found": exact_groups,
        "exact_deleted": exact_deleted,
        "similar_groups_found": similar_groups,
        "similar_deleted": similar_deleted,
        "deleted": exact_deleted + similar_deleted,
    }


async def clean_keyword_questions(db: Database, keywords: list[str]) -> dict[str, Any]:
    # Kept for backward compatibility; not used by /cleanquestions per latest cleanup rules.
    return {"deleted": 0, "details": ["پاک‌سازی کلمات فیلتر در /cleanquestions غیرفعال است؛ فقط تکراری عینی و مشابه حذف می‌شود."]}
