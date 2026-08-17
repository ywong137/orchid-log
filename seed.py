"""Seed genus care profiles, users, and sample plants transcribed from the paper log photos."""
import sqlite3
from werkzeug.security import generate_password_hash

DB = "orchid.db"

GENERA = [
    # name, color, sun, water, fertilize, medium, trimming, fall_trim, fall_water, fall_fertilize, notes
    ("Phalaenopsis", "#228B22", "Indirect / less light",
     "2x/week - keep moist NOT soggy", "Bi-weekly", "Bark + some moss",
     "Spring/summer: cut to node (browning: cut spike back to base)",
     "Cut spike back to base", "Bi-weekly", "1x/mo",
     "Log 1: Spring water 2x+/week, Fall 2x/month."),
    ("Oncidium", "#FF8C00", "Bright / filtered",
     "1x/week - thorough - dry 1/2 way pot", "Bi-weekly - balanced",
     "Bark/coarse + perlite",
     "Flower stems to base once spike turns brown",
     "Same as above", "Bi-weekly", "1x/month",
     "Crinkled leaves = need more water. Reddish green leaves = too much sun; dark green = too little. Incl. Brassia ('Barissa')."),
    ("Dendrobium", "#4169E1", "Indirect",
     "1x/week - generously during blooming", "Bi-weekly", "Bark/coarse",
     "Yellow/dead spikes cut above pseudobulb",
     "Same as above", "1x/mo", "STOP IN SEPT", None),
    ("Paphiopedilum", "#FF69B4", "Indirect / medium",
     "1x/week - keep evenly moist, never dry out fully", "Bi-weekly - weak dilution",
     "Bark/fine + perlite",
     "Cut flower stem to base after bloom",
     "Same as above", "Bi-weekly", "1x/mo", None),
    ("Cattleya", "#DC143C", "High / bright (light leaves)",
     "1x/10 days (dry out completely between)", "Bi-weekly", "Bark/coarse",
     "Cut back to base after bloom - trim yellow leaves",
     "Same as above", "1x/mo", "1x/mo", None),
    ("Brasslova", "#9ACD32", "High / bright (light leaves)",
     "1x/10 days (dry out completely between)", "Bi-weekly", "Bark/coarse",
     "Cut back to base after bloom - trim yellow leaves",
     "Same as above", "1x/mo", "1x/mo", None),
    ("Rhyncattleanthe", "#B22222", "Bright indirect",
     "1-2x a week, soak for 15 min", "Weekly, diluted",
     "Bark mix, charcoal, perlite",
     "Cut spike to base after flowering",
     "Same as above", "Weekly", "NONE", None),
    ("Epiphyllum", "#20B2AA", "Bright/filtered, don't scorch",
     "1x/week - let dry between", "Bi-weekly",
     "Airy epiphyte mix (bark, pumice - fast draining)",
     "Thin/weak segments, damaged bits, anything crowding center",
     "Just damaged/rotting/weak", "10-21 days, wait til dry",
     "6-8 weeks if still growing", None),
]

USERS = [("yishan", "orchids", "Yishan"), ("caretaker", "orchids", "Caretaker")]

