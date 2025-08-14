import os
import re
import json
import tempfile
from datetime import datetime
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

# ================= إعداد المتغيرات =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID =481595387

if not BOT_TOKEN or not ADMIN_ID:
    raise SystemExit("Set BOT_TOKEN and ADMIN_ID env vars")

LANG_UI_DEFAULT = os.getenv("LANG", "ar")
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", 16))
SESSIONS: Dict[int, Dict] = {}

USERS_FILE = "bot_users.json"
EVENTS_FILE = "bot_events.json"

# ================= إنشاء ملفات البيانات إذا لم تكن موجودة =================
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"allowed_users": [], "banned_users": [], "pending_users": []}, f, ensure_ascii=False, indent=2)

if not os.path.exists(EVENTS_FILE):
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

# ================= دوال مساعدة =================
def _ui(text_ar: str, text_en: str) -> str:
    return text_ar if LANG_UI_DEFAULT == "ar" else text_en

def _clean_text(t: str) -> str:
    t = t.replace("\u200f", " ").replace("\u200e", " ")
    t = re.sub(r"[\t\xa0]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def load_users():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_event(user_id, username, event_type, details=""):
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)
    events.append({
        "user_id": user_id,
        "username": username,
        "event_type": event_type,
        "details": details,
        "timestamp": datetime.utcnow().isoformat()
    })
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

# ================= رسائل ترحيبية =================
WELCOME_AR = (
    "أهلًا! أرسل ملف (PDF/DOCX/PPTX/TXT/صور) وسأحوّله لأسئلة قوية بالذكاء الاصطناعي.\n\n"
    "الأوامر: /start /cancel"
)
WELCOME_EN = (
    "Hi! Send a PDF/DOCX/PPTX/TXT/Image and I'll turn it into strong exam questions using AI.\n\n"
    "Commands: /start /cancel"
)

# ================= أوامر المستخدم =================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    users = load_users()

    # تحقق من حالة المستخدم
    if user_id in users["banned_users"]:
        await update.message.reply_text("عذرًا، تم حظرك من استخدام هذا البوت.")
        return
    elif user_id not in users["allowed_users"]:
        if user_id not in users["pending_users"]:
            users["pending_users"].append(user_id)
            save_users(users)
            log_event(user_id, update.effective_user.username, "new_user_pending")
        await update.message.reply_text("تم تسجيلك كـ مستخدم جديد، في انتظار موافقة المدير.")
        return

    await update.message.reply_text(_ui(WELCOME_AR, WELCOME_EN))

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in SESSIONS:
        del SESSIONS[chat_id]
        await update.message.reply_text(_ui("تم إلغاء الاختبار الحالي ✅", "Current quiz canceled ✅"))
    else:
        await update.message.reply_text(_ui("لا يوجد اختبار جارٍ الآن.", "No active quiz."))

# ================= لوحة تحكم المدير =================
async def control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("هذه الميزة للمدير فقط.")
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("المستخدمون المصرح لهم", callback_data="show_allowed")],
        [InlineKeyboardButton("المستخدمون المعلقون", callback_data="show_pending")],
        [InlineKeyboardButton("إرسال رسالة جماعية", callback_data="broadcast")],
        [InlineKeyboardButton("الإحصائيات", callback_data="stats")],
    ])
    await update.message.reply_text("لوحة التحكم الرئيسية:", reply_markup=kb)

# ================= التعامل مع أزرار لوحة التحكم =================
# الرجوع للوحة الرئيسية
async def back_to_panel(update, context):
    query = update.callback_query
    await query.answer()
    await control_panel(query, context)

