""" Telegram Quiz Bot — generates MCQ & True/False from lecture files (PDF/TXT/DOCX)

• Tech: python-telegram-bot v20, pdfminer.six, python-docx (optional) • Features:

/start: greeting & how to use

Upload a PDF/TXT/DOCX → bot extracts text → generates questions

Sends Telegram "Quiz" polls (with correct answer)

Supports MCQ & True/False

Tracks score per user within the quiz session


How it works

1. Text extraction: pdfminer for PDFs, python-docx for DOCX, or raw TXT.


2. Question generation (rule-based):

True/False: flips numbers/negations/keywords to synthesize plausible false statements.

MCQ: detects simple "X is Y" patterns; builds distractors from other Y's.



3. Polls: uses send_poll(type="quiz"). Correct answers are hidden until user votes.


4. State: stores current quiz and answers in memory per chat. (For production, use a DB.)



NOTE: For higher quality questions, plug any LLM/provider in generate_with_llm() by returning the same schema. The rest of the pipeline will work unchanged.

Setup

1. pip install -r requirements.txt  (see string at bottom)


2. Set env var BOT_TOKEN from @BotFather


3. python bot.py



Security & limits

Files are stored temporarily on disk; the bot deletes them after parsing.

Max pages/bytes are limited to avoid abuse; tweak constants below. """


import os import re import json import random import string import tempfile from dataclasses import dataclass, field from typing import List, Dict, Optional, Tuple

from telegram import Update, Poll, constants from telegram.ext import ( ApplicationBuilder, CommandHandler, MessageHandler, PollAnswerHandler, ContextTypes, filters, )

Optional imports (lazy)

PDF_OK = True DOCX_OK = True try: from pdfminer.high_level import extract_text as pdf_extract_text except Exception: PDF_OK = False try: import docx  # python-docx except Exception: DOCX_OK = False

--------------------------- Config ---------------------------------

MAX_FILE_SIZE_MB = 12 MAX_TEXT_CHAR = 80_000 N_MCQ = 6 N_TF = 6 LANG = "ar"  # affects some prompts/text

--------------------------- Helpers --------------------------------

def clean_text(t: str) -> str: t = t.replace("\u200f", " ").replace("\u200e", " ") t = re.sub(r"[\t\xa0]+", " ", t) t = re.sub(r"\s+", " ", t) return t.strip()

def split_sentences(text: str) -> List[str]: # Simple sentence splitter that works ok for Arabic & English parts = re.split(r"(?<=[.!؟?;:])\s+", text) # Filter by length return [p.strip() for p in parts if 40 <= len(p.strip()) <= 220]

---------------------- Rule-based generators -----------------------

def make_true_false(sentences: List[str], k: int) -> List[Dict]: random.shuffle(sentences) qs = [] for s in sentences: if len(qs) >= k: break s_clean = s # Try to fabricate a false version false = None # 1) Flip numbers (e.g., 128 -> 129) nums = list(re.finditer(r"\d+", s_clean)) if nums: m = random.choice(nums) val = int(m.group()) new = str(val + random.choice([-2, -1, 1, 2])) false = s_clean[: m.start()] + new + s_clean[m.end() :] # 2) Toggle negatives / keywords if false is None: toggles = [ (r"\b(is|are|was|were)\b", lambda x: x.group(0) + " not"), (r"\b(ليس|ليست|لا)\b", "") , (r"\b(must|should)\b", "must not"), (r"\b(يجب|ينبغي)\b", "لا يجب"), ] for pat, repl in toggles: if re.search(pat, s_clean, flags=re.IGNORECASE): false = re.sub(pat, repl, s_clean, count=1, flags=re.IGNORECASE) break # 3) Swap a key noun with another from other sentences if false is None: words = [w for w in re.findall(r"[\w\u0600-\u06FF]+", s_clean) if len(w) > 4] if len(words) >= 2: w = random.choice(words) w2 = random.choice(words) if w2 != w: false = s_clean.replace(w, w2, 1) if false is None or false == s_clean: continue correct_is_true = random.choice([True, False]) question = s_clean if correct_is_true else false qs.append( { "type": "tf", "question": question, "options": ["True/صح", "False/خطأ"], "correct": 0 if correct_is_true else 1, } ) return qs

