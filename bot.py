import os
import re
import json
import tempfile
from typing import Dict

from telegram import Update, Poll, constants, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from qa_builder import build_quiz_from_text
from ingest import extract_text_any

LANG_UI_DEFAULT = os.getenv("LANG", "ar")  # واجهة البوت فقط
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", 16))

SESSIONS: Dict[int, Dict] = {}

WELCOME_AR = (
    "أهلًا! أرسل ملف (PDF/DOCX/PPTX/TXT/صور) وسأحوّله لأسئلة قوية بالذكاء الاصطناعي.\n\n"
    "الأوامر: /start /cancel"
)
WELCOME_EN = (
    "Hi! Send a PDF/DOCX/PPTX/TXT/Image and I'll turn it into strong exam questions using AI.\n\n"
    "Commands: /start /cancel"
)


def _ui(text_ar: str, text_en: str) -> str:
    return text_ar if LANG_UI_DEFAULT == "ar" else text_en


def _clean_text(t: str) -> str:
    t = t.replace("\u200f", " ").replace("\u200e", " ")
    t = re.sub(r"[\t\xa0]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_ui(WELCOME_AR, WELCOME_EN))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in SESSIONS:
        del SESSIONS[chat_id]
        await update.message.reply_text(_ui("تم إلغاء الاختبار الحالي ✅", "Current quiz canceled ✅"))
    else:
        await update.message.reply_text(_ui("لا يوجد اختبار جارٍ الآن.", "No active quiz."))


# ---------- استقبال الملفات ثم اختيار لغة المحتوى ----------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    doc = update.message.document or update.message.photo[-1] if update.message.photo else None
    # دعم الصور المرسلة كـ photo
    if not doc and update.message.document is None and update.message.photo:
        # photo تُعامل هنا لاحقًا
        pass

    # لو جت صورة
    if update.message.photo:
        # حمّل أعلى دقة
        photo = update.message.photo[-1]
        tgfile = await context.bot.get_file(photo.file_id)
        file_bytes = await tgfile.download_as_bytearray()
        filename = "image.jpg"
        suffix = ".jpg"
    else:
        if not update.message.document:
            return
        d = update.message.document
        size_mb = d.file_size / (1024 * 1024)
        if size_mb > MAX_FILE_MB:
            await update.message.reply_text(_ui(f"الحجم كبير ({size_mb:.1f}MB). أرسل ملف ≤ {MAX_FILE_MB}MB.", f"File too large ({size_mb:.1f}MB). Max {MAX_FILE_MB}MB."))
            return
        filename = d.file_name or "file"
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in [".pdf", ".txt", ".docx", ".pptx", ".jpg", ".jpeg", ".png", ".tif", ".tiff"]:
            await update.message.reply_text(_ui("الرجاء إرسال PDF/DOCX/PPTX/TXT/صورة.", "Please send a PDF/DOCX/PPTX/TXT/Image."))
            return
        tgfile = await context.bot.get_file(d.file_id)
        file_bytes = await tgfile.download_as_bytearray()

    # خزّن الملف مؤقتًا بالذاكرة واطلب اللغة
    SESSIONS[chat_id] = {
        "stage": "await_lang",
        "filename": filename,
        "suffix": suffix,
        "file_bytes": bytes(file_bytes),
    }

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("العربية", callback_data="lang_ar")],
        [InlineKeyboardButton("English", callback_data="lang_en")],
    ])
    await update.message.reply_text(_ui("اختر لغة محتوى الملف:", "Choose the file content language:"), reply_markup=kb)


async def choose_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    sess = SESSIONS.get(chat_id)
    if not sess or sess.get("stage") != "await_lang":
        await query.edit_message_text(_ui("لا يوجد ملف قيد المعالجة.", "No pending file."))
        return

    lang = "ar" if query.data == "lang_ar" else "en"
    sess["lang"] = lang
    sess["stage"] = "processing"

    await query.edit_message_text(_ui("جاري تحليل الملف وإعداده… ⏳", "Analyzing the file… ⏳"))

    # اكتب الملف مؤقتًا على القرص
    with tempfile.NamedTemporaryFile(delete=False, suffix=sess["suffix"]) as f:
        f.write(sess["file_bytes"])
        tmp_path = f.name

    try:
        text = await extract_text_any(tmp_path, sess["suffix"], lang)
    except Exception:
        text = ""
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    text = _clean_text(text)
    if not text or len(text) < 400:
        await context.bot.send_message(chat_id=chat_id, text=_ui("تعذر استخراج نص كافٍ حتى بعد OCR. جرّب ملفًا أوضح.", "Couldn't extract enough text (even with OCR). Try a clearer file."))
        SESSIONS.pop(chat_id, None)
        return

    await context.bot.send_message(chat_id=chat_id, text=_ui("جاري توليد أسئلة قوية بالذكاء الاصطناعي… ⏳", "Generating strong questions with AI… ⏳"))

    questions = await build_quiz_from_text(text, lang=lang, target_total=40)
    if not questions:
        await context.bot.send_message(chat_id=chat_id, text=_ui("تعذّر توليد أسئلة كافية. حاول ملفًا آخر.", "Failed to generate enough questions. Try another file."))
        SESSIONS.pop(chat_id, None)
        return

    sess.update({"questions": questions, "index": 0, "score": 0, "answers": {}, "stage": "quiz"})
    await send_next_question(chat_id, context)


async def send_next_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    sess = SESSIONS.get(chat_id)
    if not sess or sess.get("stage") != "quiz":
        return

    if sess["index"] >= len(sess["questions"]):
        await context.bot.send_message(chat_id=chat_id, text=_ui(f"انتهى الاختبار! نتيجتك: {sess['score']}/{len(sess['questions'])} ✅", f"Done! Your score: {sess['score']}/{len(sess['questions'])} ✅"))
        SESSIONS.pop(chat_id, None)
        return

    q = sess["questions"][sess["index"]]
    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=q["question"][:255],
        options=q["options"][:10],
        type=Poll.QUIZ,
        correct_option_id=int(q["correct"]),
        is_anonymous=False,
        explanation=_ui("إجابة صحيحة", "Correct"),
    )
    sess["answers"][msg.poll.id] = int(q["correct"])
    sess["index"] += 1


async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    for chat_id, sess in list(SESSIONS.items()):
        if answer.poll_id in sess.get("answers", {}):
            correct = sess["answers"][answer.poll_id]
            if answer.option_ids and answer.option_ids[0] == correct:
                sess["score"] += 1
            # بعد كل إجابة نرسل السؤال التالي
            await send_next_question(chat_id, context)
            break


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN env var")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_(ar|en)$"))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot running…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
