import os
import json
import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
API_KEY = os.getenv("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")

SYSTEM_AR = (
    "أنت مساعد خبير في تحويل النصوص التعليمية إلى اختبارات."
    " أنشئ أسئلة دقيقة وواضحة من النص، وتجنب الأسئلة المبهمة."
    " أعد الناتج بصيغة JSON فقط بدون أي شرح إضافي."
)

USER_PROMPT = (
    "حوّل النص التالي إلى أسئلة اختبار متنوعة (اختيار من متعدد وصح/خطأ).\n"
    "الشروط:\n"
    "- عدد الأسئلة من 8 إلى 14.\n"
    "- لكل سؤال MCQ أربعة خيارات.\n"
    "- صح/خطأ يجب أن تكون الخيارات ['True/صح','False/خطأ'].\n"
    "- أعد الناتج JSON صالحًا تمامًا بالقالب: \n"
    "[{'type':'mcq'|'tf','question':'...','options':['..','..','..','..'],'correct':0..3}]\n"
    "- اجعل الأسئلة مباشرة من المعلومات الواضحة في النص فقط.\n\n"
    "النص:\n{chunk}\n"
)

async def ask_llm(text: str) -> list:
    if not API_KEY:
        return []

    payload = {
        "model": MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_AR},
            {"role": "user", "content": USER_PROMPT.format(chunk=text[:4500])},
        ],
        "response_format": {"type": "json_object"}
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(GROQ_URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=payload)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]

    try:
        obj = json.loads(content)
        arr = obj.get("items") if isinstance(obj, dict) else obj
        if isinstance(arr, list):
            return arr
    except Exception:
        pass
    return []