import os
import re
import json
import tempfile
import logging
from datetime import datetime
from typing import Dict, Set, List

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

# ================= إعدادات التسجيل =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= إعدادات البوت =================
LANG_UI_DEFAULT = os.getenv("LANG", "ar")  # واجهة البوت فقط
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", 16))
ADMIN_ID = int(os.getenv("ADMIN_ID", 481595387))  # يجب تعيينه في المتغيرات البيئية
DATA_FILE = "bot_users.json"
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("يجب تعيين متغير البيئة BOT_TOKEN")

# ================= نصوص الواجهة =================
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

# ================= هياكل البيانات =================
SESSIONS: Dict[int, Dict] = {}
allowed_users: Set[int] = set()
banned_users: Set[int] = set()
pending_users: Set[int] = set()

# ================= نظام إدارة اللغات =================
class LanguageManager:
    def __init__(self):
        self.content_lang = {}  # لغة محتوى الملف
        self.quiz_lang = {}     # لغة الأسئلة

    def set_content_lang(self, chat_id: int, lang: str):
        self.content_lang[chat_id] = lang

    def set_quiz_lang(self, chat_id: int, lang: str):
        self.quiz_lang[chat_id] = lang

    def get_content_lang(self, chat_id: int) -> str:
        return self.content_lang.get(chat_id, "ar")

    def get_quiz_lang(self, chat_id: int) -> str:
        return self.quiz_lang.get(chat_id, "ar")

LANG_MANAGER = LanguageManager()

# ================= أدوات مساعدة =================
def _ui(text_ar: str, text_en: str) -> str:
    return text_ar if LANG_UI_DEFAULT == "ar" else text_en

def _clean_text(t: str) -> str:
    t = t.replace("\u200f", " ").replace("\u200e", " ")
    t = re.sub(r"[\t\xa0]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# ================= نظام التخزين =================
def load_data() -> Dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # التأكد من وجود الهيكل الأساسي للبيانات
        required_keys = ["users", "files", "events", "statistics"]
        for key in required_keys:
            if key not in data:
                data[key] = {} if key == "users" else []
        
        # إصلاح مشكلة statistics إذا كان قائمة بدلاً من قاموس
        if isinstance(data["statistics"], list):
            data["statistics"] = {
                "total_users": 0,
                "active_today": 0,
                "files_processed": 0,
                "quizzes_taken": 0
            }
        elif isinstance(data["statistics"], dict):
            # التأكد من وجود جميع المفاتيح في statistics
            stats_keys = ["total_users", "active_today", "files_processed", "quizzes_taken"]
            for key in stats_keys:
                if key not in data["statistics"]:
                    data["statistics"][key] = 0
                    
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        # إنشاء بيانات جديدة إذا كان الملف غير موجود أو تالف
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
def save_data(data: Dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def refresh_user_lists():
    global allowed_users, banned_users, pending_users
    data = load_data()
    
    allowed_users = set()
    banned_users = set()
    pending_users = set()

    for user_id, user_data in data["users"].items():
        user_id_int = int(user_id)
        status = user_data.get("status", "pending")
        
        if status == "allowed":
            allowed_users.add(user_id_int)
        elif status == "banned":
            banned_users.add(user_id_int)
        elif status == "pending":
            pending_users.add(user_id_int)

def log_event(user_id: int, event_type: str, details: Dict = None):
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

# ================= نظام التحقق من الصلاحيات =================
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not is_admin(user_id):
            if update.callback_query:
                await update.callback_query.answer(_ui("هذه الميزة للمدير فقط.", "This feature is for admin only."), show_alert=True)
            else:
                await update.message.reply_text(_ui("هذه الميزة للمدير فقط.", "This feature is for admin only."))
            return
            
        return await func(update, context)
    return wrapper

def user_allowed(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id in banned_users:
            await update.message.reply_text(_ui("تم حظرك من استخدام هذا البوت.", "You are banned from using this bot."))
            return
            
        if user_id not in allowed_users:
            await update.message.reply_text(_ui("في انتظار موافقة المدير لاستخدام البوت.", "Waiting for admin approval to use the bot."))
            return
            
        return await func(update, context)
    return wrapper

# ================= أوامر البوت الأساسية =================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "غير معروف"
    full_name = update.effective_user.full_name

    data = load_data()
    
    # تحديث قوائم المستخدمين أولاً
    refresh_user_lists()

    if user_id in banned_users:
        await update.message.reply_text(_ui("تم حظرك من استخدام هذا البوت.", "You are banned from using this bot."))
        return

    # تسجيل مستخدم جديد إذا لم يكن موجوداً
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
        
        # إرسال طلب الموافقة للمدير
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ قبول", callback_data=f"approve_{user_id}"),
             InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")]
        ])
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"مستخدم جديد طلب استخدام البوت:\n{full_name} (@{username}) - ID: {user_id}",
            reply_markup=kb
        )
        
        # تحديث القوائم بعد إضافة المستخدم
        refresh_user_lists()

    if user_id not in allowed_users:
        await update.message.reply_text(_ui("في انتظار موافقة المدير لاستخدام البوت.", "Waiting for admin approval to use the bot."))
        return

    await update.message.reply_text(_ui(WELCOME_AR, WELCOME_EN), parse_mode="Markdown")
    log_event(user_id, "bot_started")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in SESSIONS:
        del SESSIONS[chat_id]
        await update.message.reply_text(_ui("تم إلغاء الاختبار الحالي ✅", "Current quiz canceled ✅"))
    else:
        await update.message.reply_text(_ui("لا يوجد اختبار جارٍ الآن.", "No active quiz."))

