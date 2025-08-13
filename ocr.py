import os
import base64
import json
import httpx

# يستخدم OCR.space كبوابة OCR مجانية (ينفع للصور وPDF متعددة الصفحات)
# أنشئ مفتاح مجاني من: https://ocr.space/ocrapi

async def ocr_space_file(path: str, lang: str) -> str:
    api_key = os.getenv("OCR_SPACE_API_KEY")
    if not api_key:
        return ""

    lang_code = "ara" if (lang or "ar").startswith("ar") else "eng"

    with open(path, "rb") as f:
        content = f.read()

    # نرسل الملف مباشرة كباينري
    data = {
        "language": lang_code,
        "isOverlayRequired": False,
        "OCREngine": 2,
        "scale": True,
        "detectOrientation": True,
    }

    files = {"file": (os.path.basename(path), content)}

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post("https://api.ocr.space/parse/image", data={**data, "apikey": api_key}, files=files)
        r.raise_for_status()
        obj = r.json()

    # جمع النصوص من كل الصفحات
    texts = []
    for res in obj.get("ParsedResults", []) or []:
        t = res.get("ParsedText", "")
        if t:
            texts.append(t)
    return "\n".join(texts)
