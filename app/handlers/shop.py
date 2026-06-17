import logging
from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from app.db import Database
from app.keyboards import (
    shop_keyboard, review_tx_keyboard, shop_sections_keyboard, cancel_keyboard, main_menu,
    discount_apply_keyboard,
)
from app.states import ShopReceipt
from app.payments import get_payment_provider
from app.time_utils import jalali_datetime

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "🛒 فروشگاه")
async def shop_entry(message: Message) -> None:
    await message.answer("فروشگاه:", reply_markup=ReplyKeyboardRemove())
    await message.answer("نوع بسته را انتخاب کن:", reply_markup=shop_sections_keyboard())


@router.callback_query(F.data == "shop_back:sections")
async def shop_back_sections(call: CallbackQuery) -> None:
    await call.message.edit_text("نوع بسته را انتخاب کن:", reply_markup=shop_sections_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("shop_section:"))
async def shop_section(call: CallbackQuery, db: Database) -> None:
    try:
        package_type = call.data.split(":", 1)[1]
        packages = await db.shop_packages(package_type)
        title = "بسته‌های سکه" if package_type == "coins" else "بسته‌های سطح/XP"
        if not packages:
            await call.message.edit_text(f"{title}\nفعلاً بسته‌ای در این بخش فعال نیست.", reply_markup=shop_sections_keyboard())
            await call.answer()
            return
        await call.message.edit_text(f"{title}: یکی را انتخاب کن:", reply_markup=shop_keyboard(packages, package_type))
        await call.answer()
    except Exception:
        logger.exception("Shop section failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("shop:"))
async def package_selected(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        package_id = int(call.data.split(":")[1])
        p = await db.get_package(package_id)
        if not p or not p['is_active']:
            await call.answer("بسته پیدا نشد.", show_alert=True)
            return
        tx_id = await db.create_shop_tx(call.from_user.id, package_id)
        await state.clear()
        await call.message.answer(
            f"بسته: {p['title']}\nسکه: {p['coins']} | XP: {p['xp']}\nقیمت: {p['price_label']}\n\n"
            "اگر کد تخفیف داری وارد کن، وگرنه ادامه بدون تخفیف را بزن.",
            reply_markup=discount_apply_keyboard(tx_id),
        )
        await call.answer()
    except Exception:
        logger.exception("Package select failed")
        await call.answer("خطا", show_alert=True)


@router.callback_query(F.data.startswith("discount_apply:"))
async def discount_apply_start(call: CallbackQuery, state: FSMContext) -> None:
    tx_id = int(call.data.split(":")[1])
    await state.set_state(ShopReceipt.waiting_discount)
    await state.update_data(tx_id=tx_id)
    await call.message.answer("کد تخفیف را وارد کن:", reply_markup=cancel_keyboard())
    await call.answer()


@router.message(ShopReceipt.waiting_discount, F.text)
async def discount_apply_text(message: Message, db: Database, state: FSMContext) -> None:
    try:
        data = await state.get_data()
        tx_id = int(data['tx_id'])
        ok, result = await db.apply_discount_to_tx(tx_id, message.text.strip())
        if not ok:
            await message.answer(result + "\nدوباره کد بفرست یا /cancel بزن.")
            return
        await state.clear()
        await message.answer(f"✅ کد تخفیف اعمال شد. مبلغ نهایی: <b>{result}</b>")
        await send_payment_instructions(message, tx_id, db, state)
    except Exception:
        logger.exception("Apply discount failed")
        await message.answer("خطا در اعمال کد تخفیف.")


@router.callback_query(F.data.startswith("pay:start:"))
async def payment_start_callback(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    try:
        tx_id = int(call.data.split(":")[2])
        await send_payment_instructions(call.message, tx_id, db, state)
        await call.answer()
    except Exception:
        logger.exception("Payment start failed")
        await call.answer("خطا", show_alert=True)


async def send_payment_instructions(message: Message, tx_id: int, db: Database, state: FSMContext) -> None:
    tx = await db.get_tx(tx_id)
    if not tx:
        await message.answer("تراکنش پیدا نشد.")
        return
    await db.mark_tx_ready_to_pay(tx_id)
    provider = await get_payment_provider(db)
    instructions = await provider.instructions(db, tx)
    await state.set_state(ShopReceipt.waiting_receipt)
    await state.update_data(tx_id=tx_id)
    await message.answer(instructions.text, reply_markup=cancel_keyboard())


@router.message(ShopReceipt.waiting_receipt)
async def receive_receipt(message: Message, db: Database, state: FSMContext, bot: Bot, admin_review_channel_id: int | None) -> None:
    try:
        data = await state.get_data()
        tx_id = int(data['tx_id'])
        rtype, text, file_id = 'text', message.text or message.caption, None
        if message.photo:
            rtype = 'photo'
            file_id = message.photo[-1].file_id
        elif not text:
            await message.answer("لطفاً رسید را به‌صورت عکس یا متن بفرست.")
            return
        await db.save_receipt(tx_id, rtype, text, file_id)
        tx = await db.get_tx(tx_id)
        if admin_review_channel_id and tx:
            caption = (
                f"🧾 رسید خرید #{tx_id}\nUser: <code>{tx['user_id']}</code>\n"
                f"Package: {tx['title']}\nCoins: {tx['coins']} | XP: {tx['xp']}\n"
                f"Original: {tx['original_price_label'] or tx['price_label']}\nFinal: {tx['final_price_label'] or tx['price_label']}\n"
                f"Discount ID: {tx['discount_code_id'] or '-'}\nDate: {jalali_datetime(tx['created_at'])}\nText: {text or '-'}"
            )
            if rtype == 'photo' and file_id:
                await bot.send_photo(admin_review_channel_id, file_id, caption=caption, reply_markup=review_tx_keyboard(tx_id))
            else:
                await bot.send_message(admin_review_channel_id, caption, reply_markup=review_tx_keyboard(tx_id))
        await state.clear()
        await message.answer("رسید ثبت شد و برای ادمین‌ها ارسال شد. بعد از بررسی خبر می‌دهیم.", reply_markup=main_menu(await db.is_admin(message.from_user.id)))
    except Exception:
        logger.exception("Receipt receive failed")
        await message.answer("خطا در ثبت رسید.")


@router.callback_query(F.data.startswith("tx:"))
async def review_tx(call: CallbackQuery, db: Database, bot: Bot) -> None:
    logger.info("TX review callback received: data=%s from=%s chat=%s", call.data, call.from_user.id if call.from_user else None, call.message.chat.type if call.message else None)
    try:
        if not await db.is_admin(call.from_user.id):
            await call.answer("دسترسی ندارید.", show_alert=True); return
        _, action, tx_s = call.data.split(":")
        approve = action == 'approve'
        tx = await db.review_tx(int(tx_s), call.from_user.id, approve)
        if not tx:
            await call.answer("قبلاً بررسی شده یا پیدا نشد.", show_alert=True); return
        await db.log_admin(call.from_user.id, f"tx_{action}", str(tx_s))
        if approve:
            updated_user = await db.get_user(tx['user_id'])
            added_parts = []
            if tx['coins']:
                added_parts.append(f"{tx['coins']} سکه")
            if tx['xp']:
                added_parts.append(f"{tx['xp']} XP")
            added_text = " + ".join(added_parts) or "بسته"
            balance_text = f"موجودی فعلی: {updated_user['coins']} سکه | XP: {updated_user['xp']}" if updated_user else ""
            await bot.send_message(tx['user_id'], f"✅ پرداخت تایید شد.\n{added_text} به حسابت اضافه شد.\n{balance_text}")
        else:
            await bot.send_message(tx['user_id'], "❌ رسید خرید شما رد شد.")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("ثبت شد.")
    except Exception:
        logger.exception("TX review failed")
        await call.answer("خطا", show_alert=True)
