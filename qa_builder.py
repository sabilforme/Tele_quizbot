import random
from typing import List, Dict
from llm import ask_llm

REQUIRED_KEYS = {"type", "question", "options", "correct"}


def _normalize_item(it: Dict) -> Dict:
    t = it.get("type", "mcq").lower()
    q = str(it.get("question", "")).strip()
    opts = list(it.get("options", []))
    if t == "tf":
        opts = ["True/صح", "False/خطأ"]
    if len(opts) == 2 and t != "tf":
        while len(opts) < 4:
            opts.append("—")
    c = int(it.get("correct", 0))
    c = max(0, min(c, len(opts) - 1))
    return {"type": t, "question": q, "options": opts, "correct": c}


async def build_quiz_from_text(text: str, target_total: int = 12) -> List[Dict]:
    items = await ask_llm(text)
    cleaned = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not REQUIRED_KEYS.issubset(it.keys()):
            continue
        obj = _normalize_item(it)
        if not obj["question"] or len(obj["options"]) < 2:
            continue
        cleaned.append(obj)

    random.shuffle(cleaned)
    cleaned = cleaned[:target_total]

    if not cleaned:
        return []
    return cleaned