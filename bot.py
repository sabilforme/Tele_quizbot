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
from telegram import BotCommand

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

# ================= نظام تخزين متقدم =================
def migrate_old_data(old_data):
    """تحويل البيانات من النسخة القديمة إلى الهيكل الجديد"""
    new_data = {
        "users": {},
        "files": [],
        "events": [],
        "statistics": {
            "total_users": 0,
            "active_today": 0,
            "files_processed": 0,
            "quizzes_taken": 0
        }
    }

    # تحويل المستخدمين
    for user_id in old_data.get("allowed_users", []):
        new_data["users"][str(user_id)] = {
            "status": "allowed",
            "username": "unknown_old_user",
            "full_name": "Unknown Old User",
            "join_date": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "files_sent": 0,
            "quizzes_taken": 0,
            "total_score": 0
        }
        new_data["statistics"]["total_users"] += 1

    for user_id in old_data.get("banned_users", []):
        new_data["users"][str(user_id)] = {
            "status": "banned",
            "username": "unknown_old_user",
            "full_name": "Unknown Old User",
            "join_date": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "files_sent": 0,
            "quizzes_taken": 0,
            "total_score": 0
        }
        new_data["statistics"]["total_users"] += 1

    for user_id in old_data.get("pending_users", []):
        new_data["users"][str(user_id)] = {
            "status": "pending",
            "username": "unknown_old_user",
            "full_name": "Unknown Old User",
            "join_date": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "files_sent": 0,
            "quizzes_taken": 0,
            "total_score": 0
        }
        new_data["statistics"]["total_users"] += 1

    return new_data

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)

            # تحويل البيانات القديمة إلى الهيكل الجديد
            if "users" not in data:
                data = migrate_old_data(data)
                save_data(data)

            return data
    except FileNotFoundError:
        return {
            "users": {},
            "files": [],
            "events": [],
            "statistics": {
                "total_users": 0,
                "active_today": 0,
                "files_processed": 0,
                "quizzes_taken": 0
            }
        }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ================= تسجيل الأحداث =================
def log_event(user_id, event_type, details=None):
    data = load_data()
    event = {
        "timestamp": datetime.now().isoformat(),
        "user_id": user_id,
        "type": event_type,
        "details": details or {}
    }
    data["events"].append(event)

    # تحديث الإحصائيات
    if event_type == "file_upload":
        data["statistics"]["files_processed"] += 1
    elif event_type == "quiz_completed":
        data["statistics"]["quizzes_taken"] += 1

    save_data(data)

# ================= إدارة المستخدمين =================
def refresh_user_lists():
    data = load_data()
    allowed_users = set()
    banned_users = set()
    pending_users = set()

    for user_id, user_data in data["users"].items():
        if user_data["status"] == "allowed":
            allowed_users.add(int(user_id))
        elif user_data["status"] == "banned":
            banned_users.add(int(user_id))
        elif user_data["status"] == "pending":
            pending_users.add(int(user_id))

    return allowed_users, banned_users, pending_users

