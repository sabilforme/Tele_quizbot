import os
import re
import json
import tempfile
from typing import Dict

from telegram import Update, Poll, InlineKeyboardMarkup, InlineKeyboardButton
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

# ================= إعدادات =================
LANG_UI_DEFAULT = os.getenv("LANG", "ar")  # واجهة البوت فقط
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", 16))
ADMIN_ID = 481595387  # ضع رقمك هنا
DATA_FILE = "bot_users.json"

SESSIONS: Dict[int, Dict] = {}

WELCOME_AR = (
    "🎯 **مرحبًا بك في Bashar QuizBot Vip** 🤖✨\n"
    "أرسل **📄 PDF / DOCX / PPTX / TXT / 🖼 صورة**\n"
    "وسيحوّله البوت فورًا إلى **أسئلة اختبار قوية ودقيقة** باستخدام الذكاء الاصطناعي.\n\n"
    "⚡ **أوامر البوت:**\n"
    "`/start` ➡ بدء استخدام البوت\n"
    "`/cancel` ➡ إلغاء العملية\n\n"
    "💡 احصل على أسئلة احترافية في ثوانٍ!"
)

WELCOME_EN = (
    "🎯 **Welcome to Bashar QuizBot Vip** 🤖✨\n"
    "Send a **📄 PDF / DOCX / PPTX / TXT / 🖼 Image** and watch it instantly transform "
    "into **powerful, precise exam questions** using AI.\n\n"
    "⚡ **Bot Commands:**\n"
    "`/start` ➡ Start the bot\n"
    "`/cancel` ➡ Cancel the process\n\n"
    "💡 Get professional-grade questions in seconds!"
)

# ================= إدارة المستخدمين =================
try:
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
        allowed_users = set(data.get("allowed_users", []))
        banned_users = set(data.get("banned_users", []))
        pending_users = set(data.get("pending_users", []))
except FileNotFoundError:
    allowed_users = set()
    banned_users = set()
    pending_users = set()

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "allowed_users": list(allowed_users),
            "banned_users": list(banned_users),
            "pending_users": list(pending_users)
        }, f)

def _ui(text_ar: str, text_en: str) -> str:
    return text_ar if LANG_UI_DEFAULT == "ar" else text_en

def _clean_text(t: str) -> str:
    t = t.replace("\u200f", " ").replace("\u200e", " ")
    t = re.sub(r"[\t\xa0]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

# ================= Decorator للتحقق من المدير =================
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("هذه الميزة للمدير فقط.")
            return
        return await func(update, context)
    return wrapper

# ================= Start + الموافقة =================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in banned_users:
        await update.message.reply_text("تم حظرك من استخدام هذا البوت.")
        return
    if user_id not in allowed_users:
        if user_id not in pending_users:
            pending_users.add(user_id)
            save_data()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ قبول", callback_data=f"approve_{user_id}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")]
            ])
            await context.bot.send_message(chat_id=ADMIN_ID,
                text=f"مستخدم جديد طلب استخدام البوت:\n{update.effective_user.username} (ID: {user_id})",
                reply_markup=kb)
        await update.message.reply_text("في انتظار موافقة المدير لاستخدام البوت.")
        return
    await update.message.reply_text(_ui(WELCOME_AR, WELCOME_EN), parse_mode="Markdown")

# ================= إلغاء الاختبار =================
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in SESSIONS:
        del SESSIONS[chat_id]
        await update.message.reply_text(_ui("تم إلغاء الاختبار الحالي ✅", "Current quiz canceled ✅"))
    else:
        await update.message.reply_text(_ui("لا يوجد اختبار جارٍ الآن.", "No active quiz."))

# ================= إدارة الأزرار (الموافقة / الرفض) =================
async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("approve_") or data.startswith("reject_"):
        user_id = int(data.split("_")[1])
        if data.startswith("approve_"):
            pending_users.discard(user_id)
            allowed_users.add(user_id)
            save_data()
            await query.edit_message_text(f"تم قبول المستخدم {user_id} ✅")
            await context.bot.send_message(chat_id=user_id, text="تمت الموافقة على استخدامك البوت! يمكنك الآن إرسال الملفات.")
        else:
            pending_users.discard(user_id)
            banned_users.add(user_id)
            save_data()
            await query.edit_message_text(f"تم رفض المستخدم {user_id} ❌")
            await context.bot.send_message(chat_id=user_id, text="تم رفض طلبك لاستخدام البوت.")

