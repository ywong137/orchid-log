"""Follow-up to import_pics attach:
1. Create flagged stub plants for photographed numbers not in the db.
2. Append photo genus evidence to existing review notes.
3. Flag high-confidence cross-tribe genus mismatches.
"""
import json
import os
import re
import sqlite3

DB = "orchid.db"
OUT = os.path.join("import-pics", "_analysis")
report = json.load(open(os.path.join("import-pics", "_attach-report.json")))

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# synonym groups where genus confusion is expected/meaningless
SYN = [{"phalaenopsis", "doritaenopsis"},
       {"oncidium", "odontocidium", "oncostele", "miltonidium", "aliceara",
        "miltonia", "brassia", "colmanara", "beallara", "miltoniopsis",
        "oncidiinae", "zygopetalum", "zygopabstia", "rhynchostylis"},
       {"cattleya", "brassocattleya", "brassolaeliocattleya", "brasslova",
        "rhyncattleanthe", "rhyncanthe", "epicattleya", "cattlianthe",
        "brassavola", "encyclia", "brassavola", "laelia", "coryanthes",
        "arthurara", "grammatophyllum"}]


def synonymous(a, b):
    a, b = a.lower(), b.lower()
    return any(a in g and b in g for g in SYN)


# --- 1. stub plants for photographed-but-unknown numbers --------------------
for u in report["unknown_number"]:
    num = u["number"]
    if num and num > 200:
        print(f"SKIP suspicious number {num} ({u['file']})")
        continue
    guess = u["genus_guess"]
    gid = None
    if guess:
        row = db.execute("SELECT id FROM genera WHERE name LIKE ?",
                         (guess,)).fetchone()
        gid = row["id"] if row else None
    note = (f"Photographed with physical tag #{num} (import-pics/{u['file']}) "
            f"but no such plant in the logs — stub created; "
            f"genus guessed as {guess or 'unknown'} from the photo")
    cur = db.execute(
        "INSERT INTO plants (number, genus_id, name, notes, needs_review, review_note)"
        " VALUES (?,?,?,?,1,?)", (num, gid, "Unnamed", note, note))
    pid = cur.lastrowid
    fname = f"plant{num}-{u['file'].lower()}.jpg"
    if os.path.exists(os.path.join("uploads", fname)):
        db.execute(
            """INSERT INTO photos (plant_id, filename, uploaded_by, is_headline)
               VALUES (?,?,?,1)""", (pid, fname, "import"))
    print(f"created stub #{num} ({guess}) from {u['file']}")

# --- 2. append genus evidence to flagged plants' review notes ----------------
for a in report["attached"]:
    if not a["flagged"] or not a["genus_guess"]:
        continue
    p = db.execute("SELECT * FROM plants WHERE number = ?", (a["number"],)).fetchone()
    ev = f"Photo {a['file']}: looks like {a['genus_guess']} (visual guess)."
    if ev not in (p["review_note"] or ""):
        db.execute("UPDATE plants SET review_note = ? WHERE id = ?",
                   ((p["review_note"] or "") + " " + ev, p["id"]))

# --- 3. flag meaningful genus mismatches --------------------------------------
for m in report["genus_mismatch"]:
    if m["confidence"] != "high" or synonymous(m["guess"], m["db_genus"]):
        continue
    p = db.execute("SELECT * FROM plants WHERE id = ?", (m["plant_id"],)).fetchone()
    note = ("Photo evidence: looks like " + m["guess"] +
            " but the log genus is " + m["db_genus"] +
            f" ({m['file']}, high-confidence visual read)")
    existing = p["review_note"] or ""
    if note not in existing:
        db.execute("UPDATE plants SET needs_review = 1, review_note = ? WHERE id = ?",
                   ((existing + " " + note).strip(), p["id"]))
    print(f"flagged #{m['number']}: {m['guess']} vs {m['db_genus']}")

db.commit()
n = db.execute("SELECT COUNT(*) FROM plants WHERE needs_review = 1").fetchone()[0]
print(f"\n{n} plants flagged; "
      f"{db.execute('SELECT COUNT(*) FROM photos').fetchone()[0]} photos total")
db.close()
