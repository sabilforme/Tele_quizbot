import os
import json
import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
API_KEY = os.getenv("GROQ_API_KEY")

SYS_AR = (
    "أنت أستاذ جامعي خبير في إعداد اختبارات شاملة ودقيقة.\n"
    "حوّل النص التعليمي إلى بنك أسئلة يغطي جميع التفاصيل والمفاهيم والصيغ والتعاريف والأمثلة بشكل كامل بحيث تغني الطالب عن المذاكرة.\n"
    "يجب أن تكون صياغة السؤال باللغة العربية مع إبقاء المصطلحات العلمية بالإنجليزية كما هي.\n"
    "الاختيارات للأسئلة من نوع MCQ يجب أن تكون باللغة الإنجليزية فقط، مع 4 خيارات قوية ومُلبِّسة وإجابة واحدة صحيحة.\n"
    "الأسئلة يجب أن تشمل جميع المستويات (تذكر، فهم، تطبيق، تحليل) وتكون متنوعة في الصعوبة.\n"
    "أنتج فقط JSON صالح دون أي شرح إضافي."
)

SYS_EN = (
    "You are a university professor who crafts rigorous exams.\n"
    "Turn the educational text into a rich question bank across Bloom levels (remember/understand/apply/analyze).\n"
    "Return valid JSON only, with no explanations."
)

PROMPT_AR = (
    "حوّل النص التالي إلى مجموعة كبيرة من أسئلة امتحان شاملة. المتطلبات:\n"
    "- الأنواع المدعومة: 'mcq' و 'tf'.\n"
    "- MCQ: أربعة خيارات قوية بالإنجليزية فقط، واحدة صحيحة. لا تستخدم 'All of the above' أو 'None of the above'.\n"
    "- TF: الاختيارات يجب أن تكون ['True/صح','False/خطأ'] مع صياغة دقيقة قابلة للتحقق من النص.\n"
    "- يجب أن تغطي الأسئلة كل النقاط والمفاهيم والتعاريف والصيغ والأمثلة الواردة في النص.\n"
    "- اجعل صياغة السؤال بالعربية مع إبقاء المصطلحات العلمية بالإنجليزية.\n"
    "- اجعل الأسئلة متنوعة في الصعوبة وتشمل مستويات (تذكر/فهم/تطبيق/تحليل).\n"
    "- أعد الناتج في شكل JSON Array فقط مثل:\n"
    "[{{\"type\":\"mcq\",\"question\":\"...\",\"options\":[\"Option1\",\"Option2\",\"Option3\",\"Option4\"],\"correct\":0}},"
    "{{\"type\":\"tf\",\"question\":\"...\",\"options\":[\"True/صح\",\"False/خطأ\"],\"correct\":1}}]\n"
    "النص:\n{chunk}"
)

PROMPT_EN = (
    "Convert the following text into a large set of exam questions. Requirements:\n"
    "- Supported types: 'mcq' and 'tf'.\n"
    "- MCQ: exactly 4 strong, confusing distractors; one correct. Avoid 'All of the above/None'.\n"
    "- TF: options must be ['True','False'] with statements verifiable from the text.\n"
    "- Cover all topics, definitions, formulas, edge cases, and examples.\n"
    "- Vary difficulty across Bloom levels and interleave concepts.\n"
    "- Return JSON Array ONLY in the form above:\n"
    "[{{\"type\":\"mcq\",\"question\":\"...\",\"options\":[\"Option1\",\"Option2\",\"Option3\",\"Option4\"],\"correct\":0}},"
    "{{\"type\":\"tf\",\"question\":\"...\",\"options\":[\"True\",\"False\"],\"correct\":1}}]\n"
    "TEXT:\n{chunk}"
)

async def _ask_chunk(chunk: str, lang: str) -> list:
    if not API_KEY:
        return []
    sys_msg = SYS_AR if lang == "ar" else SYS_EN
    prompt = PROMPT_AR if lang == "ar" else PROMPT_EN

    # استخدم replace بدلاً من format لتجنب مشاكل الأقواس
    prompt_text = prompt.replace("{chunk}", chunk)

    payload = {
        "model": MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt_text},
        ],
    }

    async with httpx.AsyncClient(timeout=300) as client:  # زيادة الوقت للملفات الكبيرة
        r = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=payload
        )
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


def _split_text(text: str, max_len: int = None):
    # إذا max_len=None، لا يوجد حد
    if max_len is None:
        return [text]

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


async def ask_llm_big(text: str, lang: str, target_total: int = None) -> list:
    chunks = _split_text(text, max_len=None)  # بدون حد
    out = []
    for ch in chunks:
        arr = await _ask_chunk(ch, lang)
        if not arr:
            continue
        out.extend(arr)
    return out  # بدون أي حد على عدد الأسئلة