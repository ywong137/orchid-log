"""Transcribe paper log pages with K3 vision and import into orchid.db.

Usage:
  python3 import_logs.py transcribe [IMG_xxxx.JPG ...]   # K3 -> _analysis/*.json (checkpointed)
  python3 import_logs.py import                          # JSON -> db (dedupe + report)
"""
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps

from app import load_dotenv
from kimi import chat, KimiError

load_dotenv()

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "import-log")
WORK = os.path.join(SRC, "_work")
OUT = os.path.join(SRC, "_analysis")
DB = os.path.join(BASE, "orchid.db")

PROMPT = """This is a photo of a handwritten orchid care log page. Transcribe it into JSON.

The log is a ring binder of printed form pages, one plant per sheet: a header with genus, name, care instructions and a "# N" tag number, then dated entry rows. Photos were taken in strict page order, so a photo with no header is usually the BACK side (continuation rows) of the previous photo's sheet.
{hint}
Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "page_type": "plant_log" | "care_notes" | "other",
  "continued_from_prev": <true if this page has no header and appears to continue the previous sheet's log, else false>,
  "tag_number": <integer or null — the plant's collection tag number, from "# N" at the top>,
  "plant_name": <string or null — plant name / cultivar written on the page>,
  "genus": <string or null — orchid genus if written>,
  "page_notes": <string or null — non-care-instruction header notes such as repot info, location, source tags; do NOT copy the printed care instruction block>,
  "entries": [
    {{
      "date_raw": <string — the date exactly as written, e.g. "5/11">,
      "date": <string or null — best-guess ISO YYYY-MM-DD; these logs span late 2024 through 2026, infer the year from context and from date sequences on the page (dates on a page never go backwards)>,
      "watered": <true/false — from the circled "Watered: Yes/No">,
      "fertilized": <true/false — from the circled "Fertilized: Yes/No">,
      "condition": <string or null — observations about the plant's state>,
      "trimming": <string or null — trimming/repotting work notes>,
      "events": [<any of: "blooming", "misted", "new growth", "repotted", "moved", "treated", "spike", "buds", "trimmed">]
    }}
  ],
  "confidence": "high" | "medium" | "low"
}}

Every dated row is an entry, in page order. Transcribe faithfully — do not invent entries. If the page is not a plant log (e.g. general care instructions), use the appropriate page_type and leave entries empty, putting a summary in page_notes."""


def prep_image(src_path, dst_path):
    im = Image.open(src_path)
    im = ImageOps.exif_transpose(im)
    im.thumbnail((1600, 1600), Image.LANCZOS)
    im.convert("RGB").save(dst_path, "JPEG", quality=85)