# ================= إدارة المستخدمين (للمدير) =================
@admin_only
async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    action, user_id_str = data.split("_")
    user_id = int(user_id_str)

    db_data = load_data()
    user_key = str(user_id)

    if user_key not in db_data["users"]:
        await query.edit_message_text(_ui("المستخدم غير موجود.", "User not found."))
        return

    if action == "approve":
        db_data["users"][user_key]["status"] = "allowed"
        save_data(db_data)
        await query.edit_message_text(f"تم قبول المستخدم {user_id} ✅")
        await context.bot.send_message(
            chat_id=user_id,
            text=_ui("تمت الموافقة على استخدامك البوت! يمكنك الآن إرسال الملفات.", "Your access to the bot has been approved! You can now send files.")
        )
        log_event(user_id, "user_approved")
    elif action == "reject":
        db_data["users"][user_key]["status"] = "banned"
        save_data(db_data)
        await query.edit_message_text(f"تم رفض المستخدم {user_id} ❌")
        await context.bot.send_message(
            chat_id=user_id,
            text=_ui("تم رفض طلبك لاستخدام البوت.", "Your request to use the bot has been rejected.")
        )
        log_event(user_id, "user_rejected")

    # تحديث قوائم المستخدمين
    refresh_user_lists()

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

# ================= معالجة أزرار لوحة التحكم =================
@admin_only
async def handle_control_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "user_mgmt":
        await show_user_list(query)
    elif data == "file_list":
        await show_file_list(query)
    elif data == "event_log":
        await show_event_log(query)
    elif data == "stats_detailed":
        await show_detailed_stats(query)
    elif data == "export_data":
        await export_data_menu(query)
    elif data.startswith("user_detail_"):
        user_id = int(data.split("_")[2])
        await show_user_detail(query, user_id)
    elif data.startswith("user_files_"):
        user_id = int(data.split("_")[2])
        await show_user_files(query, user_id)
    elif data == "back_to_control":
        await control_panel_from_query(query, context)
    else:
        await query.edit_message_text(_ui("الإجراء غير معروف", "Unknown action"))

async def control_panel_from_query(query, context):
    # إنشاء رسالة وهمية للعودة إلى لوحة التحكم
    from telegram import Message, Chat
    fake_chat = Chat(id=query.message.chat_id, type="private")
    fake_message = Message(message_id=query.message.message_id, date=query.message.date, chat=fake_chat)
    fake_update = Update(update_id=0, message=fake_message)
    
    await control_panel(fake_update, context)
    await query.message.delete()

# ================= وظائف لوحة التحكم =================
async def show_user_list(query):
    data = load_data()
    users = data["users"]

    buttons = []
    for user_id, user_data in list(users.items())[:10]:
        btn_text = f"{user_data['full_name']} ({user_data['status']})"
        buttons.append([InlineKeyboardButton(
            btn_text, 
            callback_data=f"user_detail_{user_id}"
        )])

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

async def show_user_files(query, user_id):
    data = load_data()
    user_files = [f for f in data["files"] if f["user_id"] == user_id]

    text = _ui(
        f"📁 الملفات المرسلة بواسطة المستخدم:\n\n",
        f"📁 Files Sent by User:\n\n"
    )

    for file in user_files[:5]:
        text += _ui(
            f"• {file['filename']} ({file['timestamp']})\n",
            f"• {file['filename']} ({file['timestamp']})\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data=f"user_detail_{user_id}")]
    ])

    await query.edit_message_text(text, reply_markup=kb)

