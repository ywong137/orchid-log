"""Final fixup after verified findings (see apply_resolution.py for phase 1)."""
import sqlite3

db = sqlite3.connect("orchid.db")
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys = ON")
log = []


def entries_of(pid):
    return db.execute("SELECT * FROM entries WHERE plant_id = ? ORDER BY date",
                      (pid,)).fetchall()


def merge_into(src_id, dst_id, only_dates=None, delete_src=True):
    n = 0
    for e in entries_of(src_id):
        if only_dates and e["date"] not in only_dates:
            continue
        dupe = db.execute("SELECT id FROM entries WHERE plant_id = ? AND date = ?",
                          (dst_id, e["date"])).fetchone()
        if dupe:
            db.execute("DELETE FROM entries WHERE id = ?", (e["id"],))
        else:
            db.execute("UPDATE entries SET plant_id = ? WHERE id = ?",
                       (dst_id, e["id"]))
            n += 1
    if delete_src and not entries_of(src_id):
        src = db.execute("SELECT * FROM plants WHERE id = ?", (src_id,)).fetchone()
        db.execute("UPDATE photos SET plant_id = ? WHERE plant_id = ?",
                   (dst_id, src_id))
        db.execute("DELETE FROM plants WHERE id = ?", (src_id,))
        log.append(f"merged+deleted plant {src_id} ('{src['name']}') -> {dst_id}")
    return n


# 1. Split 'Dark Green Prince x Hild Citron' (3 entries, 2024) out of #17 ----
dgp_dates = [e["date"] for e in entries_of(68) if e["date"] < "2025-01-01"]
cur = db.execute(
    "INSERT INTO plants (number, name, notes) VALUES (NULL, "
    "'Dark Green Prince x Hild Citron', "
    "'sheet IMG_5973; header said #17 but 5976+6066 are circled 17 — needs review')")
merge_into(68, cur.lastrowid, only_dates=dgp_dates, delete_src=False)
log.append(f"split {len(dgp_dates)} 'Dark Green Prince' entries out of #17")

# 2. 5884 (id 10) is the same #22 'Phalaenopsis Standard' as 5985 (id 76) ----
merge_into(10, 76)
db.execute("UPDATE plants SET name = 'Phalaenopsis Standard' WHERE id = 76")
log.append("id10 -> #22 (Phalaenopsis Standard, book1+book2)")

# 3. 5986+5987 (id 77) join 5885 at #23 (id 11) -------------------------------
merge_into(77, 11)
log.append("id77 -> #23")

# 4. Grammatophyllum #50 (6004 verified) from id 84 ---------------------------
cur = db.execute(
    "INSERT INTO plants (number, name, notes) VALUES (50, "
    "'Grammatophyllum scriptum v. citrinum', '(verified # 50 on IMG_6004)')")
n = merge_into(84, cur.lastrowid)
log.append(f"#50 created with {n} entries from id 84")

# 5. Yaya: merge id 103 and renumber to verified #45 --------------------------
merge_into(103, 56)
db.execute("UPDATE plants SET number = 45 WHERE id = 56")
log.append("Yaya merged and renumbered #9 -> #45 (verified on 5955+6051)")

# 6. 5970's sheet (id 65) is circled 55 ----------------------------------------
db.execute("UPDATE plants SET number = 55 WHERE id = 65")
log.append("renumbered id 65 (5970's sheet) -> #55")

# 7. drop empty leftover -------------------------------------------------------
db.execute("DELETE FROM plants WHERE id = 89")

db.commit()
for line in log:
    print(line)
print("counts:",
      db.execute("SELECT COUNT(*) c FROM plants").fetchone()["c"], "plants,",
      db.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"], "entries")
db.close()
