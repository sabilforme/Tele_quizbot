import os
import json
import math
import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
API_KEY = os.getenv("GROQ_API_KEY")

SYS_AR = (
    "أنت أستاذ جامعي خبير في إعداد اختبارات دقيقة وقوية.\n"
    "حوّل النص التعليمي إلى بنك أسئلة متنوع بمستويات صعوبة مختلفة (تذكر/فهم/تطبيق/تحليل).\n"
    "أنتج فقط JSON صالح دون أي شرح آخر."
)

SYS_EN = (
    "You are a university professor who crafts rigorous exams.\n"
    "Turn the educational text into a rich question bank across Bloom levels (remember/understand/apply/analyze).\n"
    "Return valid JSON only, with no explanations."
)

PROMPT_AR = (
    "حول النص التالي إلى مجموعة كبيرة من أسئلة الامتحان. المتطلبات:\n"
    "- النوعان المدعومان: 'mcq' و 'tf'.\n"
    "- MCQ: أربعة خيارات قوية ومُلبِّسة، واحدة صحيحة فقط. لا تستخدم 'كل ما سبق' أو 'لا شيء مما سبق'.\n"
    "- TF: الخيارات يجب أن تكون ['True/صح','False/خطأ'] مع صياغة دقيقة قابلة للتحقق من النص.\n"
    "- غطِّ جميع المحاور والجزئيات والمفاهيم والأمثلة والتعاريف والصيغ.\n"
    "- اجعل الأسئلة متنوعة الصعوبة وتشمل مفاهيم متداخلة وربط أفكار.\n"
    "- أعد الناتج JSON Array فقط بالصورة: \n"
    "[{\"type\":\"mcq\",\"question\":\"...\",\"options\":[\"Option1\",\"Option2\",\"Option3\",\"Option4\"],\"correct\":0}, {\"type\":\"tf\",\"question\":\"...\",\"options\":[\"True/صح\",\"False/خطأ\"],\"correct\":1}]\n"
    "النص:\n{chunk}\n"
)

PROMPT_EN = (
    "Convert the following text into a large set of exam questions. Requirements:\n"
    "- Supported types: 'mcq' and 'tf'.\n"
    "- MCQ: exactly 4 strong, confusing distractors; one correct. Avoid 'All of the above/None'.\n"
    "- TF: options must be ['True','False'] with statements verifiable from the text.\n"
    "- Cover all topics, definitions, formulas, edge cases, and examples.\n"
    "- Vary difficulty across Bloom levels and interleave concepts.\n"
    "- Return JSON Array ONLY in the form above.\n\n"
    "TEXT:\n{chunk}\n"
)

async def _ask_chunk(chunk: str, lang: str) -> list:
    if not API_KEY:
        return []
    sys = SYS_AR if lang == "ar" else SYS_EN
    prompt = PROMPT_AR if lang == "ar" else PROMPT_EN

    payload = {
        "model": MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": prompt.format(chunk=chunk[:6000])},
        ],
    }

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(GROQ_URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=payload)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]

    try:
        arr = json.loads(content)
        if isinstance(arr, list):
            return arr
    except Exception:
        pass
    return []


def _split_text(text: str, max_len: int = 8000):
    # تقسيم بسيط حسب الفقرات/الجمل
    parts = []
    buff = []
    count = 0
    for seg in text.split("\n"):
        seg = seg.strip()
        if not seg:
            continue
        if count + len(seg) > max_len:
            parts.append("\n".join(buff))
            buff, count = [seg], len(seg)
        else:
            buff.append(seg)
            count += len(seg)
    if buff:
        parts.append("\n".join(buff))
    return parts


async def ask_llm_big(text: str, lang: str, target_total: int = 40) -> list:
    chunks = _split_text(text)
    per_chunk_target = max(8, target_total // max(1, len(chunks)))
    out = []
    for ch in chunks:
        arr = await _ask_chunk(ch, lang)
        if not arr:
            continue
        out.extend(arr[: per_chunk_target + 4])  # خذ أكثر قليلًا
    return out[: target_total * 2]  # قص لاحقًا بعد التنظيف