allowed_users, banned_users, pending_users = refresh_user_lists()

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
    global allowed_users, banned_users, pending_users
    user_id = update.effective_user.id
    username = update.effective_user.username or "غير معروف"
    full_name = update.effective_user.full_name

    data = load_data()

    # تأكد من وجود مفتاح 'users'
    if "users" not in data:
        data = migrate_old_data({})
        save_data(data)

    # تسجيل مستخدم جديد
    if str(user_id) not in data["users"]:
        data["users"][str(user_id)] = {
            "username": username,
            "full_name": full_name,
            "join_date": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "status": "pending",
            "files_sent": 0,
            "quizzes_taken": 0,
            "total_score": 0
        }
        data["statistics"]["total_users"] += 1
        save_data(data)
        log_event(user_id, "user_join")

    # تحديث قوائم المستخدمين
    allowed_users, banned_users, pending_users = refresh_user_lists()

    if user_id in banned_users:
        await update.message.reply_text("تم حظرك من استخدام هذا البوت.")
        return

    user_status = data["users"][str(user_id)]["status"]
    if user_status != "allowed":
        if user_status == "pending":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ قبول", callback_data=f"approve_{user_id}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")]
            ])
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"مستخدم جديد طلب استخدام البوت:\n{full_name} (@{username}) - ID: {user_id}",
                reply_markup=kb
            )
        await update.message.reply_text("في انتظار موافقة المدير لاستخدام البوت.")
        return

    await update.message.reply_text(_ui(WELCOME_AR, WELCOME_EN), parse_mode="Markdown")
    log_event(user_id, "bot_started")

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
    global allowed_users, banned_users, pending_users
    query = update.callback_query
    await query.answer()
    data = query.data
    action, user_id = data.split("_")
    user_id = int(user_id)

    db_data = load_data()
    user_key = str(user_id)

    if action == "approve":
        if user_key in db_data["users"]:
            db_data["users"][user_key]["status"] = "allowed"
            save_data(db_data)
            await query.edit_message_text(f"تم قبول المستخدم {user_id} ✅")
            await context.bot.send_message(
                chat_id=user_id,
                text="تمت الموافقة على استخدامك البوت! يمكنك الآن إرسال الملفات."
            )
            log_event(user_id, "user_approved")
    elif action == "reject":
        if user_key in db_data["users"]:
            db_data["users"][user_key]["status"] = "banned"
            save_data(db_data)
            await query.edit_message_text(f"تم رفض المستخدم {user_id} ❌")
            await context.bot.send_message(
                chat_id=user_id,
                text="تم رفض طلبك لاستخدام البوت."
            )
            log_event(user_id, "user_rejected")

    # تحديث قوائم المستخدمين
    allowed_users, banned_users, pending_users = refresh_user_lists()

# ================= لوحة التحكم الرئيسية =================
@admin_only
async def control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    stats = data["statistics"]

    text = _ui(
        f"📊 **لوحة التحكم المتقدمة**\n\n"
        f"👥 المستخدمون: {stats['total_users']}\n"
        f"📤 الملفات المعالجة: {stats['files_processed']}\n"
        f"🧠 الاختبارات المكتملة: {stats['quizzes_taken']}\n"
        f"🔥 المستخدمون النشطون اليوم: {stats['active_today']}",

        f"📊 **Advanced Control Panel**\n\n"
        f"👥 Users: {stats['total_users']}\n"
        f"📤 Files Processed: {stats['files_processed']}\n"
        f"🧠 Quizzes Completed: {stats['quizzes_taken']}\n"
        f"🔥 Active Users Today: {stats['active_today']}"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_ui("👥 إدارة المستخدمين", "👥 User Management"), callback_data="user_mgmt")],
        [InlineKeyboardButton(_ui("📁 الملفات المرسلة", "📁 Sent Files"), callback_data="file_list")],
        [InlineKeyboardButton(_ui("📝 سجل الأحداث", "📝 Event Log"), callback_data="event_log")],
        [InlineKeyboardButton(_ui("📊 الإحصائيات", "📊 Statistics"), callback_data="stats_detailed")],
        [InlineKeyboardButton(_ui("📤 تصدير البيانات", "📤 Export Data"), callback_data="export_data")]
    ])

    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# ================= معالج أزرار لوحة التحكم =================
async def handle_control_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # قائمة المستخدمين
    if data == "user_mgmt":
        await show_user_list(query)

    # قائمة الملفات
    elif data == "file_list":
        await show_file_list(query)

    # سجل الأحداث
    elif data == "event_log":
        await show_event_log(query)

    # الإحصائيات التفصيلية
    elif data == "stats_detailed":
        await show_detailed_stats(query)

    # تصدير البيانات
    elif data == "export_data":
        await export_data_menu(query)

    # تفاصيل المستخدم
    elif data.startswith("user_detail_"):
        user_id = int(data.split("_")[2])
        await show_user_detail(query, user_id)

    # ملفات المستخدم
    elif data.startswith("user_files_"):
        user_id = int(data.split("_")[2])
        await show_user_files(query, user_id)

    # العودة إلى لوحة التحكم
    elif data == "back_to_control":
        await control_panel(query.message, context)
        await query.message.delete()

