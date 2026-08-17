"""One-time: set needs_review flags for plants with uncertain identity/number."""
import sqlite3

db = sqlite3.connect("orchid.db")
db.row_factory = sqlite3.Row

FLAGS = {
    108: "Two 'Chief Glory Red Ant' sheets: #10 (verified circled) and #92 — same plant or two?",
    3: "Two 'Chief Glory Red Ant' sheets: #10 (verified circled) and #92 — same plant or two?",
    56: "Verified '# 45' on two sheets, but one header read #9 — one Yaya or two?",
    61: "Sheet IMG_6058 has BOTH #103 and #93 written (a correction) — which won?",
    33: "No circled number visible; header/tab read 4 — but 'Columbiana Garden Prince' also claimed 4",
    40: "Claimed #4, taken by 'Gold Shiny Yi-Ying' — check the binder",
    38: "No circled number found; header read 13, but #13 verified as Brassavola Nodosa",
    71: "No circled number found; header read 13 (taken by Brassavola Nodosa); book-2 sheet read 43",
    82: "No circled number found; header read 13 (taken by Brassavola Nodosa)",
    113: "2024 entries ('seems dead'); header read 17, but #17 verified as 'Amazing Bangkok'",
    68: "Circled 17 on two sheets; a 'Dark Green Prince' sheet also claims 17 (kept separate)",
    29: "Absorbed 'Sun King'/'Catante Sun King' sheets reading 24, 26, 108 — probably one plant; verify number",
    39: "IMG_6031 claims circled 44 (= #44 Sapphire's Galah) — suspicious, not applied",
    107: "Merged 'VOS ROIBLE', 'Lo's Babe' and '#70 Los Roble' here; circled 33 beats un-circled 70/10 — verify",
    7: "Renumbered 8 → 42 ('Golden Girl' sheet verified #42); confirm Glory == Girl",
    65: "Number verified as 55 (circled), but the plant name was unreadable (IMG_5970)",
    15: "Number verified as 101 (circled), but the plant name was unreadable",
    93: "Number verified as 57 (circled), but the plant name was unreadable",
    81: "Number unverified — header read 16, no circled number found",
    35: "Couldn't verify name; number 19 from tab read",
    36: "Couldn't verify name; number 26 from tab read",
    41: "Couldn't verify name; number 58 from tab read",
    74: "Couldn't verify this plant's number/name from its page",
    78: "Couldn't verify this plant's number/name from its page",
    83: "Grammatophyllum sheet (IMG_6003/6005) — number uncertain (#30 area)",
    85: "Couldn't verify this plant's number/name from its page",
    91: "Couldn't verify this plant's number/name from its page",
    92: "Couldn't verify this plant's number/name from its page",
    101: "Couldn't verify this plant's number/name from its page",
}

for pid, note in FLAGS.items():
    cur = db.execute("UPDATE plants SET needs_review = 1, review_note = ? WHERE id = ?",
                     (note, pid))
    if cur.rowcount == 0:
        print(f"WARNING: no plant id {pid}")

# #23 holds the Phalaenopsis Standard sheets (verified circled 23 twice)
db.execute("UPDATE plants SET name = 'Phalaenopsis Standard' WHERE id = 11")
# leftover truly-empty import husk (its entries went to #33 Los Roble)
db.execute("DELETE FROM plants WHERE id = 44 AND NOT EXISTS "
           "(SELECT 1 FROM entries WHERE plant_id = 44)")

db.commit()
n = db.execute("SELECT COUNT(*) FROM plants WHERE needs_review = 1").fetchone()[0]
print(f"{n} plants flagged")
db.close()
