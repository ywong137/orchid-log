"""Scan orchid photos (import-pics/) for tag numbers / overlaid numbers with K3,
guess genus visually, and attach matched photos to plants in orchid.db.

  python3 import_pics.py scan [IMG_xxxx.JPG ...]   # K3 -> _analysis/*.json (checkpointed)
  python3 import_pics.py attach                    # match + attach to plants, report
"""
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps

from app import load_dotenv
from kimi import chat, KimiError

load_dotenv()
os.environ.setdefault("KIMI_TIMEOUT", "420")

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "import-pics")
WORK = os.path.join(SRC, "_work")
OUT = os.path.join(SRC, "_analysis")
DB = os.path.join(BASE, "orchid.db")
UPLOADS = os.path.join(BASE, "uploads")

PROMPT = """This is a photo of an orchid from a home collection. Answer with ONLY a JSON object (no markdown):

{
  "tag_number": <integer or null — the plant's collection number, shown either on a physical plant tag in the pot/photo OR as text the owner added onto the photo (often a big handwritten number in a corner)>,
  "number_source": <"physical_tag" | "overlaid_text" | null>,
  "genus_guess": <string or null — your best visual guess of the orchid genus from the flower/plant morphology. Pick from: {genera}, or another genus if clearly none of those>,
  "genus_confidence": <"high" | "medium" | "low">,
  "condition": <short string or null — visible state e.g. "blooming", "buds", "spike", "new growth", "healthy", "struggling">,
  "caption": <short suggested photo caption, or null>
}"""


def prep(src_path, dst_path):
    im = Image.open(src_path)
    im = ImageOps.exif_transpose(im)
    im.thumbnail((1600, 1600), Image.LANCZOS)
    im.convert("RGB").save(dst_path, "JPEG", quality=85)


def work_image(fname):
    """Path to a downscaled JPEG for fname, converting HEIC via sips if needed."""
    os.makedirs(WORK, exist_ok=True)
    dst = os.path.join(WORK, os.path.splitext(fname)[0] + ".jpg")
    if os.path.exists(dst):
        return dst
    src = os.path.join(SRC, fname)
    if fname.lower().endswith(".heic"):
        tmp = os.path.join(WORK, "_sips_" + fname + ".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", src, "--out", tmp],
                       check=True, capture_output=True)
        prep(tmp, dst)
        os.remove(tmp)
    else:
        prep(src, dst)
    return dst


def genera_list():
    db = sqlite3.connect(DB)
    names = [r[0] for r in db.execute("SELECT name FROM genera ORDER BY name")]
    db.close()
    return ", ".join(names)


def scan_one(fname):
    out_path = os.path.join(OUT, os.path.splitext(fname)[0] + ".json")
    if os.path.exists(out_path):
        return fname, "cached"
    img = base64.b64encode(open(work_image(fname), "rb").read()).decode()
    msg = [{"role": "user", "content": [
        {"type": "image",
         "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
        {"type": "text", "text": PROMPT.replace("{genera}", genera_list())}]}]
    last = None
    for attempt in range(3):
        try:
            text = chat(msg, max_tokens=8192)
            if not text.strip():
                raise KimiError("empty response")
            m = re.search(r"\{.*\}", text, re.S)
            r = json.loads(m.group(0))
            with open(out_path, "w") as f:
                json.dump(r, f)
            return fname, "ok"
        except (KimiError, json.JSONDecodeError, AttributeError,
                subprocess.CalledProcessError) as e:
            last = e
            time.sleep(8 * (attempt + 1))
    return fname, f"ERROR: {last}"


def cmd_scan(files):
    os.makedirs(OUT, exist_ok=True)
    if not files:
        files = sorted(f for f in os.listdir(SRC)
                       if f.lower().endswith((".jpg", ".jpeg", ".heic"))
                       and not f.startswith("_"))
    todo = [f for f in files
            if not os.path.exists(
                os.path.join(OUT, os.path.splitext(f)[0] + ".json"))]
    print(f"{len(todo)} of {len(files)} photos to scan", flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        n = 0
        for fname, status in ex.map(scan_one, todo):
            n += 1
            print(f"[{fname}] {status} ({n}/{len(todo)})", flush=True)


def cmd_attach():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    report = {"attached": [], "genus_mismatch": [], "no_number": [],
              "unknown_number": [], "errors": []}
    for jf in sorted(os.listdir(OUT)):
        if not jf.endswith(".json"):
            continue
        stem = jf[:-5]
        try:
            r = json.load(open(os.path.join(OUT, jf)))
        except Exception as e:
            report["errors"].append((jf, str(e)))
            continue
        num = r.get("tag_number")
        if isinstance(num, str):
            m = re.search(r"\d+", num)
            num = int(m.group(0)) if m else None
        if not isinstance(num, int):
            report["no_number"].append({
                "file": stem, "genus_guess": r.get("genus_guess"),
                "confidence": r.get("genus_confidence")})
            continue
        plant = db.execute("SELECT * FROM plants WHERE number = ?",
                           (num,)).fetchone()
        if not plant:
            report["unknown_number"].append({"file": stem, "number": num,
                                             "genus_guess": r.get("genus_guess")})
            continue
        src_img = work_image(
            next(f for f in os.listdir(SRC)
                 if os.path.splitext(f)[0] == stem))
        fname = f"plant{num}-{stem.lower()}.jpg"
        if not os.path.exists(os.path.join(UPLOADS, fname)):
            shutil.copy(src_img, os.path.join(UPLOADS, fname))
        existing = db.execute("SELECT * FROM photos WHERE filename = ?",
                              (fname,)).fetchone()
        if not existing:
            headline = 0 if db.execute(
                "SELECT id FROM photos WHERE plant_id = ? AND is_headline = 1",
                (plant["id"],)).fetchone() else 1
            db.execute(
                """INSERT INTO photos (plant_id, filename, caption, uploaded_by, is_headline)
                   VALUES (?,?,?,?,?)""",
                (plant["id"], fname, r.get("caption"), "import", headline))
        db.commit()
        genus_db = db.execute(
            "SELECT g.name FROM genera g JOIN plants p ON p.genus_id = g.id "
            "WHERE p.id = ?", (plant["id"],)).fetchone()
        guess = (r.get("genus_guess") or "").strip()
        mismatch = None
        if guess and genus_db:
            a = re.sub(r"[^a-z]", "", guess.lower())
            b = re.sub(r"[^a-z]", "", genus_db["name"].lower())
            if a and b and a not in b and b not in a:
                mismatch = {"file": stem, "number": num, "guess": guess,
                            "db_genus": genus_db["name"],
                            "confidence": r.get("genus_confidence"),
                            "plant_id": plant["id"]}
                report["genus_mismatch"].append(mismatch)
        report["attached"].append({
            "file": stem, "number": num, "name": plant["name"],
            "source": r.get("number_source"), "genus_guess": guess or None,
            "condition": r.get("condition"), "flagged": plant["needs_review"]})
    db.close()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "attach":
        cmd_attach()
    else:
        cmd_scan(sys.argv[2:] if len(sys.argv) > 2 else [])