def transcribe_one(fname, hint=""):
    out_path = os.path.join(OUT, fname.replace(".JPG", ".json"))
    work_path = os.path.join(WORK, fname.lower())
    if not os.path.exists(work_path):
        prep_image(os.path.join(SRC, fname), work_path)
    import base64
    img = base64.b64encode(open(work_path, "rb").read()).decode()
    messages = [{"role": "user", "content": [
        {"type": "image",
         "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
        {"type": "text", "text": PROMPT.format(hint=hint or "(no context about the previous page)")}]}]
    last_err = None
    for attempt in range(3):
        try:
            text = chat(messages, max_tokens=24576)
            if not text.strip():
                raise KimiError("empty response (token budget spent on thinking)")
            break
        except KimiError as e:
            last_err = e
            time.sleep(10 * (attempt + 1))
    else:
        return fname, f"ERROR: {last_err}"
    with open(out_path, "w") as f:
        f.write(text)
    return fname, "ok"


def parse_page(fname):
    import re
    raw = open(os.path.join(OUT, fname.replace(".JPG", ".json"))).read()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {}


def needs_retry(fname):
    """Headerless pages worth a second pass with previous-page context."""
    try:
        p = parse_page(fname)
    except Exception:
        return True
    return p.get("tag_number") is None and p.get("page_type") not in (
        "care_notes", None) and (p.get("entries") or p.get("page_type") == "other")


def hint_for(prev_fname):
    if not prev_fname:
        return ""
    try:
        p = parse_page(prev_fname)
    except Exception:
        return ""
    if p.get("page_type") != "plant_log" or p.get("tag_number") is None:
        return ""
    dates = [e.get("date") for e in p.get("entries", []) if e.get("date")]
    last = max(dates) if dates else "unknown"
    return (f"The previous photo showed plant #{p['tag_number']} "
            f"'{p.get('plant_name') or '?'}' (last entry date {last}). If this "
            "page has no header, it is likely the back of that sheet.")


def cmd_transcribe(files):
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    if not files:
        files = sorted(f for f in os.listdir(SRC) if f.endswith(".JPG"))
    todo = [f for f in files
            if not os.path.exists(os.path.join(OUT, f.replace(".JPG", ".json")))]
    print(f"pass 1: {len(todo)} of {len(files)} pages to transcribe", flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        n = 0
        for fname, status in ex.map(transcribe_one, todo):
            n += 1
            print(f"[{fname}] {status}  ({n}/{len(todo)})", flush=True)
    retry = [f for f in files if needs_retry(f)]
    print(f"pass 2 (with previous-page context): {len(retry)} pages", flush=True)
    for fname in retry:
        i = files.index(fname)
        hint = hint_for(files[i - 1]) if i > 0 else ""
        fname, status = transcribe_one(fname, hint)
        print(f"[{fname}] {status} (retry)", flush=True)


EVENT_CHOICES = {"blooming", "misted", "new growth", "repotted", "moved",
                 "treated", "spike", "buds", "trimmed"}


def norm_events(evts):
    out = []
    for e in evts or []:
        e = str(e).strip().lower()
        if e in EVENT_CHOICES and e not in out:
            out.append(e)
    return ",".join(out)


def similar_name(a, b):
    import re as _re
    na = _re.sub(r"[^a-z0-9 ]", "", (a or "").lower()).strip()
    nb = _re.sub(r"[^a-z0-9 ]", "", (b or "").lower()).strip()
    return bool(na) and bool(nb) and (na in nb or nb in na)


JUNK_NAMES = {"standard", "unnamed", "unknown"}


def norm_name(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (s or "").lower())).strip()


def name_sim(a, b):
    import difflib
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def load_pages():
    """Return ordered list of plant_log page dicts with normalized fields."""
    pages = []
    for f in sorted(os.listdir(OUT)):
        if not f.endswith(".json"):
            continue
        p = parse_page(f)
        if p.get("page_type") != "plant_log":
            continue
        t = p.get("tag_number")
        if isinstance(t, str):
            m = re.search(r"\d+", t)
            t = int(m.group(0)) if m else None
        name = (p.get("plant_name") or "").strip() or None
        if name and norm_name(name) in JUNK_NAMES:
            name = None
        pages.append({"file": f, "tag": t, "name": name,
                      "genus": (p.get("genus") or "").strip() or None,
                      "notes": (p.get("page_notes") or "").strip() or None,
                      "confidence": p.get("confidence"),
                      "entries": p.get("entries", [])})
    return pages


def cluster_pages(pages):
    """Group consecutive pages into per-sheet clusters (front + back sides)."""
    clusters = []
    for pg in pages:
        c = clusters[-1] if clusters else None
        same = False
        if c is not None:
            nm, cn = pg["name"], c["name"]
            if nm and cn:
                same = name_sim(nm, cn) >= 0.75
            elif nm and not cn:
                same = bool(pg["tag"]) and pg["tag"] in c["tags"]
                if same:
                    c["name"] = nm
            elif not nm:
                same = (not pg["tag"]) or pg["tag"] in c["tags"]
        if not same:
            clusters.append({"name": pg["name"], "tags": [], "pages": [],
                             "genera": [], "entries": 0, "aliases": set()})
            c = clusters[-1]
        if pg["name"] and c["name"] and pg["name"] != c["name"]:
            c["aliases"].add(pg["name"])
        if pg["tag"] and pg["tag"] not in c["tags"]:
            c["tags"].append(pg["tag"])
        if pg["genus"] and pg["genus"] not in c["genera"]:
            c["genera"].append(pg["genus"])
        c["pages"].append(pg)
        c["entries"] += len(pg["entries"])
    return clusters


def cluster_tag(c):
    """The plant's number: the front (first) page's tag, else most common."""
    for pg in c["pages"]:
        if pg["tag"]:
            return pg["tag"]
    return None


def cmd_import(dry=False):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    report = {"imported": 0, "dupes": 0, "conflicts": [], "new_plants": [],
              "tag_conflicts": [], "care_pages": [], "low_confidence": []}
    for f in sorted(os.listdir(OUT)):
        if not f.endswith(".json"):
            continue
        try:
            p = parse_page(f)
        except Exception:
            continue
        if p.get("page_type") != "plant_log":
            report["care_pages"].append(
                {"file": f, "type": p.get("page_type"),
                 "notes": (p.get("page_notes") or "")[:300]})
    all_plants = db.execute(
        "SELECT * FROM plants ORDER BY number IS NULL, number").fetchall()

    def find_plant(cname, ctag):
        """Returns (plant, how, note) — match by tag (name-checked) then name."""
        by_tag = None
        if ctag:
            for p in all_plants:
                if p["number"] == ctag:
                    by_tag = p
                    break
        best, best_sim = None, 0.0
        if cname:
            for p in all_plants:
                s = name_sim(p["name"], cname)
                if s > best_sim:
                    best, best_sim = p, s
        if by_tag and name_sim(by_tag["name"], cname) >= 0.5:
            return by_tag, "tag", None
        if best and best_sim >= 0.75:
            note = None
            if ctag and best["number"] and best["number"] != ctag:
                note = f"page tag #{ctag} != db #{best['number']} (matched by name)"
            return best, "name", note
        if by_tag:
            return None, "conflict", \
                f"tag #{ctag} is '{by_tag['name']}' in db but page says '{cname}'"
        return None, "new", None

    for c in cluster_pages(load_pages()):
        cname = c["name"]
        ctag = cluster_tag(c)
        files = [pg["file"] for pg in c["pages"]]
        if any(pg["confidence"] == "low" for pg in c["pages"]):
            report["low_confidence"].extend(files)
        plant, how, note = find_plant(cname, ctag)
        if dry:
            print(f"{'+'.join(f.replace('IMG_','').replace('.json','') for f in files)} "
                  f"| {c['entries']:3d}e | {cname!r} #{ctag} -> {how}: "
                  f"{dict(plant)['id'] if plant else '-'} {note or ''}")
            continue
        if note:
            report["tag_conflicts"].append(
                {"files": files, "name": cname, "note": note})
        if how == "conflict":
            report["tag_conflicts"].append(
                {"files": files, "name": cname, "note": note})
            ctag = None  # create numberless, flagged for review
        if not plant:
            genus_id = None
            for gname in c["genera"]:
                db.execute("INSERT OR IGNORE INTO genera (name) VALUES (?)",
                           (gname.title(),))
                row = db.execute("SELECT id FROM genera WHERE name LIKE ?",
                                 (gname,)).fetchone()
                if row:
                    genus_id = row["id"]
                    break
            dates = [e.get("date") for pg in c["pages"] for e in pg["entries"]
                     if e.get("date")]
            notes = " ".join(pg["notes"] for pg in c["pages"]
                             if pg["notes"]).strip()
            if how == "conflict":
                notes = (f"(page showed tag #{cluster_tag(c)} — needs review) "
                         + notes).strip()
            cur = db.execute(
                """INSERT INTO plants (number, genus_id, name, notes, created_at)
                   VALUES (?,?,?,?,?)""",
                (ctag, genus_id, cname or "Unnamed", notes or None,
                 min(dates) + " 00:00:00" if dates else None))
            plant = db.execute("SELECT * FROM plants WHERE id = ?",
                               (cur.lastrowid,)).fetchone()
            all_plants.append(plant)
            report["new_plants"].append(
                {"files": files, "number": ctag, "name": cname})
        else:
            if ctag and not plant["number"]:
                taken = any(p["number"] == ctag for p in all_plants
                            if p["id"] != plant["id"])
                if taken:
                    report["tag_conflicts"].append(
                        {"files": files, "name": cname,
                         "note": f"page tag #{ctag} already used by another plant"})
                else:
                    db.execute("UPDATE plants SET number = ? WHERE id = ?",
                               (ctag, plant["id"]))
                    plant = db.execute("SELECT * FROM plants WHERE id = ?",
                                       (plant["id"],)).fetchone()
            for pg in c["pages"]:
                if pg["notes"]:
                    existing = db.execute("SELECT notes FROM plants WHERE id = ?",
                                          (plant["id"],)).fetchone()["notes"] or ""
                    if pg["notes"].lower() not in existing.lower():
                        db.execute("UPDATE plants SET notes = ? WHERE id = ?",
                                   ((existing + " " + pg["notes"]).strip(),
                                    plant["id"]))
        for pg in c["pages"]:
            for e in pg["entries"]:
                d = e.get("date")
                if not d:
                    continue
                old = db.execute(
                    "SELECT * FROM entries WHERE plant_id = ? AND date = ?",
                    (plant["id"], d)).fetchone()
                cond = (e.get("condition") or "").strip()
                trim = (e.get("trimming") or "").strip()
                ev = norm_events(e.get("events"))
                if old:
                    report["dupes"] += 1
                    old_cond = (old["condition"] or "").strip()
                    if old_cond and cond and old_cond.lower() != cond.lower():
                        report["conflicts"].append({
                            "file": pg["file"], "plant": plant["number"],
                            "date": d, "db": old_cond, "page": cond})
                    continue
                db.execute(
                    """INSERT INTO entries
                       (plant_id, date, watered, fertilized, condition, trimming,
                        events, user)
                       VALUES (?,?,?,?,?,?,?, 'import')""",
                    (plant["id"], d, 1 if e.get("watered") else 0,
                     1 if e.get("fertilized") else 0, cond or None, trim or None,
                     ev))
                report["imported"] += 1
        db.commit()
    db.close()
    if not dry:
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "import":
        cmd_import(dry="--dry" in sys.argv)
    else:
        cmd_transcribe(sys.argv[2:] if len(sys.argv) > 2 else [])
