"""Resolve tag-number conflicts using the authoritative circled top-right number.

  python3 resolve_conflicts.py read    # K3 reads corner of each worklist page (checkpointed)
  python3 resolve_conflicts.py apply   # reconcile + fix db (renumber / merge) + report
"""
import base64
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from app import load_dotenv
from kimi import chat, KimiError
from import_logs import (OUT, SRC, WORK, DB, load_pages, cluster_pages,
                         cluster_tag, name_sim)

load_dotenv()
os.environ.setdefault("KIMI_TIMEOUT", "420")

CIRCLED_DIR = os.path.join(SRC, "_circled")
WORKLIST = os.path.join(SRC, "_resolve_worklist.json")

PROMPT = """Look at this orchid log page photo. I need three specific answers:

1. circled_number: In the TOP-RIGHT corner of the page there may be a plant number, sometimes written as "# N" and/or with a circle around it. That is the plant's official tag number. Give the integer, or null if there is no number in the top-right.

2. tab_color: the color of the tab divider attached to THIS page's own leaf (along the right/fore-edge), or null.

3. tab_number: the number printed on THAT tab (the one belonging to this leaf), or null. Other pages' tabs may be visible along the edge — ignore those and report only this leaf's own tab.

Respond with ONLY a JSON object:
{"circled_number": <int|null>, "tab_color": <string|null>, "tab_number": <int|null>}"""


