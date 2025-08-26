خimport random
from typing import List, Dict
from llm import ask_llm_big

REQUIRED_KEYS = {"type", "question", "options", "correct"}


def _normalize_item(it: Dict, lang: str) -> Dict:
    t = str(it.get("type", "mcq")).lower()
    q = str(it.get("question", "")).strip()
    opts = list(it.get("options", []))

    if t == "tf":
        opts = ["True/صح", "False/خطأ"] if lang == "ar" else ["True", "False"]
    else:
        # MCQ: تأكد من 4 خيارات
        if len(opts) < 4:
            # أكمل بعناصر محايدة (نادرًا ما تصل هنا إن كان LLM ملتزمًا)
            while len(opts) < 4:
                opts.append("—")
        elif len(opts) > 4:
            opts = opts[:4]

    c = it.get("correct", 0)
    try:
        c = int(c)
    except Exception:
        c = 0
    c = max(0, min(c, len(opts) - 1))

    return {"type": t, "question": q, "options": opts, "correct": c}


async def build_quiz_from_text(text: str, lang: str = "ar") -> List[Dict]:
    items = await ask_llm_big(text, lang=lang)
    cleaned = []
    seen_q = set()

    for it in items:
        if not isinstance(it, dict):
            continue
        if not REQUIRED_KEYS.issubset(it.keys()):
            continue
        obj = _normalize_item(it, lang)
        if not obj["question"] or len(obj["options"]) < 2:
            continue
        # إزالة التكرار
        key = (obj["type"], obj["question"])
        if key in seen_q:
            continue
        seen_q.add(key)
        cleaned.append(obj)

    random.shuffle(cleaned)
    # خذ العدد المطلوب أو أقل عند الحاجة
    return cleaned