async def show_file_list(query):
    data = load_data()
    files = data["files"][-10:]

    text = _ui("📁 آخر 10 ملفات معالجة:\n\n", "📁 Last 10 Processed Files:\n\n")
    for file in files:
        text += _ui(
            f"• {file['filename']} ({file['size_mb']:.1f}MB) - {file['timestamp']}\n",
            f"• {file['filename']} ({file['size_mb']:.1f}MB) - {file['timestamp']}\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data="back_to_control")]
    ])

    await query.edit_message_text(text, reply_markup=kb)

async def show_event_log(query):
    data = load_data()
    events = data["events"][-10:]

    text = _ui("📝 آخر 10 أحداث:\n\n", "📝 Last 10 Events:\n\n")
    for event in events:
        text += _ui(
            f"• [{event['timestamp']}] {event['type']} بواسطة {event['user_id']}\n",
            f"• [{event['timestamp']}] {event['type']} by {event['user_id']}\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data="back_to_control")]
    ])

    await query.edit_message_text(text, reply_markup=kb)

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

async def export_data_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("JSON", callback_data="export_json")],
        [InlineKeyboardButton("CSV", callback_data="export_csv")],
        [InlineKeyboardButton(_ui("◀ العودة", "◀ Back"), callback_data="back_to_control")]
    ])

    await query.edit_message_text(_ui("اختر صيغة التصدير:", "Choose export format:"), reply_markup=kb)

@admin_only
async def handle_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "export_json":
        # إنشاء ملف JSON مؤقت
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp_file:
            json.dump(load_data(), tmp_file, ensure_ascii=False, indent=2)
            tmp_file.flush()
            
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=open(tmp_file.name, 'rb'),
                filename='bot_data.json'
            )
            
        await query.edit_message_text(_ui("تم تصدير البيانات بنجاح ✅", "Data exported successfully ✅"))
    elif data == "export_csv":
        await query.edit_message_text(_ui("هذه الميزة قيد التطوير", "This feature is under development"))

# ================= معالجة الملفات والاختبارات =================
@user_allowed
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # التحقق من نوع الملف
    if update.message.document:
        doc = update.message.document
        size_mb = doc.file_size / (1024 * 1024)
        if size_mb > MAX_FILE_MB:
            await update.message.reply_text(_ui(f"الحجم كبير ({size_mb:.1f}MB). أرسل ملف ≤ {MAX_FILE_MB}MB.", f"File too large ({size_mb:.1f}MB). Max {MAX_FILE_MB}MB."))
            return
            
        filename = doc.file_name or "file"
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in [".pdf", ".txt", ".docx", ".pptx", ".jpg", ".jpeg", ".png", ".tif", ".tiff"]:
            await update.message.reply_text(_ui("الرجاء إرسال PDF/DOCX/PPTX/TXT/صورة.", "Please send a PDF/DOCX/PPTX/TXT/Image."))
            return
            
        tgfile = await context.bot.get_file(doc.file_id)
        file_bytes = await tgfile.download_as_bytearray()
        
    elif update.message.photo:
        photo = update.message.photo[-1]
        tgfile = await context.bot.get_file(photo.file_id)
        file_bytes = await tgfile.download_as_bytearray()
        filename = "image.jpg"
        suffix = ".jpg"
        size_mb = len(file_bytes) / (1024 * 1024)
    else:
        await update.message.reply_text(_ui("الرجاء إرسال ملف أو صورة.", "Please send a file or image."))
        return

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
        "user_id": user_id
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

    # حفظ لغة المحتوى
    content_lang = "ar" if query.data == "lang_ar" else "en"
    LANG_MANAGER.set_content_lang(chat_id, content_lang)
    
    # عرض خيارات لغة الاختبار
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("العربية", callback_data="quiz_ar")],
        [InlineKeyboardButton("English", callback_data="quiz_en")],
    ])
    await query.edit_message_text(
        _ui("اختر لغة الأسئلة:", "Choose quiz language:"), 
        reply_markup=kb
    )
    sess["stage"] = "await_quiz_lang"

