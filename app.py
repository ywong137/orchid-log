import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import kimi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "orchid.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}


def load_dotenv():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("ORCHID_SECRET", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  display_name TEXT
);
CREATE TABLE IF NOT EXISTS genera (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  color TEXT NOT NULL DEFAULT '#888888',
  sun TEXT, water TEXT, fertilize TEXT, medium TEXT,
  trimming TEXT, fall_trim TEXT, fall_water TEXT, fall_fertilize TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS plants (
  id INTEGER PRIMARY KEY,
  number INTEGER UNIQUE,
  genus_id INTEGER REFERENCES genera(id),
  name TEXT,
  location TEXT,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  sun TEXT, water TEXT, fertilize TEXT, medium TEXT,
  trimming TEXT, fall_trim TEXT, fall_water TEXT, fall_fertilize TEXT,
  needs_review INTEGER DEFAULT 0,
  review_note TEXT,
  deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  plant_id INTEGER NOT NULL REFERENCES plants(id),
  date TEXT NOT NULL,
  watered INTEGER DEFAULT 0,
  fertilized INTEGER DEFAULT 0,
  condition TEXT,
  trimming TEXT,
  events TEXT DEFAULT '',
  user TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  plant_id INTEGER NOT NULL REFERENCES plants(id),
  entry_id INTEGER REFERENCES entries(id),
  filename TEXT NOT NULL,
  caption TEXT,
  taken_at TEXT,
  uploaded_by TEXT,
  is_headline INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS staged_photos (
  id INTEGER PRIMARY KEY,
  filename TEXT NOT NULL,
  uploaded_by TEXT,
  analysis TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS review_images (
  plant_id INTEGER NOT NULL,
  image TEXT NOT NULL,
  UNIQUE(plant_id, image)
);
CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY,
  ts TEXT DEFAULT (datetime('now')),
  user TEXT,
  ip TEXT,
  method TEXT,
  path TEXT,
  endpoint TEXT,
  detail TEXT,
  status INTEGER
);
"""

EVENT_TAGS = ["blooming", "misted", "new growth", "repotted", "moved",
              "treated", "spike", "buds"]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    cols = [r[1] for r in db.execute("PRAGMA table_info(photos)")]
    if "is_headline" not in cols:
        db.execute("ALTER TABLE photos ADD COLUMN is_headline INTEGER DEFAULT 0")
    pcols = [r[1] for r in db.execute("PRAGMA table_info(plants)")]
    if "needs_review" not in pcols:
        db.execute("ALTER TABLE plants ADD COLUMN needs_review INTEGER DEFAULT 0")
    if "review_note" not in pcols:
        db.execute("ALTER TABLE plants ADD COLUMN review_note TEXT")
    if "deleted" not in pcols:
        db.execute("ALTER TABLE plants ADD COLUMN deleted INTEGER DEFAULT 0")
    if "deleted" not in cols:
        db.execute("ALTER TABLE photos ADD COLUMN deleted INTEGER DEFAULT 0")
    ecols = [r[1] for r in db.execute("PRAGMA table_info(entries)")]
    if "deleted" not in ecols:
        db.execute("ALTER TABLE entries ADD COLUMN deleted INTEGER DEFAULT 0")
    db.commit()
    db.close()


@app.context_processor
def inject_review_count():
    if "user" not in session:
        return {}
    n = get_db().execute(
        "SELECT COUNT(*) c FROM plants WHERE needs_review = 1 AND deleted = 0").fetchone()["c"]
    return {"review_count": n}


SENSITIVE_FIELDS = {"password", "current", "new", "confirm"}


@app.after_request
def log_activity(resp):
    """Audit trail: every mutating action + auth events, kept out of the UI."""
    try:
        interesting = request.method in ("POST", "PUT", "DELETE") or \
            request.path == "/logout"
        if not interesting or request.endpoint == "uploaded_file":
            return resp
        detail = {}
        if request.form:
            form = {}
            for k, vals in request.form.to_dict(flat=False).items():
                form[k] = "***" if k in SENSITIVE_FIELDS else \
                    (vals[0] if len(vals) == 1 else vals)
            detail["form"] = form
        if request.files:
            detail["files"] = {
                k: [f.filename for f in fs if f.filename]
                for k, fs in request.files.to_dict(flat=False).items()}
        if request.args:
            detail["args"] = dict(request.args)
        ldb = sqlite3.connect(DB_PATH)
        ldb.execute(
            """INSERT INTO activity_log (user, ip, method, path, endpoint, detail, status)
               VALUES (?,?,?,?,?,?,?)""",
            (session.get("user", "-"), request.remote_addr, request.method,
             request.path, request.endpoint, json.dumps(detail),
             resp.status_code))
        ldb.commit()
        ldb.close()
    except Exception:
        pass
    return resp


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def current_season():
    # Spring/summer growing season: April-September; fall/winter: Oct-March
    return "spring" if 4 <= date.today().month <= 9 else "fall"


def care_profile(plant):
    """Merge genus care defaults with per-plant overrides."""
    db = get_db()
    genus = db.execute("SELECT * FROM genera WHERE id = ?",
                       (plant["genus_id"],)).fetchone()
    fields = ["sun", "water", "fertilize", "medium", "trimming",
              "fall_trim", "fall_water", "fall_fertilize", "notes"]
    prof = {}
    for f in fields:
        prof[f] = plant[f] or (genus[f] if genus else None)
    return prof, genus


# ---------- auth ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE username = ?",
                       (request.form["username"].strip().lower(),)).fetchone()
        if u and check_password_hash(u["password"], request.form["password"]):
            session["user"] = u["username"]
            session["display_name"] = u["display_name"] or u["username"]
            return redirect(request.args.get("next") or url_for("index"))
        flash("Invalid username or password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- collection ----------

ACTIVITY_SQL = """
  MAX(substr(p.created_at, 1, 10),
      COALESCE((SELECT MAX(e.date) FROM entries e
                WHERE e.plant_id = p.id AND e.deleted = 0), ''),
      COALESCE((SELECT MAX(substr(ph.created_at, 1, 10)) FROM photos ph
                WHERE ph.plant_id = p.id AND ph.deleted = 0), ''))
"""


@app.route("/")
@login_required
def index():
    db = get_db()
    plants = db.execute(f"""
      SELECT p.*, g.name AS genus_name, g.color AS genus_color,
        (SELECT MAX(date) FROM entries WHERE plant_id = p.id AND watered AND deleted = 0) AS last_watered,
        (SELECT MAX(date) FROM entries WHERE plant_id = p.id AND fertilized AND deleted = 0) AS last_fertilized,
        (SELECT COUNT(*) FROM entries WHERE plant_id = p.id AND events LIKE '%blooming%'
          AND deleted = 0 AND date >= date('now', '-60 day')) AS recent_blooms,
        (SELECT filename FROM photos WHERE plant_id = p.id AND deleted = 0
          ORDER BY is_headline DESC, created_at DESC, id DESC LIMIT 1) AS thumb,
        {ACTIVITY_SQL} AS activity
      FROM plants p LEFT JOIN genera g ON g.id = p.genus_id
      WHERE p.deleted = 0
      ORDER BY activity DESC, p.number IS NULL, p.number
    """).fetchall()
    return render_template("index.html", plants=plants)


@app.route("/quick/<int:plant_id>/<action>", methods=["POST"])
@login_required
def quick_log(plant_id, action):
    if action not in ("watered", "fertilized"):
        abort(404)
    db = get_db()
    today = date.today().isoformat()
    row = db.execute("SELECT id FROM entries WHERE plant_id = ? AND date = ? AND deleted = 0",
                     (plant_id, today)).fetchone()
    if row:
        db.execute(f"UPDATE entries SET {action} = 1 WHERE id = ?", (row["id"],))
    else:
        db.execute(
            f"INSERT INTO entries (plant_id, date, {action}, user) VALUES (?,?,1,?)",
            (plant_id, today, session["user"]))
    db.commit()
    return redirect(request.referrer or url_for("index"))


# ---------- find ----------
# (Find page removed — the collection search bar does live filtering.)


@app.route("/api/plants")
@login_required
def api_plants():
    db = get_db()
    q = request.args.get("q", "").strip()
    sql = """
      SELECT p.id, p.number, p.name, p.location, g.name AS genus_name,
             g.color AS genus_color,
        (SELECT MAX(date) FROM entries WHERE plant_id = p.id AND watered AND deleted = 0) AS last_watered
      FROM plants p LEFT JOIN genera g ON g.id = p.genus_id
      WHERE p.deleted = 0
    """
    params = []
    if q:
        if q.isdigit():
            sql += " AND CAST(p.number AS TEXT) LIKE ?"
            params.append(q + "%")
        else:
            sql += " AND (p.name LIKE ? OR g.name LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY p.number IS NULL, p.number LIMIT 40"
    rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------- plants ----------

@app.route("/plant/new", methods=["GET", "POST"])
@login_required
def plant_new():
    db = get_db()
    if request.method == "POST":
        number = request.form.get("number", "").strip() or None
        genus_id = request.form.get("genus_id") or None
        new_genus = request.form.get("new_genus", "").strip()
        if genus_id == "__new__":
            genus_id = None
        if not genus_id and new_genus:
            db.execute("INSERT OR IGNORE INTO genera (name) VALUES (?)", (new_genus,))
            genus_id = db.execute("SELECT id FROM genera WHERE name LIKE ?",
                                  (new_genus,)).fetchone()["id"]
        try:
            cur = db.execute(
                """INSERT INTO plants (number, genus_id, name, location, notes)
                   VALUES (?,?,?,?,?)""",
                (number, genus_id, request.form["name"].strip(),
                 request.form.get("location", "").strip(),
                 request.form.get("notes", "").strip()))
            plant_id = cur.lastrowid
        except sqlite3.IntegrityError:
            flash(f"Tag number {number} is already in use")
            genera = db.execute("SELECT * FROM genera ORDER BY name").fetchall()
            return render_template("plant_form.html", plant=None, genera=genera,
                                   prefill=request.form,
                                   staged_id=request.form.get("staged_id", type=int))
        n_photos = 0
        for f in request.files.getlist("photos"):
            if not f.filename:
                continue
            fname = save_photo(f)
            if fname:
                db.execute(
                    """INSERT INTO photos (plant_id, filename, uploaded_by, is_headline)
                       VALUES (?,?,?,?)""",
                    (plant_id, fname, session["user"], 1 if n_photos == 0 else 0))
                n_photos += 1
        sid = request.form.get("staged_id", type=int)
        if sid:
            st = db.execute("SELECT * FROM staged_photos WHERE id = ?",
                            (sid,)).fetchone()
            if st:
                db.execute(
                    """INSERT INTO photos (plant_id, filename, uploaded_by, is_headline)
                       VALUES (?,?,?,?)""",
                    (plant_id, st["filename"], session["user"],
                     1 if n_photos == 0 else 0))
                db.execute("DELETE FROM staged_photos WHERE id = ?", (sid,))
                n_photos += 1
        db.commit()
        flash(f"Added “{request.form['name'].strip()}”" +
              (f" with {n_photos} photo(s)" if n_photos else
               " — you can add photos anytime from the Photos tab"))
        return redirect(url_for("plant_detail", plant_id=plant_id))
    prefill = {
        "number": request.args.get("number", ""),
        "name": request.args.get("name", ""),
        "genus_id": request.args.get("genus_id", ""),
        "new_genus": request.args.get("new_genus", ""),
    }
    genera = db.execute("SELECT * FROM genera ORDER BY name").fetchall()
    return render_template("plant_form.html", plant=None, genera=genera,
                           prefill=prefill,
                           staged_id=request.args.get("staged", type=int))


@app.route("/plant/<int:plant_id>")
@login_required
def plant_detail(plant_id):
    db = get_db()
    plant = db.execute(
        """SELECT p.*, g.name AS genus_name, g.color AS genus_color
           FROM plants p LEFT JOIN genera g ON g.id = p.genus_id
           WHERE p.id = ? AND p.deleted = 0""", (plant_id,)).fetchone()
    if not plant:
        abort(404)
    prof, genus = care_profile(plant)
    entries = db.execute(
        "SELECT * FROM entries WHERE plant_id = ? AND deleted = 0 ORDER BY date DESC, id DESC",
        (plant_id,)).fetchall()
    photos = db.execute(
        """SELECT * FROM photos WHERE plant_id = ? AND deleted = 0
           ORDER BY created_at DESC, id DESC""", (plant_id,)).fetchall()
    hero = next((ph for ph in photos if ph["is_headline"]),
                photos[0] if photos else None)
    entry_photos = {}
    for ph in db.execute(
            """SELECT ph.* FROM photos ph JOIN entries e ON e.id = ph.entry_id
               WHERE ph.plant_id = ? AND ph.deleted = 0""", (plant_id,)).fetchall():
        entry_photos.setdefault(ph["entry_id"], []).append(ph)
    review_imgs = []
    if plant["needs_review"]:
        review_imgs = [r["image"] for r in db.execute(
            "SELECT image FROM review_images WHERE plant_id = ? ORDER BY image",
            (plant_id,)).fetchall()]
    return render_template("plant.html", plant=plant, prof=prof, genus=genus,
                           entries=entries, photos=photos, hero=hero,
                           entry_photos=entry_photos, review_imgs=review_imgs,
                           season=current_season(), event_tags=EVENT_TAGS,
                           today=date.today().isoformat())


@app.route("/plant/<int:plant_id>/edit", methods=["GET", "POST"])
@login_required
def plant_edit(plant_id):
    db = get_db()
    plant = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not plant:
        abort(404)
    if request.method == "POST":
        number = request.form.get("number", "").strip() or None
        override_fields = ["sun", "water", "fertilize", "medium", "trimming",
                           "fall_trim", "fall_water", "fall_fertilize"]
        overrides = {f: (request.form.get(f, "").strip() or None)
                     for f in override_fields}
        try:
            db.execute(
                """UPDATE plants SET number=?, genus_id=?, name=?, location=?, notes=?,
                   sun=?, water=?, fertilize=?, medium=?, trimming=?,
                   fall_trim=?, fall_water=?, fall_fertilize=?
                   WHERE id=?""",
                (number, request.form.get("genus_id") or None,
                 request.form["name"].strip(), request.form.get("location", "").strip(),
                 request.form.get("notes", "").strip(),
                 *[overrides[f] for f in override_fields], plant_id))
            db.commit()
            return redirect(url_for("plant_detail", plant_id=plant_id))
        except sqlite3.IntegrityError:
            flash(f"Tag number {number} is already in use")
    genera = db.execute("SELECT * FROM genera ORDER BY name").fetchall()
    return render_template("plant_form.html", plant=plant, genera=genera)


@app.route("/plant/<int:plant_id>/entry", methods=["POST"])
@login_required
def entry_add(plant_id):
    db = get_db()
    events = ",".join(request.form.getlist("events"))
    db.execute(
        """INSERT INTO entries
           (plant_id, date, watered, fertilized, condition, trimming, events, user)
           VALUES (?,?,?,?,?,?,?,?)""",
        (plant_id, request.form["date"],
         1 if request.form.get("watered") else 0,
         1 if request.form.get("fertilized") else 0,
         request.form.get("condition", "").strip(),
         request.form.get("trimming", "").strip(),
         events, session["user"]))
    db.commit()
    return redirect(url_for("plant_detail", plant_id=plant_id) + "#log")


@app.route("/entry/<int:entry_id>/delete", methods=["POST"])
@login_required
def entry_delete(entry_id):
    db = get_db()
    e = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not e:
        abort(404)
    db.execute("UPDATE entries SET deleted = 1 WHERE id = ?", (entry_id,))
    db.commit()
    return redirect(url_for("plant_detail", plant_id=e["plant_id"]) + "#log")


# ---------- photos ----------

def save_photo(file):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    fname = f"{ts}{ext}"
    file.save(os.path.join(UPLOAD_DIR, fname))
    return fname


def _analyze_staged(db, st):
    """Run K3 analysis for one staged photo and store the result."""
    plants = db.execute(
        """SELECT p.number, p.name, g.name AS genus_name FROM plants p
           LEFT JOIN genera g ON g.id = p.genus_id""").fetchall()
    genera = db.execute("SELECT * FROM genera").fetchall()
    try:
        a = kimi.analyze_photo(os.path.join(UPLOAD_DIR, st["filename"]),
                               plants, genera)
    except kimi.KimiError as e:
        a = None
        result = {"error": str(e)}
    if a is not None:
        result = {"error": None, "raw": a}
        if "_parse_error" in a:
            result["error"] = "K3 returned unparseable output"
        tag = a.get("tag_number") or a.get("matched_number")
        matched = None
        if isinstance(tag, int):
            matched = db.execute(
                "SELECT id, number, name FROM plants WHERE number = ?",
                (tag,)).fetchone()
        result["matched_plant"] = dict(matched) if matched else None
        guess_genus_id = None
        if a.get("guess_genus"):
            grow = db.execute("SELECT id FROM genera WHERE name LIKE ?",
                              (a["guess_genus"],)).fetchone()
            guess_genus_id = grow["id"] if grow else None
        result["guess_genus_id"] = guess_genus_id
    db.execute("UPDATE staged_photos SET analysis = ? WHERE id = ?",
               (json.dumps(result), st["id"]))
    db.commit()


def _analysis_worker():
    """Background thread: analyze staged photos serially, then exit."""
    while True:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        st = db.execute(
            """SELECT * FROM staged_photos WHERE analysis IS NULL
               ORDER BY id LIMIT 1""").fetchone()
        if not st:
            db.close()
            return
        try:
            _analyze_staged(db, st)
        except Exception as e:
            db.execute("UPDATE staged_photos SET analysis = ? WHERE id = ?",
                       (json.dumps({"error": f"analysis failed: {e}"}), st["id"]))
            db.commit()
        db.close()


_worker = None


def ensure_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = threading.Thread(target=_analysis_worker, daemon=True)
    _worker.start()


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    db = get_db()
    if request.method == "POST":
        files = [f for f in request.files.getlist("photos") if f.filename]
        saved = 0
        for f in files:
            fname = save_photo(f)
            if fname:
                db.execute(
                    "INSERT INTO staged_photos (filename, uploaded_by) VALUES (?,?)",
                    (fname, session["user"]))
                saved += 1
        db.commit()
        if not saved:
            flash("No usable photos (allowed: " + ", ".join(sorted(ALLOWED_EXT)) + ")")
            return redirect(url_for("upload"))
        ensure_worker()
        flash(f"Uploaded {saved} photo(s) — K3 is analyzing and tagging them now. "
              "They'll appear below as they're processed; you can leave and come back.")
        return redirect(url_for("upload") + "#pending")
    staged = db.execute(
        "SELECT * FROM staged_photos WHERE uploaded_by = ? ORDER BY id",
        (session["user"],)).fetchall()
    cards = []
    for st in staged:
        a = json.loads(st["analysis"]) if st["analysis"] else None
        prefill = None
        if a and not a.get("error") and not a.get("matched_plant"):
            raw = a.get("raw") or {}
            params = {"staged": st["id"]}
            if isinstance(raw.get("tag_number"), int):
                params["number"] = raw["tag_number"]
            if raw.get("guess_name"):
                params["name"] = raw["guess_name"]
            if a.get("guess_genus_id"):
                params["genus_id"] = a["guess_genus_id"]
            elif raw.get("guess_genus"):
                params["new_genus"] = raw["guess_genus"]
            prefill = url_for("plant_new", **params)
        cards.append({"st": st, "a": a, "prefill": prefill})
    plants = db.execute(
        """SELECT p.*, g.name AS genus_name FROM plants p
           LEFT JOIN genera g ON g.id = p.genus_id
           WHERE p.deleted = 0
           ORDER BY p.number IS NULL, p.number""").fetchall()
    return render_template("upload.html", cards=cards, plants=plants)


@app.route("/api/staged")
@login_required
def staged_list():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM staged_photos WHERE uploaded_by = ? ORDER BY id",
        (session["user"],)).fetchall()
    return jsonify([{"id": st["id"], "filename": st["filename"],
                     "pending": st["analysis"] is None} for st in rows])


@app.route("/upload/commit/<int:sid>", methods=["POST"])
@login_required
def upload_commit(sid):
    db = get_db()
    st = db.execute("SELECT * FROM staged_photos WHERE id = ?", (sid,)).fetchone()
    if not st:
        abort(404)
    ajax = request.form.get("ajax") == "1"

    def fail(msg):
        if ajax:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg)
        return redirect(url_for("upload"))

    plant_id = request.form.get("plant_id", type=int)
    if not plant_id or not db.execute(
            "SELECT id FROM plants WHERE id = ? AND deleted = 0", (plant_id,)).fetchone():
        return fail("Pick which plant this photo belongs to")
    headline = 0 if db.execute(
        "SELECT id FROM photos WHERE plant_id = ? AND is_headline = 1 AND deleted = 0",
        (plant_id,)).fetchone() else 1
    db.execute(
        """INSERT INTO photos (plant_id, filename, caption, taken_at, uploaded_by, is_headline)
           VALUES (?,?,?,?,?,?)""",
        (plant_id, st["filename"], request.form.get("caption", "").strip(),
         request.form.get("taken_at") or None, session["user"], headline))
    db.execute("DELETE FROM staged_photos WHERE id = ?", (sid,))
    db.commit()
    if ajax:
        return jsonify({"ok": True,
                        "url": url_for("plant_detail", plant_id=plant_id) + "#photos"})
    return redirect(url_for("plant_detail", plant_id=plant_id) + "#photos")


@app.route("/upload/discard/<int:sid>", methods=["POST"])
@login_required
def upload_discard(sid):
    db = get_db()
    st = db.execute("SELECT * FROM staged_photos WHERE id = ?", (sid,)).fetchone()
    if not st:
        abort(404)
    try:
        os.remove(os.path.join(UPLOAD_DIR, st["filename"]))
    except OSError:
        pass
    db.execute("DELETE FROM staged_photos WHERE id = ?", (sid,))
    db.commit()
    if request.form.get("ajax") == "1":
        return jsonify({"ok": True})
    return redirect(url_for("upload"))


@app.route("/photo/<int:photo_id>/move", methods=["GET", "POST"])
@login_required
def photo_move(photo_id):
    db = get_db()
    ph = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not ph:
        abort(404)
    if request.method == "POST":
        target = request.form.get("plant_id", type=int)
        if target and target != ph["plant_id"] and db.execute(
                "SELECT id FROM plants WHERE id = ? AND deleted = 0", (target,)).fetchone():
            db.execute(
                "UPDATE photos SET plant_id = ?, is_headline = 0 WHERE id = ?",
                (target, photo_id))
            db.commit()
            flash("Photo moved")
            return redirect(url_for("plant_detail", plant_id=target) + "#photos")
        return redirect(url_for("plant_detail", plant_id=ph["plant_id"]) + "#photos")
    plant = db.execute(
        """SELECT p.*, g.name AS genus_name FROM plants p
           LEFT JOIN genera g ON g.id = p.genus_id
           WHERE p.id = ?""", (ph["plant_id"],)).fetchone()
    return render_template("move.html", ph=ph, plant=plant)


@app.route("/photo/<int:photo_id>/headline", methods=["POST"])
@login_required
def photo_headline(photo_id):
    db = get_db()
    ph = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not ph:
        abort(404)
    db.execute("UPDATE photos SET is_headline = 0 WHERE plant_id = ?",
               (ph["plant_id"],))
    db.execute("UPDATE photos SET is_headline = 1 WHERE id = ?", (photo_id,))
    db.commit()
    return redirect(url_for("plant_detail", plant_id=ph["plant_id"]))


@app.route("/photo/<int:photo_id>/delete", methods=["POST"])
@login_required
def photo_delete(photo_id):
    db = get_db()
    ph = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not ph:
        abort(404)
    db.execute("UPDATE photos SET deleted = 1, is_headline = 0 WHERE id = ?",
               (photo_id,))
    db.commit()
    return redirect(url_for("plant_detail", plant_id=ph["plant_id"]) + "#photos")


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ---------- review ----------

@app.route("/review")
@login_required
def review():
    db = get_db()
    plants = db.execute("""
      SELECT p.*, g.name AS genus_name, g.color AS genus_color,
        (SELECT COUNT(*) FROM entries WHERE plant_id = p.id AND deleted = 0) AS entry_count,
        (SELECT MAX(date) FROM entries WHERE plant_id = p.id AND deleted = 0) AS last_entry
      FROM plants p LEFT JOIN genera g ON g.id = p.genus_id
      WHERE p.needs_review = 1 AND p.deleted = 0
      ORDER BY p.number IS NULL, p.number, p.name""").fetchall()
    imgs = {}
    for r in db.execute("SELECT * FROM review_images").fetchall():
        imgs.setdefault(r["plant_id"], []).append(r["image"])
    return render_template("review.html", plants=plants, imgs=imgs)


@app.route("/plant/<int:plant_id>/review/clear", methods=["POST"])
@login_required
def review_clear(plant_id):
    db = get_db()
    db.execute("UPDATE plants SET needs_review = 0 WHERE id = ?", (plant_id,))
    db.commit()
    return redirect(request.referrer or url_for("review"))


def _review_img_path(name, small):
    if not re.fullmatch(r"IMG_\d{4}", name):
        return None
    candidates = []
    if small:
        candidates += [
            os.path.join(BASE_DIR, "import-log", "_work", name.lower() + ".jpg"),
            os.path.join(BASE_DIR, "import-pics", "_work", name.lower() + ".jpg")]
    candidates += [
        os.path.join(BASE_DIR, "import-log", name + ".JPG"),
        os.path.join(BASE_DIR, "import-pics", name + ".JPG"),
        os.path.join(BASE_DIR, "import-pics", name + ".jpg"),
        os.path.join(BASE_DIR, "import-log", "_work", name.lower() + ".jpg"),
        os.path.join(BASE_DIR, "import-pics", "_work", name.lower() + ".jpg")]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


@app.route("/review-img/<name>")
@login_required
def review_img(name):
    path = _review_img_path(name, small=True)
    if not path:
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path))


@app.route("/review-img/<name>/full")
@login_required
def review_img_full(name):
    path = _review_img_path(name, small=False)
    if not path:
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path))


# ---------- settings & users ----------

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            name = request.form.get("display_name", "").strip()
            db.execute("UPDATE users SET display_name = ? WHERE username = ?",
                       (name or session["user"], session["user"]))
            db.commit()
            session["display_name"] = name or session["user"]
            flash("Profile updated")
        elif action == "password":
            u = db.execute("SELECT * FROM users WHERE username = ?",
                           (session["user"],)).fetchone()
            if not check_password_hash(u["password"],
                                       request.form.get("current", "")):
                flash("Current password is wrong")
            elif request.form["new"] != request.form.get("confirm", ""):
                flash("New passwords don't match")
            else:
                db.execute("UPDATE users SET password = ? WHERE username = ?",
                           (generate_password_hash(request.form["new"]),
                            session["user"]))
                db.commit()
                flash("Password changed")
        return redirect(url_for("settings"))
    return render_template("settings.html")


@app.route("/users", methods=["GET", "POST"])
@login_required
def manage_users():
    db = get_db()
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]+", username):
            flash("Username: letters, numbers, . _ - only")
        elif not request.form["password"]:
            flash("Password required")
        else:
            try:
                db.execute(
                    "INSERT INTO users (username, password, display_name) VALUES (?,?,?)",
                    (username, generate_password_hash(request.form["password"]),
                     request.form.get("display_name", "").strip() or username))
                db.commit()
                flash(f"Added user {username}")
            except sqlite3.IntegrityError:
                flash(f"User {username} already exists")
    users = db.execute("SELECT id, username, display_name FROM users").fetchall()
    return render_template("users.html", users=users)


if __name__ == "__main__":
    init_db()
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        ensure_worker()
    app.run(host="0.0.0.0", port=5055, debug=True, threaded=True)
