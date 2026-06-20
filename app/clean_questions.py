from __future__ import annotations

import logging
from typing import Any
from app.db import Database

logger = logging.getLogger(__name__)


async def get_filter_words(db: Database) -> list[str]:
    raw = await db.get_setting("question_filter_words", "ربات,ماشین,بازی")
    return [w.strip() for w in raw.split(",") if w.strip()]


async def get_duplicate_stats(db: Database) -> dict[str, int]:
    rows = await db.fetchall("""
        SELECT text, COUNT(*) cnt, MIN(id) keep_id
        FROM questions
        GROUP BY text
        HAVING COUNT(*) > 1
    """)
    return {"groups": len(rows), "to_delete": sum(int(r["cnt"]) - 1 for r in rows)}


async def get_keyword_stats(db: Database, keywords: list[str]) -> dict[str, Any]:
    total = 0
    details = []
    for kw in keywords:
        rows = await db.fetchall("SELECT id FROM questions WHERE text LIKE ? ORDER BY id", (f"%{kw}%",))
        if len(rows) > 1:
            total += len(rows) - 1
            details.append(f"{kw}: {len(rows)} یافت شد، {len(rows)-1} قابل حذف")
    return {"to_delete": total, "details": details}


async def clean_duplicate_questions(db: Database) -> dict[str, int]:
    rows = await db.fetchall("""
        SELECT text, COUNT(*) cnt, MIN(id) keep_id
        FROM questions
        GROUP BY text
        HAVING COUNT(*) > 1
    """)
    deleted = 0
    for r in rows:
        cur = await db.execute_write("DELETE FROM questions WHERE text=? AND id<>?", (r["text"], r["keep_id"]))
        deleted += cur.rowcount if cur.rowcount is not None else int(r["cnt"]) - 1
    return {"groups_found": len(rows), "deleted": deleted}


async def clean_keyword_questions(db: Database, keywords: list[str]) -> dict[str, Any]:
    deleted = 0
    details = []
    for kw in keywords:
        rows = await db.fetchall("SELECT id FROM questions WHERE text LIKE ? ORDER BY id", (f"%{kw}%",))
        if len(rows) <= 1:
            continue
        ids = [r["id"] for r in rows[1:]]
        placeholders = ",".join("?" for _ in ids)
        cur = await db.execute_write(f"DELETE FROM questions WHERE id IN ({placeholders})", ids)
        count = cur.rowcount if cur.rowcount is not None else len(ids)
        deleted += count
        details.append(f"کلمه «{kw}»: {len(rows)} یافت شد، {count} حذف شد")
    return {"deleted": deleted, "details": details}