async def choose_quiz_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    sess = SESSIONS.get(chat_id)
    
    if not sess or sess.get("stage") != "await_quiz_lang":
        await query.edit_message_text(_ui("لا يوجد ملف قيد المعالجة.", "No pending file."))
        return

    # حفظ لغة الاختبار
    quiz_lang = "ar" if query.data == "quiz_ar" else "en"
    LANG_MANAGER.set_quiz_lang(chat_id, quiz_lang)
    
    sess["stage"] = "processing"
    await query.edit_message_text(_ui("جاري تحليل الملف وإعداده… ⏳", "Analyzing the file… ⏳"))

    # استخراج النص
    with tempfile.NamedTemporaryFile(delete=False, suffix=sess["suffix"]) as f:
        f.write(sess["file_bytes"])
        tmp_path = f.name

    try:
        content_lang = LANG_MANAGER.get_content_lang(chat_id)
        text = await extract_text_any(tmp_path, sess["suffix"], content_lang)
    except Exception as e:
        logger.error(f"Error extracting text: {e}")
        text = ""
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    text = _clean_text(text)
    if not text or len(text) < 400:
        await context.bot.send_message(chat_id=chat_id, text=_ui("تعذر استخراج نص كافٍ حتى بعد OCR. جرّب ملفًا أوضح.", "Couldn't extract enough text (even with OCR). Try a clearer file."))
        if chat_id in SESSIONS:
            del SESSIONS[chat_id]
        return

    await context.bot.send_message(chat_id=chat_id, text=_ui("جاري توليد أسئلة قوية بالذكاء الاصطناعي… ⏳", "Generating strong questions with AI… ⏳"))

    # توليد الأسئلة مع مراعاة اللغات
    content_lang = LANG_MANAGER.get_content_lang(chat_id)
    quiz_lang = LANG_MANAGER.get_quiz_lang(chat_id)
    
    try:
        questions = await build_quiz_from_text(text, content_lang=content_lang, quiz_lang=quiz_lang, target_total=40)
    except Exception as e:
        logger.error(f"Error generating questions: {e}")
        questions = []

    if not questions:
        await context.bot.send_message(chat_id=chat_id, text=_ui("تعذّر توليد أسئلة كافية. حاول ملفًا آخر.", "Failed to generate enough questions. Try another file."))
        if chat_id in SESSIONS:
            del SESSIONS[chat_id]
        return

    sess.update({
        "questions": questions, 
        "index": 0, 
        "score": 0, 
        "answers": {}, 
        "stage": "quiz"
    })
    await send_next_question(chat_id, context)

async def send_next_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    sess = SESSIONS.get(chat_id)
    if not sess or sess.get("stage") != "quiz":
        return

    if sess["index"] >= len(sess["questions"]):
        # انتهاء الاختبار
        score_text = _ui(
            f"انتهى الاختبار! نتيجتك: {sess['score']}/{len(sess['questions'])} ✅",
            f"Done! Your score: {sess['score']}/{len(sess['questions'])} ✅"
        )
        await context.bot.send_message(chat_id=chat_id, text=score_text)

        # تسجيل إكمال الاختبار
        user_id = sess.get("user_id")
        if user_id:
            log_event(user_id, "quiz_completed", {
                "score": sess['score'],
                "total": len(sess['questions'])
            })
            
            # تحديث إحصائيات المستخدم
            data = load_data()
            if str(user_id) in data["users"]:
                data["users"][str(user_id)]["quizzes_taken"] += 1
                data["users"][str(user_id)]["total_score"] += sess['score']
                data["users"][str(user_id)]["last_activity"] = datetime.now().isoformat()
                save_data(data)

        if chat_id in SESSIONS:
            del SESSIONS[chat_id]
        return

    q = sess["questions"][sess["index"]]
    try:
        msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=q["question"][:300],  # زيادة الحد إلى 300 حرف
            options=q["options"][:10],
            type=Poll.QUIZ,
            correct_option_id=int(q["correct"]),
            is_anonymous=False,
            explanation=_ui("إجابة صحيحة", "Correct"),
        )
        sess["answers"][msg.poll.id] = int(q["correct"])
        sess["index"] += 1
    except Exception as e:
        logger.error(f"Error sending poll: {e}")
        # تخطي السؤال في حالة خطأ
        sess["index"] += 1
        await send_next_question(chat_id, context)

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
                user_data["last_activity"] = datetime.now().isoformat()
                save_data(data)

            log_event(user_id, "quiz_answer", {
                "question_index": sess["index"],
                "is_correct": answer.option_ids and answer.option_ids[0] == correct
            })

            await send_next_question(chat_id, context)
            break

# ================= تشغيل البوت =================
def main():
    # تحميل البيانات الأولية
    refresh_user_lists()
    
    # بناء التطبيق
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # إضافة المعالجات
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("control", control_panel))
    
    app.add_handler(CallbackQueryHandler(handle_approval, pattern=r"^(approve|reject)_\d+$"))
    app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_(ar|en)$"))
    app.add_handler(CallbackQueryHandler(choose_quiz_language, pattern=r"^quiz_(ar|en)$"))
    app.add_handler(CallbackQueryHandler(handle_export, pattern=r"^export_(json|csv)$"))
    app.add_handler(CallbackQueryHandler(handle_control_buttons))
    
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()