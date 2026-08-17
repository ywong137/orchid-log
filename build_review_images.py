"""Map each review-flagged plant to the source page photos behind its
uncertainty (import-log scans + import-pics references in its note)."""
import json
import os
import re
import sqlite3

from import_logs import load_pages, cluster_pages, cluster_tag, name_sim

db = sqlite3.connect("orchid.db")
db.row_factory = sqlite3.Row
db.execute("""
CREATE TABLE IF NOT EXISTS review_images (
  plant_id INTEGER NOT NULL,
  image TEXT NOT NULL,
  UNIQUE(plant_id, image)
)""")

pages = load_pages()
clusters = cluster_pages(pages)

for p in db.execute(
        "SELECT id, number, name, review_note FROM plants WHERE needs_review = 1"):
    imgs = set(re.findall(r"IMG_\d{4}", p["review_note"] or ""))
    for c in clusters:
        ctag = cluster_tag(c)
        match = False
        if p["number"] and ctag == p["number"] and \
                name_sim(c["name"] or "", p["name"]) >= 0.5:
            match = True
        elif c["name"] and p["name"] and name_sim(c["name"], p["name"]) >= 0.75:
            match = True
        if match:
            for pg in c["pages"]:
                imgs.add(pg["file"].replace(".json", ""))
    for im in sorted(imgs):
        db.execute("INSERT OR IGNORE INTO review_images (plant_id, image) VALUES (?,?)",
                   (p["id"], im))
db.commit()
n = db.execute("SELECT COUNT(*) FROM review_images").fetchone()[0]
print(f"{n} plant/image links")
db.close()
