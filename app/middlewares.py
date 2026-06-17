from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from app.db import Database
from app.keyboards import MAIN_MENU_TEXTS

logger = logging.getLogger(__name__)


class AccessGuardMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[Any, dict[str, Any]], Awaitable[Any]], event: Any, data: dict[str, Any]) -> Any:
        db: Database | None = data.get("db")
        try:
            if isinstance(event, Message):
                if event.chat.type != "private":
                    if event.text and event.text.startswith("/start"):
                        await event.answer("برای بازی باید از طریق پیوی (چت خصوصی) با بات وارد شوید.")
                    return None
                if event.text and event.text.split()[0].split('@')[0] == "/version":
                    return await handler(event, data)
                if db and event.from_user and await db.get_int("maintenance_mode", 0) == 1 and not await db.is_admin(event.from_user.id):
                    await event.answer(await db.get_setting("maintenance_text", "بات موقتاً در حال تعمیر است."))
                    return None
                if db and event.from_user and event.text in MAIN_MENU_TEXTS:
                    duel = await db.active_duel_for_user(event.from_user.id)
                    if duel and duel["status"] in {"waiting", "invite_waiting", "genre_selection", "playing"}:
                        await event.answer("شما در حال بازی هستید؛ لطفاً ابتدا دوئل را تمام کنید.")
                        return None
            elif isinstance(event, CallbackQuery):
                logger.info("Callback received: data=%s from=%s chat_type=%s", event.data, event.from_user.id if event.from_user else None, event.message.chat.type if event.message else None)
                if event.message and event.message.chat.type != "private":
                    allowed_prefixes = ("tx:", "qrev:")
                    if not (event.data or "").startswith(allowed_prefixes):
                        return None
                if db and event.from_user and await db.get_int("maintenance_mode", 0) == 1 and not await db.is_admin(event.from_user.id):
                    await event.answer(await db.get_setting("maintenance_text", "بات موقتاً در حال تعمیر است."), show_alert=True)
                    return None
        except Exception:
            logger.exception("Access guard failed")
        return await handler(event, data)


# Backward-compatible name used by older main.py imports.
ActiveDuelMenuGuardMiddleware = AccessGuardMiddleware
