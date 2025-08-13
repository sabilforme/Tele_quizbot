import os
import re
import json
import tempfile
from typing import Dict

from telegram import Update, Poll, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)

from qa_builder import build_quiz_from_text

LANG = os.getenv("LANG", "ar")
WELCOME_AR = (
    "أهلًا! أرسل ملف المحاضرات (PDF/DOCX/TXT) وسأولّد أسئلة اختيار من متعدد وصح/خطأ باستخدام الذكاء الاصطناعي.\n\n"
    "الأوامر: /start /cancel"
)
WELCOME_EN = (
    "Hi! Send a PDF/DOCX/TXT and I'll generate MCQ + True/False questions using AI.\n\n"
    "Commands: /start /cancel"
)

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", 12))

SESSIONS: Dict[int, Dict] = {}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_AR if LANG == "ar" else WELCOME_EN)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in SESSIONS:
        del SESSIONS[chat_id]
        await update.message.reply_text("تم إلغاء الاختبار الحالي ✅")
    else:
        await update.message.reply_text("لا يوجد اختبار جارٍ الآن.")


def _clean_text(t: str) -> str:
    t = t.replace("\u200f", " ").replace("\u200e", " ")
    t = re.sub(r"[\t\xa0]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


PDF_OK = True
DOCX_OK = True
try:
    from pdfminer.high_level import extract_text as pdf_extract_text
except Exception:
    PDF_OK = False
try:
    import docx
except Exception:
    DOCX_OK = False


def extract_from_pdf(path: str) -> str:
    if not PDF_OK:
        return ""
    return pdf_extract_text(path)


def extract_from_docx(path: str) -> str:
    if not DOCX_OK:
        return ""
    d = docx.Document(path)
    return "\n".join(p.text for p in d.paragraphs)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        return
    size_mb = doc.file_size / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        await update.message.reply_text(f"الحجم كبير ({size_mb:.1f}MB). أرسل ملف ≤ {MAX_FILE_MB}MB.")
        return

    filename = doc.file_name or "file"
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in [".pdf", ".txt", ".docx"]:
        await update.message.reply_text("الرجاء إرسال PDF أو DOCX أو TXT.")
        return

    await update.message.reply_chat_action(constants.ChatAction.UPLOAD_DOCUMENT)

    with tempfile.TemporaryDirectory() as td:
        local = os.path.join(td, filename)
        tgfile = await context.bot.get_file(doc.file_id)
        await tgfile.download_to_drive(local)

        text = ""
        try:
            if suffix == ".pdf":
                text = extract_from_pdf(local)
            elif suffix == ".docx":
                text = extract_from_docx(local)
            else:
                text = open(local, "r", encoding="utf-8", errors="ignore").read()
        except Exception:
            text = ""

    text = _clean_text(text)
    if not text or len(text) < 600:
        await update.message.reply_text("تعذر قراءة النص من الملف. تأكد أنه ليس صورًا ممسوحة أو أرسل نصًا أطول.")
        return

    await update.message.reply_text("جاري توليد الأسئلة بالذكاء الاصطناعي… ⏳")

    questions = await build_quiz_from_text(text)
    if not questions:
        await update.message.reply_text("تعذّر توليد أسئلة كافية. حاول ملفًا آخر.")
        return

    chat_id = update.effective_chat.id
    SESSIONS[chat_id] = {"questions": questions, "index": 0, "score": 0, "answers": {}}

    await send_next_question(update, context)


async def send_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sess = SESSIONS.get(chat_id)
    if not sess:
        await update.message.reply_text("لا يوجد اختبار حاليًا. أرسل ملفًا للبدء.")
        return

    if sess["index"] >= len(sess["questions"]):
        await context.bot.send_message(chat_id=chat_id, text=f"انتهى الاختبار! نتيجتك: {sess['score']}/{len(sess['questions'])} ✅")
        del SESSIONS[chat_id]
        return

    q = sess["questions"][sess["index"]]
    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=q["question"][:255],
        options=q["options"][:10],
        type=Poll.QUIZ,
        correct_option_id=int(q["correct"]),
        is_anonymous=False,
        explanation=("إجابة صحيحة" if LANG == "ar" else "Correct"),
    )
    sess["answers"][msg.poll.id] = int(q["correct"])
    sess["index"] += 1


async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    for chat_id, sess in list(SESSIONS.items()):
        if answer.poll_id in sess["answers"]:
            correct = sess["answers"][answer.poll_id]
            if answer.option_ids and answer.option_ids[0] == correct:
                sess["score"] += 1
            try:
                await context.bot.send_message(chat_id=chat_id, text="سجلنا إجابتك ✅")
            except Exception:
                pass
            break


def main():
    token = os.getenv("7761296155:AAFhJXXxLc5WxOWw5VN0Q82JrzywgFiI4_Q")
    if not token:
        raise SystemExit("Set BOT_TOKEN env var")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot running…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
