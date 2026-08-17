"""Apply the hand-verified conflict resolutions to orchid.db.

Each action was verified by reading the page photos. Circled top-right
number is authoritative (per user). Moves are entry-level, by page date
lists, so back-side pages can split from their front page's plant.
"""
import json
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orchid.db")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "import-log", "_analysis")

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys = ON")

log = []


def page_dates(img):
    p = json.load(open(os.path.join(OUT, f"IMG_{img}.json")))
    return [e["date"] for e in p.get("entries", []) if e.get("date")]


def plant_by_number(n):
    return db.execute("SELECT * FROM plants WHERE number = ?", (n,)).fetchone()


def plant_by_name(like):
    return db.execute("SELECT * FROM plants WHERE name LIKE ?",
                      (like,)).fetchone()


def create(number, name, genus=None):
    gid = None
    if genus:
        row = db.execute("SELECT id FROM genera WHERE name LIKE ?",
                         (genus,)).fetchone()
        gid = row["id"] if row else None
    cur = db.execute("INSERT INTO plants (number, genus_id, name) VALUES (?,?,?)",
                     (number, gid, name))
    log.append(f"created #{number} '{name}'")
    return cur.lastrowid


def get_or_create(number, name, genus=None):
    p = plant_by_number(number)
    return p["id"] if p else create(number, name, genus)


def holder_of(date, name_like=None):
    """Find which plant currently holds the entry with this date."""
    rows = db.execute(
        """SELECT DISTINCT p.* FROM plants p JOIN entries e ON e.plant_id = p.id
           WHERE e.date = ?""", (date,)).fetchall()
    if name_like:
        named = [r for r in rows if name_like.lower() in (r["name"] or "").lower()]
        if named:
            return named[0]
    return rows[0] if rows else None


def move(dates, target_id, source_hint=None, source_name=None):
    """Move entries with these dates to the target plant.

    source_hint (set of plant numbers) and source_name (substring) are
    AND-ed when both are given; either alone works by itself. With no
    filters, only move when exactly one plant holds that date.
    """
    moved = duped = 0
    for d in dates:
        rows = db.execute(
            "SELECT id, plant_id FROM entries WHERE date = ?", (d,)).fetchall()
        for e in rows:
            src = db.execute("SELECT * FROM plants WHERE id = ?",
                             (e["plant_id"],)).fetchone()
            if not src or src["id"] == target_id:
                continue
            if source_hint and src["number"] not in source_hint:
                continue
            if source_name and source_name.lower() not in (src["name"] or "").lower():
                continue
            if not source_hint and not source_name and len(rows) > 1:
                continue  # ambiguous unhinted date
            dupe = db.execute(
                "SELECT id FROM entries WHERE plant_id = ? AND date = ?",
                (target_id, d)).fetchone()
            if dupe:
                db.execute("DELETE FROM entries WHERE id = ?", (e["id"],))
                duped += 1
            else:
                db.execute("UPDATE entries SET plant_id = ? WHERE id = ?",
                           (target_id, e["id"]))
                moved += 1
    if moved or duped:
        log.append(f"  moved {moved} entries (dupes removed {duped})")
    return moved, duped


def renumber(pid, new):
    taken = plant_by_number(new)
    assert not taken, f"#{new} taken by {taken['name']}"
    db.execute("UPDATE plants SET number = ? WHERE id = ?", (new, pid))
    log.append(f"renumbered plant {pid} -> #{new}")


def merge_plants(old_id, keep_id):
    for e in db.execute("SELECT * FROM entries WHERE plant_id = ?",
                        (old_id,)).fetchall():
        dupe = db.execute("SELECT id FROM entries WHERE plant_id = ? AND date = ?",
                          (keep_id, e["date"])).fetchone()
        if dupe:
            db.execute("DELETE FROM entries WHERE id = ?", (e["id"],))
        else:
            db.execute("UPDATE entries SET plant_id = ? WHERE id = ?",
                       (keep_id, e["id"]))
    db.execute("UPDATE photos SET plant_id = ? WHERE plant_id = ?",
               (keep_id, old_id))
    old = db.execute("SELECT * FROM plants WHERE id = ?", (old_id,)).fetchone()
    keep = db.execute("SELECT * FROM plants WHERE id = ?", (keep_id,)).fetchone()
    if old["notes"] and old["notes"].lower() not in (keep["notes"] or "").lower():
        db.execute("UPDATE plants SET notes = ? WHERE id = ?",
                   (((keep["notes"] or "") + " " + old["notes"]).strip(), keep_id))
    db.execute("DELETE FROM plants WHERE id = ?", (old_id,))
    log.append(f"merged '{old['name']}' (#{old['number'] or '–'}) into "
               f"'{keep['name']}' (#{keep['number'] or '–'})")