def make_mcq(sentences: List[str], k: int) -> List[Dict]: # Extract simple "X is Y" style facts → Q: What is X? pattern_en = re.compile(r"([A-Z\u0600-\u06FF][^.!?]{2,40})\s+(is|are|تعرف|هو|هي|يعرف|تسمى|يسمى)\s+([^.!?]{2,80})") candidates: List[Tuple[str, str]] = [] for s in sentences: m = pattern_en.search(s) if not m: continue x = clean_text(m.group(1)) y = clean_text(m.group(3)) if 2 <= len(x.split()) <= 8 and 1 <= len(y.split()) <= 12: candidates.append((x, y)) # Build distractor pool from Y's pool = [y for _, y in candidates] random.shuffle(candidates) qs = [] for x, y in candidates: if len(qs) >= k: break distractors = [p for p in pool if p != y] random.shuffle(distractors) distractors = distractors[:3] if len(distractors) < 3: # fabricate short distractors from frequent words extra = [ "layered security", "data confidentiality", "network perimeter", "transport protocol", "التشفير", "مصادقة المستخدم", "جدار ناري", ] while len(distractors) < 3: distractors.append(random.choice(extra)) options = distractors + [y] random.shuffle(options) correct_idx = options.index(y) q_text = f"What is {x}?" if re.search(r"[A-Za-z]", x) else f"ما هو/هي {x}?" qs.append({ "type": "mcq", "question": q_text, "options": options, "correct": correct_idx, }) return qs

-------------- (Optional) LLM integration placeholder --------------

def generate_with_llm(text: str, n_mcq: int, n_tf: int) -> Optional[List[Dict]]: """Hook for integrating any LLM provider. Return list of dicts: {type:'mcq'|'tf', question, options, correct(int)} Implement your provider call here and return None to fallback to rule-based. """ return None  # keep None to use rule-based by default

--------------------- Quiz Orchestration ---------------------------

@dataclass class Quiz: questions: List[Dict] index: int = 0 score: int = 0 answers: Dict[str, int] = field(default_factory=dict)  # poll_id -> correct option

SESSIONS: Dict[int, Quiz] = {}

def build_quiz_from_text(text: str, n_mcq: int = N_MCQ, n_tf: int = N_TF) -> List[Dict]: text = clean_text(text)[:MAX_TEXT_CHAR] sentences = split_sentences(text) # Try LLM first (if wired) llm = generate_with_llm(text, n_mcq, n_tf) if llm: return llm # Fallback: rule-based q_tf = make_true_false(sentences, n_tf) q_mcq = make_mcq(sentences, n_mcq) qs = q_mcq + q_tf random.shuffle(qs) return qs

------------------------ Telegram Handlers -------------------------

WELCOME_AR = ( "أهلاً! أرسل لي ملف المحاضرات (PDF أو DOCX أو TXT) وسأحوّله إلى اختبار " "اختيار من متعدد وصح/خطأ. بعد كل سؤال سترى الإجابة الصحيحة ونجمع نقاطك.\n\n" "أوامر مفيدة:\n" "/start — تعليمات\n" "/cancel — إلغاء الاختبار الحالي" ) WELCOME_EN = ( "Hi! Send me your lecture file (PDF/DOCX/TXT) and I'll generate a quiz " "(MCQ + True/False). You'll get your score as you answer.\n\n" "Commands: /start, /cancel" )

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE): lang = WELCOME_AR if LANG == "ar" else WELCOME_EN await update.message.reply_text(lang)

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE): chat_id = update.effective_chat.id if chat_id in SESSIONS: del SESSIONS[chat_id] await update.message.reply_text("تم إلغاء الاختبار الحالي ✅") else: await update.message.reply_text("لا يوجد اختبار جارٍ الآن.")

def _ext_from_docx(path: str) -> str: if not DOCX_OK: return "" d = docx.Document(path) return "\n".join([p.text for p in d.paragraphs])

def _ext_from_pdf(path: str) -> str: if not PDF_OK: return "" return pdf_extract_text(path)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE): doc = update.message.document if not doc: return size_mb = doc.file_size / (1024 * 1024) if size_mb > MAX_FILE_SIZE_MB: await update.message.reply_text(f"الحجم كبير ({size_mb:.1f}MB). أرسل ملف ≤ {MAX_FILE_SIZE_MB}MB.") return

