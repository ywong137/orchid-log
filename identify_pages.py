"""Second pass: identify headerless pages (tab numbers on page edges / continuation)."""
import base64
import json
import os
import re
import sys

from app import load_dotenv
from kimi import chat, KimiError

load_dotenv()
os.environ.setdefault("KIMI_TIMEOUT", "420")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "import-log", "_analysis")
WORK = os.path.join(BASE, "import-log", "_work")

ID_PROMPT = """This photo is one page from an orchid care log binder, photographed in strict page order. The automatic first pass found no "# N" plant tag number in the page header.

The previous photo showed plant {prev}.

Look carefully:
1. Along the right edge of the binder for numbered tab dividers (e.g. tabs reading 20, 22, 83, 29...). The tab attached to THIS page indicates its plant number — pick the tab whose leaf this page belongs to if visible.
2. Anywhere else a plant number might be written or printed.
3. Whether this page is simply the back side (continuation rows, no header) of the previous photo's sheet.

Respond with ONLY a JSON object:
{{
  "tag_number": <integer or null>,
  "continued_from_prev": <true/false>,
  "plant_name": <string or null — any plant name written on the page>,
  "reasoning": "<one short sentence>"
}}"""


def main():
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".json"))
    pages = {}
    for f in files:
        pages[f] = json.loads(open(os.path.join(OUT, f)).read())
    # map each file to previous page's (tag, name)
    prev_of = {}
    last = None
    for f in files:
        prev_of[f] = last
        p = pages[f]
        if p.get("page_type") == "plant_log" and p.get("tag_number") is not None:
            t = p["tag_number"]
            if isinstance(t, str):
                m = re.search(r"\d+", t)
                t = int(m.group(0)) if m else None
            if t is not None:
                last = (t, p.get("plant_name"))
    targets = [f for f in files
               if pages[f].get("page_type") == "plant_log"
               and pages[f].get("tag_number") is None
               and "_id_pass" not in pages[f]]
    print(f"{len(targets)} headerless pages", flush=True)
    for f in targets:
        prev = prev_of[f]
        prev_s = f"#{prev[0]} '{prev[1]}'" if prev else "(unknown)"
        work = os.path.join(WORK, f.replace(".json", ".jpg").replace("IMG", "img"))
        if not os.path.exists(work):
            work = os.path.join(WORK, f.replace(".json", ".jpg"))
        img = base64.b64encode(open(work, "rb").read()).decode()
        msg = [{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
            {"type": "text", "text": ID_PROMPT.format(prev=prev_s)}]}]
        try:
            text = chat(msg, max_tokens=8192)
            m = re.search(r"\{.*\}", text, re.S)
            r = json.loads(m.group(0))
        except (KimiError, json.JSONDecodeError, AttributeError) as e:
            print(f"{f}: ERROR {e}", flush=True)
            continue
        print(f"{f}: tag={r.get('tag_number')} cont={r.get('continued_from_prev')} "
              f"name={r.get('plant_name')!r} — {r.get('reasoning')}", flush=True)
        p = pages[f]
        changed = False
        if isinstance(r.get("tag_number"), int):
            p["tag_number"] = r["tag_number"]
            changed = True
        if r.get("continued_from_prev"):
            p["continued_from_prev"] = True
            changed = True
        if not p.get("plant_name") and r.get("plant_name"):
            p["plant_name"] = r["plant_name"]
            changed = True
        if changed:
            p["_id_pass"] = r.get("reasoning", "")
            with open(os.path.join(OUT, f), "w") as fh:
                json.dump(p, fh, indent=2)


if __name__ == "__main__":
    main()