def husk_cleanup():
    for p in db.execute("SELECT * FROM plants").fetchall():
        n = db.execute("SELECT COUNT(*) c FROM entries WHERE plant_id = ?",
                       (p["id"],)).fetchone()["c"]
        np_ = db.execute("SELECT COUNT(*) c FROM photos WHERE plant_id = ?",
                         (p["id"],)).fetchone()["c"]
        if n == 0 and np_ == 0 and p["created_at"] and \
                p["created_at"] >= "2026-08-15":
            db.execute("DELETE FROM plants WHERE id = ?", (p["id"],))
            log.append(f"deleted empty husk '#{p['number'] or '–'} {p['name']}'")


def restore_empty_named_plants():
    """Real binder sheets that had no log entries (blank forms) — keep them."""
    if not plant_by_number(118):
        db.execute(
            "INSERT INTO plants (number, name, notes) VALUES (118, 'ANGRAECUM', "
            "'(blank log sheet IMG_5915)')")
        log.append("restored #118 ANGRAECUM (blank sheet)")
    if not plant_by_name("didieri"):
        db.execute(
            "INSERT INTO plants (number, name, notes) VALUES (12, 'didieri', "
            "'(blank log sheet IMG_5999)')")
        log.append("restored #12 didieri (blank sheet)")


D = lambda *imgs: [d for im in imgs for d in page_dates(im)]

# --- 1. Roble tangle first (frees #10): one plant 'Los Roble' #33 ------------
# 6036's sheet visually reads "LOS ROBLE" (K3 heard "Lo's Babe"); 6028 is
# circled 33 ("R lower shelf", "distilled 12/25" = VOS ROIBLE's sheet);
# "VOS ROIBLE" was a session-1 misread of "LOS ROBLE".
pid33 = get_or_create(33, "Los Roble", "Oncidium")
lob = plant_by_number(10)
if lob and "lo's babe" in (lob["name"] or "").lower():
    merge_plants(lob["id"], pid33)
for like in ("vos roible", "los roble"):
    row = plant_by_name(like)
    if row and row["id"] != pid33:
        merge_plants(row["id"], pid33)
move(D("6028"), pid33, source_name="sun king")
move(D("5934"), pid33, source_hint={62})

# --- 2. Chief Glory Red Ant is #10 (verified "#10" on 5896) ------------------
pid = get_or_create(10, "Chief Glory 'Red Ant'", "Rhynchostylis")
move(D("5896"), pid, source_hint={92}, source_name="red ant")
move(D("5996"), pid, source_name="chief glory")

# --- 3. Two Wildcat sheets: #1 and #2 ----------------------------------------
pid = get_or_create(1, 'Odontocidium Wildcat "Gold Red Star"', "Oncidium")
move(D("5916", "6025"), pid, source_hint={2}, source_name="wildcat")

# --- 4. Pink Lady is two plants: #79 and #81 ---------------------------------
pid = get_or_create(79, "Den. Pink Lady", "Dendrobium")
move(D("5960", "5961", "6055"), pid, source_hint={81}, source_name="pink lady")
pl79 = db.execute("SELECT * FROM plants WHERE id = ?", (pid,)).fetchone()
pl81 = plant_by_number(81)
if pl81 and pl81["notes"] and not pl79["notes"]:
    db.execute("UPDATE plants SET notes = ? WHERE id = ?", (pl81["notes"], pid))

# --- 5. Honey Bee #67 / AKA Baby (Sharry Baby) #68 ---------------------------
pid67 = get_or_create(67, "Honey Bee", "Oncidium")
move(D("5938"), pid67, source_name="honey bee")
move(D("5940"), pid67, source_name="sharry baby")
p68 = plant_by_number(68)
if p68:
    move(D("5939"), p68["id"], source_name="sharry baby")