# التعامل مع أزرار لوحة التحكم الموسعة
async def handle_control_buttons(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    users = load_users()

    if data == "show_allowed":
        if not users["allowed_users"]:
            await query.edit_message_text("لا يوجد مستخدمون مصرح لهم.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{u}", callback_data=f"user_stats_{u}")]
            for u in users["allowed_users"]
        ] + [[InlineKeyboardButton("العودة", callback_data="back")]])
        await query.edit_message_text("المستخدمون المصرح لهم (اضغط لرؤية التفاصيل):", reply_markup=kb)

    elif data == "show_pending":
        if not users["pending_users"]:
            await query.edit_message_text("لا يوجد مستخدمون معلقون.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"قبول {u}", callback_data=f"approve_{u}"),
             InlineKeyboardButton(f"رفض {u}", callback_data=f"reject_{u}")]
            for u in users["pending_users"]
        ] + [[InlineKeyboardButton("العودة", callback_data="back")]])
        await query.edit_message_text("المستخدمون المعلقون:", reply_markup=kb)

    elif data == "broadcast":
        await query.edit_message_text("ارسل الرسالة الجماعية وسيتم إرسالها للمستخدمين المصرح لهم.")

    elif data == "stats":
        total_allowed = len(users["allowed_users"])
        total_pending = len(users["pending_users"])
        total_banned = len(users["banned_users"])
        await query.edit_message_text(
            f"إحصائيات المستخدمين:\nمصرح لهم: {total_allowed}\nمعلقون: {total_pending}\nمحظورون: {total_banned}\n\nاضغط على أي مستخدم من 'المصرح لهم' لمشاهدة التفاصيل."
        )

    elif data.startswith("user_stats_"):
        user_id = data.split("_")[-1]
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            events = json.load(f)
        user_events = [e for e in events if str(e["user_id"]) == str(user_id)]
        if not user_events:
            text = f"لا توجد بيانات للمستخدم {user_id}"
        else:
            text = f"تفاصيل المستخدم {user_id}:\n"
            for e in user_events[-10:]:  # آخر 10 أحداث
                text += f"- {e['timestamp']}: {e['event_type']} ({e.get('details','')})\n"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("العودة", callback_data="back")]])
        await query.edit_message_text(text, reply_markup=kb)

    elif data == "back":
        await back_to_panel(update, context)

# ================= قبول أو رفض المستخدمين الجدد =================
async def handle_approval(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    users = load_users()

    if data.startswith("approve_"):
        user_id = data.split("_")[1]
        if user_id in users["pending_users"]:
            users["pending_users"].remove(user_id)
            users["allowed_users"].append(user_id)
            save_users(users)
            log_event(user_id, query.from_user.username, "approved")
            await query.edit_message_text(f"تم قبول المستخدم {user_id}")
    elif data.startswith("reject_"):
        user_id = data.split("_")[1]
        if user_id in users["pending_users"]:
            users["pending_users"].remove(user_id)
            users["banned_users"].append(user_id)
            save_users(users)
            log_event(user_id, query.from_user.username, "rejected")
            await query.edit_message_text(f"تم رفض المستخدم {user_id}")

# ================= استقبال الملفات =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    users = load_users()

    if user_id not in users["allowed_users"]:
        await update.message.reply_text("عذرًا، لم يتم السماح لك باستخدام هذا البوت بعد.")
        return

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

    # تسجيل الملف في الأحداث
    log_event(user_id, update.effective_user.username, "file_sent", filename)

    # تخزين الملف مؤقتًا
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

# ================= اختيار لغة الملف =================
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

    sess.update({"questions": questions, "index": 0, "score": 0, "answers": {}, "stage": "quiz"})
    await send_next_question(chat_id, context)

# ================= إرسال الأسئلة =================
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

# ================= استقبال إجابات الاختبارات =================
async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    for chat_id, sess in list(SESSIONS.items()):
        if answer.poll_id in sess.get("answers", {}):
            correct = sess["answers"][answer.poll_id]
            if answer.option_ids and answer.option_ids[0] == correct:
                sess["score"] += 1
            # تسجيل الحدث
            log_event(str(update.effective_user.id), update.effective_user.username, "quiz_answer", str(answer.option_ids))
            await send_next_question(chat_id, context)
            break

# ================= تشغيل البوت =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    
    # لوحة تحكم المدير
    app.add_handler(CommandHandler("control", control_panel))
    app.add_handler(CallbackQueryHandler(handle_control_buttons, pattern=r"^(show_allowed|show_pending|broadcast|stats)$"))
    app.add_handler(CallbackQueryHandler(handle_approval, pattern=r"^(approve_|reject_).+"))
    
    # استقبال الملفات
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_(ar|en)$"))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot running…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()