# plant: (number, genus, name, location, notes, entries)
# entry: (date, watered, fertilized, condition, trimming, events)
PLANTS = [
    (44, "Phalaenopsis", "Sapphire's Galah", None,
     "Nursery tag P2074 4DSCH, purple bloom.", [
        ("2025-05-11", 1, 0, "Blooming!", "", "blooming"),
        ("2025-05-18", 1, 0, "Doing great - still blooming", "", "blooming"),
        ("2025-05-21", 1, 0, "Doing great - blooming", "", "blooming"),
        ("2025-05-26", 1, 0, "Leaves perky - blooming", "", "blooming"),
        ("2025-05-29", 1, 0, "Looking great - blooming", "", "blooming"),
        ("2025-06-01", 1, 0, "Doing so well - blooming", "", "blooming"),
        ("2025-06-04", 1, 0, "Looking great - blooming", "", "blooming"),
        ("2025-06-08", 1, 0, "Doing great - blooming", "", "blooming"),
        ("2025-06-15", 1, 0, "Blooming", "", "blooming"),
        ("2025-06-18", 1, 0, "Blooming", "", "blooming"),
        ("2025-06-22", 1, 0, "Blooming", "", "blooming"),
     ]),
    (99, "Phalaenopsis", "DTPS Yu Pin Burgundy", None, None, [
        ("2025-06-04", 1, 0, "Doing great, leaves perky - misted, blooming", "", "misted,blooming"),
        ("2025-06-08", 1, 0, "Doing great - misted, blooming!", "", "misted,blooming"),
        ("2025-06-15", 1, 0, "Doing great - blooming", "", "blooming"),
        ("2025-06-19", 1, 0, "Blooming!", "", "blooming"),
        ("2025-06-22", 1, 0, "Blooming", "", "blooming"),
        ("2025-06-29", 1, 0, "Blooming", "", "blooming"),
        ("2025-07-02", 1, 0, "Blooming", "", "blooming"),
        ("2025-07-06", 1, 0, "Blooming", "", "blooming"),
        ("2025-07-09", 1, 0, "Blooming", "", "blooming"),
        ("2025-07-13", 1, 0, "Blooming", "", "blooming"),
        ("2025-07-20", 1, 0, "Blooming", "", "blooming"),
     ]),
    (92, "Rhyncattleanthe", "Rth. Chief Glory 'Red Ant'", None, None, [
        ("2025-03-09", 1, 0, "Misted", "", "misted"),
        ("2025-03-12", 1, 0, "Misted", "", "misted"),
        ("2025-03-16", 1, 0, "Leaves, still blooming - misted, strong", "", "misted,blooming"),
        ("2025-03-19", 1, 0, "Doing well, blooming", "", "blooming"),
        ("2025-03-23", 1, 0, "Leaves look strong, healthy", "", ""),
        ("2025-04-02", 1, 0, "Leaves perky - bloom removed, roots look great", "", ""),
        ("2025-04-06", 1, 0, "Lots of new growth - doing great", "", "new growth"),
        ("2025-04-09", 1, 0, "Leaves perky - doing great after repot", "", "repotted"),
        ("2025-04-13", 1, 0, "Doing great - thriving", "", ""),
        ("2025-04-16", 1, 0, "Leaves perky - looking amazing", "", ""),
        ("2025-04-20", 1, 0, "Roots/leaves happy - looking good", "", ""),
     ]),
    (81, "Dendrobium", "Den. Pink Lady", None,
     "Put into self-watering glass pot. Moss added 2/26.", [
        ("2025-02-01", 1, 0, "", "", ""),
        ("2025-02-21", 1, 0, "Looks good... about to bloom?", "", ""),
        ("2025-02-26", 0, 0, "Leaves cleaned, happy + blooming :) moss added", "", "blooming"),
        ("2025-03-02", 1, 0, "Misted", "", "misted"),
        ("2025-03-05", 1, 0, "Cleaned + conditioned w/ coconut oil - misted leaves", "", "misted"),
        ("2025-03-09", 1, 0, "Misted", "", "misted"),
        ("2025-03-12", 1, 0, "New growth! - misted", "", "misted,new growth"),
        ("2025-03-16", 1, 0, "Roots look great, still blooming - misted, new growth", "", "misted,blooming,new growth"),
        ("2025-03-19", 1, 0, "New growth, roots growing - still blooming; + white = healthy", "", "blooming,new growth"),
        ("2025-03-23", 1, 0, "Flowers removed - doing well - same strong, healthy", "", ""),
     ]),
    (80, "Epiphyllum", "Makoyama Orange 'Florida Sweet'", None,
     "Repotted into self-watering pot 12/30.", [
        ("2024-12-30", 1, 0, "Repot, put in self-watering pot - distilled, a little - looks good, not yet blooming", "", "repotted"),
        ("2025-01-21", 1, 0, "Seems good, water was empty", "", ""),
        ("2025-02-01", 1, 0, "Fine, new little growths! - reservoir, a bit", "", "new growth"),
        ("2025-02-21", 1, 0, "Good, new growths up top (5) - drain thru to fill reservoir", "", "new growth"),
        ("2025-02-26", 1, 0, "Moss added, new growth - leaves cleaned", "", "new growth"),
        ("2025-03-02", 1, 0, "Misted", "", "misted"),
        ("2025-03-05", 1, 0, "Misted, leaves cleaned + conditioned w/ coconut oil", "", "misted"),
        ("2025-03-09", 1, 0, "Misted", "", "misted"),
        ("2025-03-12", 1, 0, "New growth - + misted", "", "misted,new growth"),
        ("2025-03-16", 1, 0, "Looks great - new shoot coming out of the top! - misted", "", "misted,new growth"),
     ]),
    (2, "Oncidium", "Odontocidium Wildcat 'Gold Red Star'", None, None, [
        ("2025-05-14", 1, 0, "New growth - looking great, coming back to life", "", "new growth"),
        ("2025-05-18", 1, 0, "Doing well - time for repot", "", ""),
        ("2025-05-21", 1, 0, "Looking good - new growth", "", "new growth"),
        ("2025-05-26", 1, 0, "Coming back", "", ""),
        ("2025-05-29", 1, 0, "Doing well - new growth", "", "new growth"),
        ("2025-06-01", 1, 0, "Looking good", "", ""),
        ("2025-06-04", 1, 0, "New growth - slowly coming back", "", "new growth"),
        ("2025-06-08", 1, 0, "Doing great - leaves perky", "", ""),
        ("2025-06-15", 1, 0, "Misted - doing well", "", "misted"),
        ("2025-06-19", 1, 0, "Doing well - new growth", "", "new growth"),
        ("2025-06-22", 1, 0, "Looking good - leaves perky", "", ""),
     ]),
    (8, "Brasslova", "Brassolaeliocattleya 'Golden Glory'", None, None, [
        ("2025-04-20", 1, 0, "Doing great - rotated", "", ""),
        ("2025-04-23", 1, 0, "Leaves perky - doing great", "", ""),
        ("2025-05-04", 1, 0, "New growth - doing well", "", "new growth"),
        ("2025-05-07", 1, 0, "Leaves perky - looking great", "", ""),
        ("2025-05-11", 1, 0, "New growth - looking great", "", "new growth"),
        ("2025-05-14", 1, 0, "Doing great", "", ""),
        ("2025-05-18", 1, 0, "Healthy, doing well - leaves + roots", "", ""),
        ("2025-05-21", 1, 0, "Doing great", "", ""),
        ("2025-05-26", 1, 0, "New growth - looking good", "", "new growth"),
        ("2025-05-29", 1, 0, "Doing well - leaves perky", "", ""),
        ("2025-06-01", 1, 0, "New growth - looking great", "", "new growth"),
     ]),
    (None, "Oncidium", "VOS ROIBLE", "R lower shelf",
     "Moved to self-watering pot + R lower shelf 12/25. Rope added.", [
        ("2024-10-17", 1, 0, "Doing well", "", ""),
        ("2024-11-12", 1, 0, "Flowers dying, spikes cut off", "", ""),
        ("2024-11-28", 1, 0, "Trimmed all dead leaves", "", "trimmed"),
        ("2024-12-25", 1, 0, "Distilled - add rope, moved to self-watering pot, moved to R lower shelf", "", "moved,repotted"),
        ("2025-01-20", 1, 0, "Keep trying!", "", ""),
     ]),
]