# --- 6. Aunty Diana Aki #72 (5943+6038 verified), freeing #17 ----------------
pid72 = get_or_create(72, "Aunty Diana Aki", "Oncidium (Brassia)")
move(D("5944", "6038"), pid72, source_name="diana aki")
move(D("5943"), pid72, source_hint={17}, source_name="garden pears")
p7 = plant_by_number(7)
if p7:
    move(D("5942"), p7["id"], source_hint={17}, source_name="garden pears")
# plant #17 now holds nothing from the Garden Pears cluster; repurpose it
p17 = plant_by_number(17)
if p17:
    db.execute("UPDATE plants SET name = ? WHERE id = ?",
               ("Hybrid 'Amazing Bangkok'", p17["id"]))
    pid17 = p17["id"]
else:
    pid17 = create(17, "Hybrid 'Amazing Bangkok'", "Cattleya")
move(D("5976"), pid17, source_name="amazing")
move(D("6066"), pid17, source_name="rhync")
p56 = plant_by_number(56)
if p56:
    move(D("5974", "5975"), p56["id"], source_name="maudiae")

# --- 7. Golden Girl #42 / Green Lantern #8 (both visually verified) ----------
gold = plant_by_number(8)
if gold and "golden" in (gold["name"] or "").lower():
    renumber(gold["id"], 42)
gl = plant_by_number(53)
if gl and "green lantern" in (gl["name"] or "").lower():
    renumber(gl["id"], 8)

# --- 8. Grammatophyllum #50 (6004 verified) and #51 (6006) -------------------
pid50 = get_or_create(50, "Grammatophyllum scriptum v. citrinum",
                      "Grammatophyllum")
move(D("6004"), pid50, source_hint={30}, source_name="scriptum")
p51 = plant_by_number(51)
if p51:
    move(D("6006"), p51["id"], source_hint={30}, source_name="scriptum")

# --- 9. #13 belongs to Brassavola Nodosa (6073 verified "# 13") --------------
nod = plant_by_name("brassavola nodosa")
rmy = plant_by_number(13)
if nod and rmy and rmy["id"] != nod["id"]:
    db.execute("UPDATE plants SET number = NULL WHERE id = ?", (rmy["id"],))
    db.execute("UPDATE plants SET number = 13 WHERE id = ?", (nod["id"],))
    log.append(f"moved #13 from '{rmy['name']}' to '{nod['name']}'")

# --- 10. Phal band greens: 5884->#22, 5885->#23 ------------------------------
for n, img in ((22, "5884"), (23, "5885")):
    p = plant_by_number(n)
    if p:
        move(D(img), p["id"])

# --- 11. Sapphire's Galeh is Sapphire's Galah #44 ----------------------------
sag = plant_by_name("%galeh%")
sag2 = plant_by_number(44)
if sag and sag2 and sag["id"] != sag2["id"]:
    merge_plants(sag["id"], sag2["id"])

# --- 12. Small World 4N is #52 (verified "(52)") -----------------------------
p52 = plant_by_number(52)
if p52:
    move(D("5980"), p52["id"], source_name="small world")

# --- 13. Clean singles: entries to plants verified by circled numbers -------
singles = [
    ("5892", 101, None), ("5902", 84, None), ("5924", 19, None),
    ("5930", 58, None), ("5947", 102, None), ("5970", 55, None),
    ("5999", 12, "didieri"), ("6006", 51, None), ("6008", 18, None),
    ("6011", 83, None), ("6012", 83, None), ("6013", 84, None),
    ("6017", 91, None), ("6018", 94, None), ("6020", 100, None),
    ("6021", 90, None), ("6029", 4, None), ("6032", 57, None),
    ("6045", 115, None), ("6049", 15, None), ("6054", 63, None),
    ("6057", 82, None), ("6059", 103, None), ("6071", 106, None),
    ("6072", 107, None),
]
for img, num, src_name in singles:
    p = plant_by_number(num)
    if not p:
        continue
    move(D(img), p["id"], source_name=src_name)

husk_cleanup()
restore_empty_named_plants()
db.commit()

for line in log:
    print(line)
print("\nfinal counts:",
      db.execute("SELECT COUNT(*) c FROM plants").fetchone()["c"], "plants,",
      db.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"], "entries")
db.close()
