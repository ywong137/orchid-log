"""Kimi K3 vision/OCR client for photo analysis.

Reads config from environment (loaded from .env by app.py):
  KIMI_API_KEY, KIMI_BASE_URL, KIMI_MODEL
"""
import base64
import json
import os
import re
import urllib.request

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp", ".heic": "image/heic"}


class KimiError(Exception):
    pass


def configured():
    return bool(os.environ.get("KIMI_API_KEY"))


def chat(messages, max_tokens=2048):
    key = os.environ.get("KIMI_API_KEY")
    if not key:
        raise KimiError("KIMI_API_KEY not set (see .env)")
    base = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding")
    model = os.environ.get("KIMI_MODEL", "k3")
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/messages",
        data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        timeout = int(os.environ.get("KIMI_TIMEOUT", "180"))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
    except Exception as e:
        raise KimiError(f"K3 request failed: {e}")
    if resp.get("type") == "error" or "content" not in resp:
        raise KimiError(f"K3 error: {resp}")
    return "".join(c.get("text", "") for c in resp["content"]
                   if c.get("type") == "text")


PROMPT = """You are analyzing a photo for an orchid collection log app.

Known plants (number — name (genus)):
{plants}

Known genera: {genera}

Examine the photo carefully, including any plant tags, labels, and text overlaid on the image by the photographer (caretakers often add text containing the plant's tag number).

Respond with ONLY a JSON object (no markdown fences, no explanation):
{{
  "tag_number": <integer or null — a collection tag number visible in the photo, whether printed on a physical tag, handwritten, or in overlaid text>,
  "overlaid_text": <string or null — any text overlaid on the image itself>,
  "matched_number": <integer or null — if this is clearly one of the known plants above, its number>,
  "guess_genus": <string or null — the orchid genus if identifiable from the plant/flower; prefer a name from the known genera list>,
  "guess_name": <string or null — cultivar or hybrid name if readable from a tag in the photo>,
  "condition": <short string or null — visible state, e.g. "blooming", "buds", "new growth", "spike">,
  "caption": <short suggested caption for the photo, or null>,
  "confidence": <"high" | "medium" | "low" — how sure you are about matched_number>
}}"""


def analyze_photo(path, plants, genera):
    """Return K3's analysis dict for the image at path.

    plants: list of sqlite rows/dicts with number, name, genus_name.
    Raises KimiError on transport/API failure; returns {"_parse_error": ...}
    if the model output isn't valid JSON.
    """
    ext = os.path.splitext(path)[1].lower()
    media_type = MEDIA_TYPES.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        img = base64.b64encode(f.read()).decode()
    plant_lines = "\n".join(
        f"#{p['number']} — {p['name']} ({p['genus_name'] or 'unknown genus'})"
        for p in plants if p["number"] is not None) or "(none yet)"
    prompt = PROMPT.format(plants=plant_lines,
                           genera=", ".join(g["name"] for g in genera) or "(none)")
    text = chat([{"role": "user", "content": [
        {"type": "image",
         "source": {"type": "base64", "media_type": media_type, "data": img}},
        {"type": "text", "text": prompt}]}])
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"_parse_error": text[:500]}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"_parse_error": text[:500]}