# ===== عرض قائمة المستخدمين =====
async def show_user_list(query):
    data = load_data()
    users = data["users"]

    buttons = []
    for user_id, user_data in list(users.items())[:10]:  # أول 10 مستخدمين
        btn_text = f"{user_data['full_name']} ({user_data['status']})"
        buttons.append([InlineKeyboardButton(
            btn_text, 
            callback_data=f"user_detail_{user_id}"
        )])

    # إضافة زر الصفحة التالية إذا لزم الأمر
    if len(users) > 10:
        buttons.append([InlineKeyboardButton(
            _ui("الصفحة التالية →", "Next Page →"), 
            callback_data="user_page_2"
        )])

    buttons.append([InlineKeyboardButton(
        _ui("◀ العودة", "◀ Back"), 
        callback_data="back_to_control"
    )])

    kb = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(_ui("قائمة المستخدمين:", "User List:"), reply_markup=kb)

# ===== تفاصيل المستخدم =====
async def show_user_detail(query, user_id):
    data = load_data()
    user_data = data["users"].get(str(user_id))

    if not user_data:
        await query.edit_message_text(_ui("المستخدم غير موجود", "User not found"))
        return

    text = _ui(
        f"🧑 **تفاصيل المستخدم**\n\n"
        f"الاسم: {user_data['full_name']}\n"
        f"المعرف: @{user_data['username']}\n"
        f"الحالة: {user_data['status']}\n\n"
        f"📅 تاريخ التسجيل: {user_data['join_date']}\n"
        f"⏱ آخر نشاط: {user_data['last_activity']}\n\n"
        f"📤 الملفات المرسلة: {user_data['files_sent']}\n"
        f"🧠 الاختبارات المكتملة: {user_data['quizzes_taken']}\n"
        f"💯 إجمالي النقاط: {user_data['total_score']}",

        f"🧑 **User Details**\n\n"
        f"Name: {user_data['full_name']}\n"
        f"Username: @{user_data['username']}\n"
        f"Status: {user_data['status']}\n\n"
        f"📅 Join Date: {user_data['join_date']}\n"
        f"⏱ Last Activity: {user_data['last_activity']}\n\n"
        f"📤 Files Sent: {user_data['files_sent']}\n"
        f"🧠 Quizzes Completed: {user_data['quizzes_taken']}\n"
        f"💯 Total Score: {user_data['total_score']}"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_ui("📁 ملفات المستخدم", "User Files"), callback_data=f"user_files_{user_id}"),
            InlineKeyboardButton(_ui("📝 أحداث المستخدم", "User Events"), callback_data=f"user_events_{user_id}")
        ],
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data="user_mgmt")]
    ])

    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

# ===== ملفات المستخدم =====
async def show_user_files(query, user_id):
    data = load_data()
    user_files = [f for f in data["files"] if f["user_id"] == user_id]

    text = _ui(
        f"📁 الملفات المرسلة بواسطة المستخدم:\n\n",
        f"📁 Files Sent by User:\n\n"
    )

    for file in user_files[:5]:  # آخر 5 ملفات
        text += _ui(
            f"• {file['filename']} ({file['timestamp']})\n",
            f"• {file['filename']} ({file['timestamp']})\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data=f"user_detail_{user_id}")]
    ])

    await query.edit_message_text(text, reply_markup=kb)

# ===== سجل الأحداث =====
async def show_event_log(query):
    data = load_data()
    events = data["events"][-10:]  # آخر 10 أحداث

    text = _ui("📝 آخر 10 أحداث:\n\n", "📝 Last 10 Events:\n\n")

    for event in events:
        event_type = event["type"]
        user_id = event["user_id"]
        timestamp = event["timestamp"]

        text += _ui(
            f"• [{timestamp}] {event_type} بواسطة {user_id}\n",
            f"• [{timestamp}] {event_type} by {user_id}\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data="back_to_control")]
    ])

    await query.edit_message_text(text, reply_markup=kb)