def main():
    db = sqlite3.connect(DB)
    db.execute("PRAGMA foreign_keys = ON")

    for name, color, sun, water, fert, med, trim, ftrim, fwater, ffert, notes in GENERA:
        db.execute(
            """INSERT OR IGNORE INTO genera
               (name, color, sun, water, fertilize, medium, trimming,
                fall_trim, fall_water, fall_fertilize, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (name, color, sun, water, fert, med, trim, ftrim, fwater, ffert, notes))

    for username, pw, display in USERS:
        db.execute(
            "INSERT OR IGNORE INTO users (username, password, display_name) VALUES (?,?,?)",
            (username, generate_password_hash(pw), display))

    for number, genus_name, name, location, notes, entries in PLANTS:
        genus = db.execute("SELECT id FROM genera WHERE name = ?", (genus_name,)).fetchone()
        if db.execute("SELECT id FROM plants WHERE name = ?", (name,)).fetchone():
            continue
        cur = db.execute(
            "INSERT INTO plants (number, genus_id, name, location, notes, created_at) VALUES (?,?,?,?,?,?)",
            (number, genus[0], name, location, notes,
             min(e[0] for e in entries) + " 00:00:00" if entries else None))
        pid = cur.lastrowid
        for d, w, f, cond, trim, ev in entries:
            db.execute(
                """INSERT INTO entries (plant_id, date, watered, fertilized, condition, trimming, events, user)
                   VALUES (?,?,?,?,?,?,?, 'seed')""",
                (pid, d, w, f, cond, trim, ev))

    db.commit()
    n = db.execute("SELECT COUNT(*) FROM plants").fetchone()[0]
    e = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    print(f"Seeded: {n} plants, {e} entries")
    db.close()


if __name__ == "__main__":
    main()