filename = doc.file_name or "file"
suffix = os.path.splitext(filename)[1].lower()
if suffix not in [".pdf", ".txt", ".docx"]:
    await update.message.reply_text("الرجاء إرسال PDF أو DOCX أو TXT.")
    return

await update.message.reply_chat_action(constants.ChatAction.UPLOAD_DOCUMENT)

# Download
with tempfile.TemporaryDirectory() as td:
    local_path = os.path.join(td, filename)
    tgfile = await context.bot.get_file(doc.file_id)
    await tgfile.download_to_drive(local_path)

    # Extract text
    text = ""
    try:
        if suffix == ".pdf":
            text = _ext_from_pdf(local_path)
        elif suffix == ".docx":
            text = _ext_from_docx(local_path)
        elif suffix == ".txt":
            with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
    except Exception as e:
        text = ""

text = clean_text(text)
if not text or len(text) < 600:
    await update.message.reply_text(
        "تعذر قراءة النص من الملف. تأكد أنه ليس صوراً ممسوحة ضوئياً أو أرسل نصاً أطول."
    )
    return

await update.message.reply_text("جاري إعداد الأسئلة… ⏳")
questions = build_quiz_from_text(text)
if not questions:
    await update.message.reply_text("لم أتمكن من توليد أسئلة كافية. حاول ملفاً آخر.")
    return

chat_id = update.effective_chat.id
SESSIONS[chat_id] = Quiz(questions=questions)
await send_next_question(update, context)

async def send_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE): chat_id = update.effective_chat.id quiz = SESSIONS.get(chat_id) if not quiz: await update.message.reply_text("لا يوجد اختبار حالياً. أرسل ملفاً للبدء.") return if quiz.index >= len(quiz.questions): await update.message.reply_text(f"انتهى الاختبار! نتيجتك: {quiz.score}/{len(quiz.questions)} ✅") del SESSIONS[chat_id] return

q = quiz.questions[quiz.index]
msg = await context.bot.send_poll(
    chat_id=chat_id,
    question=q["question"][:255],
    options=q["options"][:10],
    type=Poll.QUIZ,
    correct_option_id=int(q["correct"]),
    is_anonymous=False,
    explanation=("إجابة صحيحة" if LANG == "ar" else "Correct"),
)
quiz.answers[msg.poll.id] = int(q["correct"])
quiz.index += 1

async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE): answer = update.poll_answer chat_id = answer.user.id  # we score per user; for group chats adapt this # find which quiz this poll belongs to correct = None for qchat, quiz in SESSIONS.items(): if answer.poll_id in quiz.answers: correct = quiz.answers[answer.poll_id] # score only for the voter who answered if answer.option_ids and answer.option_ids[0] == correct: quiz.score += 1 # After each vote, if this is the chat owner, send next # In private chats answer.user.id == chat id; in groups you may adjust logic try: if qchat == answer.user.id: fakemsg = type('obj', (), {'chat': type('obj', (), {'id': qchat}), 'from_user': None}) # send next by crafting a minimal Update-like wrapper await context.bot.send_message(chat_id=qchat, text=f"سجلنا إجابتك ✅") except Exception: pass break

--------------------------- Main -----------------------------------

async def post_vote_progress(context: ContextTypes.DEFAULT_TYPE): # This helper is not wired to a Job; kept for future progress posts pass

def main(): token = os.getenv("7761296155:AAFhJXXxLc5WxOWw5VN0Q82JrzywgFiI4_Q") if not token: print("ERROR: Set BOT_TOKEN env var from @BotFather") return

app = ApplicationBuilder().token(token).build()

app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("cancel", cmd_cancel))
app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
app.add_handler(PollAnswerHandler(receive_poll_answer))

print("Bot running… Press Ctrl+C to stop")
app.run_polling(close_loop=False)

if name == "main": main()

----------------------- requirements.txt ---------------------------

Save this block to requirements.txt if needed:

python-telegram-bot==20.7

pdfminer.six==20231228

python-docx==1.1.2