# ===== الإحصائيات التفصيلية =====
async def show_detailed_stats(query):
    data = load_data()
    stats = data["statistics"]

    text = _ui(
        f"📊 **الإحصائيات التفصيلية**\n\n"
        f"👥 إجمالي المستخدمين: {stats['total_users']}\n"
        f"📤 الملفات المعالجة: {stats['files_processed']}\n"
        f"🧠 الاختبارات المكتملة: {stats['quizzes_taken']}\n"
        f"🔥 المستخدمون النشطون اليوم: {stats['active_today']}\n\n"
        f"📈 معدل النشاط اليومي: {stats['files_processed'] / max(1, stats['active_today']):.1f} ملف/مستخدم",

        f"📊 **Detailed Statistics**\n\n"
        f"👥 Total Users: {stats['total_users']}\n"
        f"📤 Files Processed: {stats['files_processed']}\n"
        f"🧠 Quizzes Completed: {stats['quizzes_taken']}\n"
        f"🔥 Active Users Today: {stats['active_today']}\n\n"
        f"📈 Daily Activity Rate: {stats['files_processed'] / max(1, stats['active_today']):.1f} files/user"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data="back_to_control")]
    ])

    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

# ===== قائمة تصدير البيانات =====
async def export_data_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("JSON", callback_data="export_json")],
        [InlineKeyboardButton("CSV", callback_data="export_csv")],
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data="back_to_control")]
    ])

    await query.edit_message_text(_ui("اختر صيغة التصدير:", "Choose export format:"), reply_markup=kb)

# ===== معالج تصدير البيانات =====
@admin_only
async def handle_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = load_data()
    export_type = query.data.split("_")[1]

    if export_type == "json":
        # إنشاء ملف JSON مؤقت
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp_file:
            with open(tmp_file.name, "w") as f:
                json.dump(data, f)
            await context.bot.send_document(chat_id=ADMIN_ID, document=tmp_file.name)

    await query.edit_message_text(_ui("تم تصدير البيانات بنجاح ✅", "Data exported successfully ✅"))

# ================= استقبال الملفات =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in allowed_users:
        await update.message.reply_text(_ui("لا يمكنك استخدام البوت قبل موافقة المدير.", "You need admin approval to use this bot."))
        return

    chat_id = update.effective_chat.id
    doc = update.message.document or update.message.photo[-1] if update.message.photo else None
    if not doc and update.message.document is None and update.message.photo:
        pass

    size_mb = 0
    filename = ""
    suffix = ""

    if update.message.photo:
        photo = update.message.photo[-1]
        tgfile = await context.bot.get_file(photo.file_id)
        file_bytes = await tgfile.download_as_bytearray()
        filename = "image.jpg"
        suffix = ".jpg"
        size_mb = len(file_bytes) / (1024 * 1024)
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

    # تسجيل الملف في النظام
    data = load_data()
    data["files"].append({
        "user_id": user_id,
        "filename": filename,
        "timestamp": datetime.now().isoformat(),
        "size_mb": size_mb,
        "status": "processing"
    })

    # تحديث إحصائيات المستخدم
    if str(user_id) in data["users"]:
        data["users"][str(user_id)]["files_sent"] += 1
        data["users"][str(user_id)]["last_activity"] = datetime.now().isoformat()

    save_data(data)
    log_event(user_id, "file_upload", {
        "filename": filename,
        "size": size_mb
    })

    SESSIONS[chat_id] = {
    "stage": "await_lang",
    "filename": filename,
    "suffix": suffix,
    "file_bytes": bytes(file_bytes),
    "content_lang": None,  # سيتم تعيينها لاحقاً
    "question_lang": None,  # سيتم تعيينها لاحقاً
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
    sess["content_lang"] = lang
    sess["stage"] = "await_question_lang"  # الانتقال لمرحلة اختيار لغة الأسئلة فقط
    
    # عرض خيارات لغة الأسئلة بشكل منفصل
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("العربية", callback_data="qlang_ar")],
        [InlineKeyboardButton("English", callback_data="qlang_en")],
    ])
    await query.edit_message_text(
        _ui("اختر لغة أسئلة الاختبار:", "Choose the quiz questions language:"), 
        reply_markup=kb
    )

