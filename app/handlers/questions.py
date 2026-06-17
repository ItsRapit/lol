import logging
from aiogram import Bot, Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from app.db import Database, now_iso
from app.keyboards import review_question_keyboard, cancel_keyboard, main_menu
from app.states import QuestionSubmit
from app.time_utils import jalali_datetime

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "➕ ثبت سوال")
async def submit_entry(message: Message, db: Database, state: FSMContext) -> None:
    try:
        limit = await db.get_int('daily_question_limit', 5)
        used = await db.daily_submissions_count(message.from_user.id)
        if used >= limit:
            await message.answer(f"سقف ثبت سوال امروز شما ({limit}) تکمیل شده است.")
            return
        await state.set_state(QuestionSubmit.text)
        await message.answer("ثبت سوال:", reply_markup=ReplyKeyboardRemove())
        await message.answer("متن سوال را ارسال کن.", reply_markup=cancel_keyboard())
    except Exception:
        logger.exception("Submit entry failed")
        await message.answer("خطا.")


@router.message(QuestionSubmit.text, F.text)
async def q_text(message: Message, state: FSMContext) -> None:
    await state.update_data(text=message.text)
    await state.set_state(QuestionSubmit.option1)
    await message.answer("گزینه 1:")


@router.message(QuestionSubmit.option1, F.text)
async def q_o1(message: Message, state: FSMContext) -> None:
    await state.update_data(option1=message.text)
    await state.set_state(QuestionSubmit.option2)
    await message.answer("گزینه 2:")


@router.message(QuestionSubmit.option2, F.text)
async def q_o2(message: Message, state: FSMContext) -> None:
    await state.update_data(option2=message.text)
    await state.set_state(QuestionSubmit.option3)
    await message.answer("گزینه 3:")


@router.message(QuestionSubmit.option3, F.text)
async def q_o3(message: Message, state: FSMContext) -> None:
    await state.update_data(option3=message.text)
    await state.set_state(QuestionSubmit.option4)
    await message.answer("گزینه 4:")


@router.message(QuestionSubmit.option4, F.text)
async def q_o4(message: Message, state: FSMContext) -> None:
    await state.update_data(option4=message.text)
    await state.set_state(QuestionSubmit.correct)
    await message.answer("شماره گزینه صحیح را بفرست (1 تا 4):")


@router.message(QuestionSubmit.correct, F.text)
async def q_correct(message: Message, state: FSMContext, db: Database) -> None:
    try:
        n = int(message.text.strip())
        if n not in [1, 2, 3, 4]:
            raise ValueError
        await state.update_data(correct=n)
        await state.set_state(QuestionSubmit.genre)
        genres = "، ".join(await db.all_genres())
        await message.answer(f"ژانر/دسته‌بندی سوال را بنویس. ژانرهای مجاز:\n{genres}", reply_markup=cancel_keyboard())
    except ValueError:
        await message.answer("فقط عدد 1 تا 4 قابل قبول است.")


@router.message(QuestionSubmit.genre, F.text)
async def q_genre(message: Message, db: Database, state: FSMContext, bot: Bot, admin_review_channel_id: int | None) -> None:
    try:
        genre = message.text.strip()
        valid_genres = await db.all_genres()
        if genre not in valid_genres:
            await message.answer("ژانر نامعتبر است. لطفاً دقیقاً یکی از ژانرهای مجاز را بدون کم/زیاد کردن بنویس:\n" + "، ".join(valid_genres))
            return
        d = await state.get_data()
        opts = [d['option1'], d['option2'], d['option3'], d['option4']]
        qid = await db.submit_question(message.from_user.id, d['text'], opts, d['correct'], genre)
        if admin_review_channel_id:
            text = (
                f"➕ سوال پیشنهادی #{qid}\nSubmitter: <code>{message.from_user.id}</code>\nGenre: {genre}\nDate: {jalali_datetime(now_iso())}\n\n"
                f"{d['text']}\n1) {opts[0]}\n2) {opts[1]}\n3) {opts[2]}\n4) {opts[3]}\nCorrect: {d['correct']}"
            )
            await bot.send_message(admin_review_channel_id, text, reply_markup=review_question_keyboard(qid))
        await state.clear()
        await message.answer("سوال ثبت شد و بعد از تایید ادمین وارد بازی می‌شود.", reply_markup=main_menu(await db.is_admin(message.from_user.id)))
    except Exception:
        logger.exception("Submit save failed")
        await message.answer("خطا در ثبت سوال.")


@router.callback_query(F.data.startswith("qrev:"))
async def q_review(call: CallbackQuery, db: Database, bot: Bot) -> None:
    logger.info("Question review callback received: data=%s from=%s chat=%s", call.data, call.from_user.id if call.from_user else None, call.message.chat.type if call.message else None)
    try:
        if not await db.is_admin(call.from_user.id):
            await call.answer("دسترسی ندارید.", show_alert=True); return
        _, action, qid_s = call.data.split(":")
        approve = action == 'approve'
        q = await db.review_question(int(qid_s), call.from_user.id, approve)
        if not q:
            await call.answer("سوال قبلاً بررسی شده یا وجود ندارد.", show_alert=True); return
        await db.log_admin(call.from_user.id, f"question_{action}", qid_s)
        if q['submitted_by']:
            if approve:
                reward = await db.get_int("question_approval_reward_coins", 20)
                if reward > 0:
                    await db.change_coins(q['submitted_by'], reward, "question_approved_reward")
                updated = await db.get_user(q['submitted_by'])
                await bot.send_message(q['submitted_by'], f"✅ سوال پیشنهادی شما تایید شد.\n🎁 پاداش: {reward} سکه\nموجودی فعلی: {updated['coins'] if updated else '-'} سکه")
            else:
                await bot.send_message(q['submitted_by'], "❌ سوال پیشنهادی شما رد شد.")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("ثبت شد.")
    except Exception:
        logger.exception("Question review failed")
        await call.answer("خطا", show_alert=True)