# ================= لوحة التحكم الرئيسية =================
@admin_only
async def control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("المستخدمون المصرح لهم", callback_data="show_allowed")],
        [InlineKeyboardButton("المستخدمون المعلقون", callback_data="show_pending")],
        [InlineKeyboardButton("إرسال رسالة جماعية", callback_data="broadcast")],
    ])
    await update.message.reply_text("لوحة التحكم الرئيسية:", reply_markup=kb)

async def handle_control_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "show_allowed":
        if not allowed_users:
            await query.edit_message_text("لا يوجد مستخدمون حاليًا.")
            return
        text = "المستخدمون المصرح لهم:\n" + "\n".join([str(u) for u in allowed_users])
        await query.edit_message_text(text)
    elif data == "show_pending":
        if not pending_users:
            await query.edit_message_text("لا يوجد مستخدمون في الانتظار.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ قبول {u}", callback_data=f"approve_{u}"),
             InlineKeyboardButton(f"❌ رفض {u}", callback_data=f"reject_{u}")]
            for u in pending_users
        ])
        await query.edit_message_text("المستخدمون في الانتظار:", reply_markup=kb)
    elif data == "broadcast":
        await query.edit_message_text("استخدم الأمر /broadcast <رسالتك> لإرسال رسالة لجميع المستخدمين المصرح لهم.")

@admin_only
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /broadcast <رسالتك>")
        return
    message = " ".join(context.args)
    for user_id in allowed_users:
        await context.bot.send_message(chat_id=user_id, text=message)
    await update.message.reply_text("تم الإرسال لجميع المستخدمين المصرح لهم.")

# ================= استقبال الملفات =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in allowed_users:
        await update.message.reply_text("لا يمكنك استخدام البوت قبل موافقة المدير.")
        return

    chat_id = update.effective_chat.id
    doc = update.message.document or update.message.photo[-1] if update.message.photo else None
    if not doc and update.message.document is None and update.message.photo:
        pass
    if update.message.photo:
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

# ================= اختيار اللغة =================
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

    sess.update({"questions": questions, "index":0, "score": 0, "answers": {}, "stage": "quiz"})
    await send_next_question(chat_id, context)

# ================= إرسال الأسئلة التالية =================
async def send_next_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    sess = SESSIONS.get(chat_id)
    if not sess or sess.get("stage") != "quiz":
        return

    if sess["index"] >= len(sess["questions"]):
        await context.bot.send_message(chat_id=chat_id, text=_ui(
            f"انتهى الاختبار! نتيجتك: {sess['score']}/{len(sess['questions'])} ✅",
            f"Done! Your score: {sess['score']}/{len(sess['questions'])} ✅"))
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

# ================= استقبال إجابات الاختبار =================
async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    for chat_id, sess in list(SESSIONS.items()):
        if answer.poll_id in sess.get("answers", {}):
            correct = sess["answers"][answer.poll_id]
            if answer.option_ids and answer.option_ids[0] == correct:
                sess["score"] += 1
            await send_next_question(chat_id, context)
            break

# ================= تشغيل البوت =================
def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN env var")
    app = ApplicationBuilder().token(token).build()

    # أوامر المستخدم
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("control", control_panel))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # استقبال الملفات
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    # أزرار اختيار اللغة
    app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_(ar|en)$"))
    # أزرار الموافقة / الرفض
    app.add_handler(CallbackQueryHandler(handle_approval, pattern=r"^(approve|reject)_\d+$"))
    # أزرار لوحة التحكم
    app.add_handler(CallbackQueryHandler(handle_control_buttons, pattern=r"^(show_allowed|show_pending|broadcast)$"))
    # استقبال إجابات الاختبار
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot running…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()