async def choose_question_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    sess = SESSIONS.get(chat_id)
    if not sess or sess.get("stage") != "await_question_lang":
        await query.edit_message_text(_ui("لا يوجد ملف قيد المعالجة.", "No pending file."))
        return

    lang = "ar" if query.data == "qlang_ar" else "en"
    sess["question_lang"] = lang
    
    # الانتقال مباشرة إلى دالة المعالجة المشتركة
    await start_file_processing(chat_id, context)
    
async def start_file_processing(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    sess = SESSIONS.get(chat_id)
    if not sess:
        return

    sess["stage"] = "processing"
    await context.bot.send_message(chat_id=chat_id, text=_ui("جاري تحليل الملف وإعداده… ⏳", "Analyzing the file… ⏳"))

    # استخراج النص باستخدام لغة المحتوى
    with tempfile.NamedTemporaryFile(delete=False, suffix=sess["suffix"]) as f:
        f.write(sess["file_bytes"])
        tmp_path = f.name

    try:
        text = await extract_text_any(tmp_path, sess["suffix"], sess["content_lang"])
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

    # استخدام لغة الأسئلة المختارة
    questions = await build_quiz_from_text(text, lang=sess["question_lang"], target_total=40)
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

        # تسجيل إكمال الاختبار
        user_id = sess.get("user_id", chat_id)
        log_event(user_id, "quiz_completed", {
            "score": sess['score'],
            "total": len(sess['questions'])
        })

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

            # تسجيل نتيجة الاختبار
            user_id = answer.user.id
            data = load_data()
            if str(user_id) in data["users"]:
                user_data = data["users"][str(user_id)]
                user_data["quizzes_taken"] += 1
                if answer.option_ids and answer.option_ids[0] == correct:
                    user_data["total_score"] += 1
                user_data["last_activity"] = datetime.now().isoformat()
                save_data(data)

            log_event(user_id, "quiz_answer", {
                "question_index": sess["index"],
                "is_correct": answer.option_ids and answer.option_ids[0] == correct
            })

            await send_next_question(chat_id, context)
            break

async def set_bot_commands(application):
    """إعداد قائمة الأوامر في واجهة المستخدم"""
    commands = [
        BotCommand("start", _ui("بدء استخدام البوت", "Start the bot")),
        BotCommand("cancel", _ui("إلغاء العملية الحالية", "Cancel current process")),
        BotCommand("control", _ui("لوحة تحكم المدير", "Admin control panel")),
    ]
    await application.bot.set_my_commands(commands)
# ================= تشغيل البوت =================
def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN env var")
    app = ApplicationBuilder().token(token).build()
    app.post_init = set_bot_commands
    # أوامر المستخدم
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("control", control_panel))
    app.add_handler(CallbackQueryHandler(handle_export, pattern=r"^export_(json|csv)$"))

    # استقبال الملفات
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    # أزرار اختيار اللغة
    app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_(ar|en)$"))
    # إضافة هذا المعالج بعد المعالجات الأخرى
    app.add_handler(CallbackQueryHandler(choose_question_language, pattern=r"^qlang_(ar|en)$"))
    # أزرار الموافقة / الرفض
    app.add_handler(CallbackQueryHandler(handle_approval, pattern=r"^(approve|reject)_\d+$"))
    # أزرار لوحة التحكم
    app.add_handler(CallbackQueryHandler(handle_control_buttons))
    # استقبال إجابات الاختبار
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot running…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()