def read_one(jf):
    out_path = os.path.join(CIRCLED_DIR, jf)
    if os.path.exists(out_path):
        return jf, "cached"
    img_name = jf.replace(".json", ".jpg")
    work_path = os.path.join(WORK, img_name.lower())
    if not os.path.exists(work_path):
        work_path = os.path.join(WORK, img_name)
    img = base64.b64encode(open(work_path, "rb").read()).decode()
    msg = [{"role": "user", "content": [
        {"type": "image",
         "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
        {"type": "text", "text": PROMPT}]}]
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
            return jf, "ok"
        except (KimiError, json.JSONDecodeError, AttributeError) as e:
            last = e
            time.sleep(8 * (attempt + 1))
    return jf, f"ERROR: {last}"


def cmd_read():
    os.makedirs(CIRCLED_DIR, exist_ok=True)
    work = json.load(open(WORKLIST))
    todo = [jf for jf in work if not os.path.exists(os.path.join(CIRCLED_DIR, jf))]
    print(f"{len(todo)} of {len(work)} pages to read", flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        n = 0
        for jf, status in ex.map(read_one, todo):
            n += 1
            print(f"[{jf}] {status} ({n}/{len(todo)})", flush=True)


def circled_of(jf):
    path = os.path.join(CIRCLED_DIR, jf)
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            pass
    return {}


def cmd_apply():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    pages = load_pages()
    clusters = cluster_pages(pages)
    report = {"moves": [], "created": [], "merged": [], "renumbered": [],
              "husks_deleted": [], "confirmed": 0, "unresolved": [],
              "mixed_name_groups": [], "tab_flags": []}

    def plant_by_number(n):
        return db.execute("SELECT * FROM plants WHERE number = ?", (n,)).fetchone()

    def cluster_plant(c):
        """Reproduce the plant a cluster was imported into."""
        ctag, cname = cluster_tag(c), c["name"]
        if ctag:
            p = plant_by_number(ctag)
            if p and name_sim(p["name"], cname) >= 0.5:
                return p
        best, bs = None, 0.0
        for p in db.execute("SELECT * FROM plants").fetchall():
            s = name_sim(p["name"], cname)
            if s > bs:
                best, bs = p, s
        return best if bs >= 0.75 else None

    def move_page_entries(pg, src, dst):
        """Re-assign one page's entries from plant src to plant dst (date-deduped)."""
        moved = 0
        for e in pg["entries"]:
            d = e.get("date")
            if not d:
                continue
            row = db.execute(
                "SELECT * FROM entries WHERE plant_id = ? AND date = ?",
                (src["id"], d)).fetchone()
            if not row:
                continue
            dupe = db.execute(
                "SELECT id FROM entries WHERE plant_id = ? AND date = ?",
                (dst["id"], d)).fetchone()
            if dupe:
                db.execute("DELETE FROM entries WHERE id = ?", (row["id"],))
            else:
                db.execute("UPDATE entries SET plant_id = ? WHERE id = ?",
                           (dst["id"], row["id"]))
                moved += 1
        return moved

    # ---- phase 1: page-level circled-number moves --------------------------
    for c in clusters:
        src = cluster_plant(c)
        if not src:
            continue
        for pg in c["pages"]:
            r = circled_of(pg["file"])
            circ = r.get("circled_number")
            if not isinstance(circ, int):
                continue
            if src["number"] == circ:
                continue
            dst = plant_by_number(circ)
            if not dst:
                gname = (c["genera"] or [None])[0]
                gid = None
                if gname:
                    row = db.execute("SELECT id FROM genera WHERE name LIKE ?",
                                     (gname,)).fetchone()
                    gid = row["id"] if row else None
                dates = [e.get("date") for e in pg["entries"] if e.get("date")]
                cur = db.execute(
                    """INSERT INTO plants (number, genus_id, name, notes, created_at)
                       VALUES (?,?,?,?,?)""",
                    (circ, gid, pg["name"] or c["name"] or "Unnamed",
                     "(number from circled top-right)", 
                     min(dates) + " 00:00:00" if dates else None))
                dst = plant_by_number(circ)
                report["created"].append(
                    {"number": circ, "name": pg["name"] or c["name"],
                     "from": pg["file"]})
            if name_sim(dst["name"], pg["name"] or c["name"] or "") < 0.5:
                report["mixed_name_groups"].append(
                    {"file": pg["file"], "circled": circ,
                     "page_name": pg["name"] or c["name"],
                     "dst_name": dst["name"]})
            n = move_page_entries(pg, src, dst)
            report["moves"].append({
                "file": pg["file"], "from": f"#{src['number'] or '–'} {src['name']}",
                "to": f"#{circ} {dst['name']}", "entries": n})
        db.commit()

    # ---- phase 2: cluster-level majority renumber/merge --------------------
    for c in clusters:
        src = cluster_plant(c)
        if not src or src["number"] is None:
            continue
        votes = {}
        for pg in c["pages"]:
            circ = circled_of(pg["file"]).get("circled_number")
            if isinstance(circ, int):
                votes[circ] = votes.get(circ, 0) + 1
        if not votes:
            continue
        winner = max(votes, key=votes.get)
        if winner == src["number"]:
            report["confirmed"] += 1
            continue
        owner = plant_by_number(winner)
        if owner and owner["id"] != src["id"]:
            for e in db.execute("SELECT * FROM entries WHERE plant_id = ?",
                                (src["id"],)).fetchall():
                dupe = db.execute(
                    "SELECT id FROM entries WHERE plant_id = ? AND date = ?",
                    (owner["id"], e["date"])).fetchone()
                if dupe:
                    db.execute("DELETE FROM entries WHERE id = ?", (e["id"],))
                else:
                    db.execute("UPDATE entries SET plant_id = ? WHERE id = ?",
                               (owner["id"], e["id"]))
            db.execute("UPDATE photos SET plant_id = ? WHERE plant_id = ?",
                       (owner["id"], src["id"]))
            if src["notes"] and src["notes"].lower() not in \
                    (owner["notes"] or "").lower():
                db.execute("UPDATE plants SET notes = ? WHERE id = ?",
                           (((owner["notes"] or "") + " " + src["notes"]).strip(),
                            owner["id"]))
            db.execute("DELETE FROM plants WHERE id = ?", (src["id"],))
            report["merged"].append(
                {"name": c["name"], "was": src["number"], "into": winner})
        else:
            db.execute("UPDATE plants SET number = ? WHERE id = ?",
                       (winner, src["id"]))
            report["renumbered"].append(
                {"name": c["name"], "from": src["number"], "to": winner})
        db.commit()

    # ---- phase 3: husk cleanup + unresolved + tab flags --------------------
    for p in db.execute("SELECT * FROM plants").fetchall():
        ne = db.execute("SELECT COUNT(*) c FROM entries WHERE plant_id = ?",
                        (p["id"],)).fetchone()["c"]
        np_ = db.execute("SELECT COUNT(*) c FROM photos WHERE plant_id = ?",
                         (p["id"],)).fetchone()["c"]
        if ne == 0 and np_ == 0 and p["created_at"] and \
                p["created_at"] >= "2026-08-15":
            db.execute("DELETE FROM plants WHERE id = ?", (p["id"],))
            report["husks_deleted"].append(
                {"number": p["number"], "name": p["name"]})
    db.commit()

    for c in clusters:
        has_circ = any(isinstance(circled_of(pg["file"]).get("circled_number"), int)
                       for pg in c["pages"])
        if not has_circ and len(set(c["tags"])) > 1:
            report["unresolved"].append({
                "files": [pg["file"] for pg in c["pages"]], "name": c["name"],
                "tags_seen": c["tags"],
                "why": "no circled number; tab reads disagree"})
    by_color = {}
    for pg in pages:
        r = circled_of(pg["file"])
        if r.get("tab_color") and isinstance(r.get("tab_number"), int) \
                and not isinstance(r.get("circled_number"), int):
            by_color.setdefault(r["tab_color"].lower(), []).append(
                (pg["file"], r["tab_number"]))
    for color, seq in sorted(by_color.items()):
        nums = [n for _, n in seq]
        if not all(a <= b for a, b in zip(nums, nums[1:])):
            report["tab_flags"].append(
                {"color": color,
                 "sequence": [{"file": f, "tab": n} for f, n in seq]})
    db.close()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        cmd_apply()
    else:
        cmd_read()
