from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
import os
import uuid
import json
import re
from datetime import datetime, timezone

from env_loader import load_dotenv

load_dotenv()

# Imported after load_dotenv() so the Maps and AI modules pick up .env values.
import google_maps  # noqa: E402
import ai_assistant  # noqa: E402

app = Flask(__name__, static_folder="frontend", static_url_path="")
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB upload cap

DB = "civicfix.db"

# Paste your Google Maps API key here (enable "Maps JavaScript API" in Google Cloud),
# or set the GOOGLE_MAPS_API_KEY environment variable when hosting.
# Leave empty ("") to use free OpenStreetMap maps instead — no key or billing needed.
# Tip: restrict the key to your domain via HTTP referrer restrictions in Google Cloud.
#
# With a key set, CivicFix also unlocks the server-side Maps Platform features
# (geocoding, place autocomplete, directions and nearby civic services) through
# google_maps.py. Enable these APIs in Google Cloud for the full experience:
#   Maps JavaScript API · Geocoding API · Places API (New) · Directions API
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

# AI / LLM configuration — all optional, see ai_assistant.py for the full list.
#   AI_PROVIDER      auto | openai | gemini | anthropic | groq | custom
#   OPENAI_API_KEY   (also GEMINI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY,
#                     LLM_API_KEY + LLM_BASE_URL for any OpenAI-compatible server)
#   OPENAI_MODEL     optional model override (default gpt-4o-mini)
# With no key set, CivicFix falls back to its built-in rule engine so every AI
# feature (triage, resolution plans, assistant chat) still works offline.
AI_STATUS = ai_assistant.status()

# Your UPI ID for receiving contributions (e.g. "yourname@okhdfcbank").
# Set directly here or via the UPI_ID environment variable when hosting.
# When set, the Donate dialog generates a working "Pay via UPI" deep link that
# opens GPay / PhonePe / Paytm with the amount pre-filled. No gateway fees.
UPI_ID = os.environ.get("UPI_ID", "")

UPLOAD_DIR = os.path.join("frontend", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_IMAGE_TYPES = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}


def utcnow():
    return datetime.now(timezone.utc).isoformat()

CATEGORIES = {"Pothole", "Garbage", "Streetlight", "Water Leak", "Road Damage", "Other"}
STATUSES = {"Reported", "In Progress", "Resolved"}

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            severity INTEGER NOT NULL CHECK(severity BETWEEN 1 AND 5),
            impact INTEGER NOT NULL CHECK(impact BETWEEN 1 AND 5),
            urgency INTEGER NOT NULL CHECK(urgency BETWEEN 1 AND 5),
            priority INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Reported',
            image_url TEXT,
            upvotes INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    # Migration: add columns to pre-existing databases
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(issues)")]
    if "upvotes" not in cols:
        conn.execute("ALTER TABLE issues ADD COLUMN upvotes INTEGER NOT NULL DEFAULT 0")
    if "state" not in cols:
        conn.execute("ALTER TABLE issues ADD COLUMN state TEXT NOT NULL DEFAULT ''")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            author TEXT NOT NULL DEFAULT 'Anonymous',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT 'Website Issue',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            donor_name TEXT NOT NULL DEFAULT 'Anonymous',
            amount REAL NOT NULL CHECK(amount > 0),
            issue_id INTEGER REFERENCES issues(id) ON DELETE SET NULL,
            message TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT 'UPI',
            status TEXT NOT NULL DEFAULT 'Paid',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Other',
            amount REAL NOT NULL CHECK(amount > 0),
            issue_id INTEGER REFERENCES issues(id) ON DELETE SET NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS issue_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            plan TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'rules',
            model TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    count = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    if count == 0:
        seed = [
            ("Large pothole near college gate", "Pothole",
             "Deep pothole creating a safety risk for bikes and pedestrians.",
             "Kolkata, West Bengal", 22.5726, 88.3639, 5, 4, 5, "Reported", 14),
            ("Overflowing garbage bin", "Garbage",
             "Garbage has not been collected for several days.",
             "Salt Lake, Kolkata", 22.5800, 88.4200, 3, 4, 4, "In Progress", 9),
            ("Streetlight not working", "Streetlight",
             "Dark road section affecting evening commuters.",
             "New Town, Kolkata", 22.5958, 88.4797, 4, 3, 4, "Resolved", 6),
        ]
        for item in seed:
            *data, status, upvotes = item
            severity, impact, urgency = data[6], data[7], data[8]
            priority = calculate_priority(severity, impact, urgency)
            conn.execute("""INSERT INTO issues
                (title,category,description,location,latitude,longitude,severity,impact,urgency,priority,status,image_url,upvotes,state,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*data[:6], severity, impact, urgency, priority, status, "", upvotes, "West Bengal", utcnow()))

    if conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 0:
        conn.execute("""INSERT INTO donations (donor_name, amount, issue_id, message, method, status, created_at)
            VALUES ('Aarav', 500.0, 1, 'Fix it soon, students cross here daily.', 'UPI', 'Paid', ?),
                    ('Priya', 250.0, NULL, 'Keep up the good work.', 'UPI', 'Paid', ?)""",
            (utcnow(), utcnow()))
    if conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 0:
        conn.execute("""INSERT INTO expenses (title, category, amount, issue_id, notes, created_at)
            VALUES ('Cold-mix asphalt bag', 'Materials', 350.0, 1, 'Pothole patching material.', ?)""",
            (utcnow(),))
    conn.commit()
    conn.close()

def calculate_priority(severity, impact, urgency):
    # Weighted score: safety severity 45%, community impact 30%, urgency 25%.
    return round((severity * 0.45 + impact * 0.30 + urgency * 0.25) * 20)

def serialize(row):
    return dict(row)

@app.get("/")
def home():
    return send_from_directory("frontend", "index.html")

@app.get("/api/issues")
def get_issues():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    status = request.args.get("status", "").strip()
    state = request.args.get("state", "").strip()
    sort = request.args.get("sort", "priority")

    conn = db()
    sql = "SELECT * FROM issues WHERE 1=1"
    params = []

    if q:
        sql += " AND (title LIKE ? OR description LIKE ? OR location LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    if category:
        sql += " AND category = ?"
        params.append(category)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if state:
        sql += " AND state = ?"
        params.append(state)

    order = {
        "priority": "priority DESC",
        "newest": "created_at DESC",
        "upvotes": "upvotes DESC, priority DESC"
    }.get(sort, "priority DESC")
    sql += f" ORDER BY {order}"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([serialize(r) for r in rows])

@app.post("/api/issues")
def create_issue():
    data = request.get_json(silent=True) or {}
    required = ["title", "category", "description", "location", "severity", "impact", "urgency"]

    missing = [x for x in required if x not in data or str(data[x]).strip() == ""]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    if data["category"] not in CATEGORIES:
        return jsonify({"error": "Invalid category"}), 400

    try:
        severity = int(data["severity"])
        impact = int(data["impact"])
        urgency = int(data["urgency"])
        if not all(1 <= x <= 5 for x in [severity, impact, urgency]):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Severity, impact and urgency must be integers from 1 to 5"}), 400

    priority = calculate_priority(severity, impact, urgency)
    now = utcnow()

    state = str(data.get("state", "")).strip()[:60]
    conn = db()
    cur = conn.execute("""INSERT INTO issues
        (title,category,description,location,latitude,longitude,severity,impact,urgency,priority,status,image_url,upvotes,state,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data["title"].strip(), data["category"], data["description"].strip(),
         data["location"].strip(), data.get("latitude"), data.get("longitude"),
         severity, impact, urgency, priority, "Reported", data.get("image_url", ""), 0, state, now))
    conn.commit()
    issue_id = cur.lastrowid
    row = conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
    conn.close()
    return jsonify(serialize(row)), 201

@app.patch("/api/issues/<int:issue_id>")
def update_issue(issue_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in STATUSES:
        return jsonify({"error": "Invalid status"}), 400

    conn = db()
    cur = conn.execute("UPDATE issues SET status=? WHERE id=?", (status, issue_id))
    conn.commit()
    row = conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
    conn.close()

    if cur.rowcount == 0:
        return jsonify({"error": "Issue not found"}), 404
    return jsonify(serialize(row))

@app.post("/api/issues/<int:issue_id>/upvote")
def upvote_issue(issue_id):
    conn = db()
    cur = conn.execute("UPDATE issues SET upvotes = upvotes + 1 WHERE id=?", (issue_id,))
    conn.commit()
    row = conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "Issue not found"}), 404
    return jsonify(serialize(row))


@app.get("/api/issues/<int:issue_id>/comments")
def get_comments(issue_id):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM comments WHERE issue_id=? ORDER BY created_at ASC", (issue_id,)
    ).fetchall()
    conn.close()
    return jsonify([serialize(r) for r in rows])


@app.post("/api/issues/<int:issue_id>/comments")
def create_comment(issue_id):
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Comment cannot be empty"}), 400
    if len(message) > 500:
        return jsonify({"error": "Comment too long (max 500 characters)"}), 400

    conn = db()
    issue = conn.execute("SELECT id FROM issues WHERE id=?", (issue_id,)).fetchone()
    if not issue:
        conn.close()
        return jsonify({"error": "Issue not found"}), 404
    author = str(data.get("author", "")).strip() or "Anonymous"
    cur = conn.execute(
        "INSERT INTO comments (issue_id, author, message, created_at) VALUES (?,?,?,?)",
        (issue_id, author[:50], message, utcnow()))
    conn.commit()
    row = conn.execute("SELECT * FROM comments WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(serialize(row)), 201


@app.delete("/api/issues/<int:issue_id>")
def delete_issue(issue_id):
    conn = db()
    cur = conn.execute("DELETE FROM issues WHERE id=?", (issue_id,))
    conn.execute("DELETE FROM comments WHERE issue_id=?", (issue_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "Issue not found"}), 404
    return jsonify({"deleted": issue_id})


@app.post("/api/contact")
def create_contact():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip()
    topic = str(data.get("topic", "")).strip() or "Website Issue"
    message = str(data.get("message", "")).strip()

    if not name or not email or not message:
        return jsonify({"error": "Name, email and message are required"}), 400
    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Please enter a valid email address"}), 400
    if len(message) > 1000:
        return jsonify({"error": "Message too long (max 1000 characters)"}), 400

    conn = db()
    cur = conn.execute(
        "INSERT INTO contact_messages (name, email, topic, message, created_at) VALUES (?,?,?,?,?)",
        (name[:80], email[:120], topic[:60], message, utcnow()))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return jsonify({"ok": True, "id": row_id}), 201


@app.get("/api/config")
def config():
    return jsonify({
        "google_maps_key": GOOGLE_MAPS_API_KEY,
        "upi_id": UPI_ID,
        "maps": google_maps.status(),
        "ai": ai_assistant.status(),
    })


@app.post("/api/upload")
def upload_image():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    data = file.read()
    if len(data) > app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({"error": "File too large (max 8 MB)"}), 413

    ext = None
    for signature, known_ext in ALLOWED_IMAGE_TYPES.items():
        if data.startswith(signature):
            ext = known_ext
            break
    if not ext:
        return jsonify({"error": "Only JPG, PNG, GIF or WebP images are allowed"}), 400

    filename = uuid.uuid4().hex + ext
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        f.write(data)
    return jsonify({"url": f"/uploads/{filename}"}), 201


@app.get("/api/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/api/donations")
def list_donations():
    limit = min(int(request.args.get("limit", 20)), 100)
    conn = db()
    rows = conn.execute("SELECT * FROM donations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return jsonify([serialize(r) for r in rows])


@app.post("/api/donations")
def create_donation():
    data = request.get_json(silent=True) or {}
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number"}), 400
    if amount <= 0 or amount > 500000:
        return jsonify({"error": "Amount must be between ₹1 and ₹5,00,000"}), 400

    donor = str(data.get("donor_name", "")).strip()[:60] or "Anonymous"
    message = str(data.get("message", "")).strip()[:300]
    method = str(data.get("method", "UPI")).strip()[:30]
    issue_id = data.get("issue_id")

    conn = db()
    if issue_id is not None:
        issue_id = int(issue_id)
        if not conn.execute("SELECT id FROM issues WHERE id=?", (issue_id,)).fetchone():
            conn.close()
            return jsonify({"error": "Issue not found"}), 404
    cur = conn.execute(
        """INSERT INTO donations (donor_name, amount, issue_id, message, method, status, created_at)
           VALUES (?,?,?,?,?,'Paid',?)""",
        (donor, amount, issue_id, message, method or "UPI", utcnow()))
    conn.commit()
    row = conn.execute("SELECT * FROM donations WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(serialize(row)), 201


@app.delete("/api/donations/<int:donation_id>")
def delete_donation(donation_id):
    conn = db()
    cur = conn.execute("DELETE FROM donations WHERE id=?", (donation_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "Donation not found"}), 404
    return jsonify({"deleted": donation_id})


EXPENSE_CATEGORIES = {"Materials", "Labor", "Transport", "Equipment", "Other"}


@app.get("/api/expenses")
def list_expenses():
    limit = min(int(request.args.get("limit", 20)), 100)
    conn = db()
    rows = conn.execute("SELECT * FROM expenses ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return jsonify([serialize(r) for r in rows])


@app.post("/api/expenses")
def create_expense():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    category = str(data.get("category", "")).strip()
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number"}), 400

    if not title:
        return jsonify({"error": "Title is required"}), 400
    if category not in EXPENSE_CATEGORIES:
        return jsonify({"error": f"Category must be one of: {', '.join(sorted(EXPENSE_CATEGORIES))}"}), 400
    if amount <= 0 or amount > 500000:
        return jsonify({"error": "Amount must be between ₹1 and ₹5,00,000"}), 400

    notes = str(data.get("notes", "")).strip()[:300]
    issue_id = data.get("issue_id")

    conn = db()
    if issue_id in ("", None):
        issue_id = None
    else:
        issue_id = int(issue_id)
        if not conn.execute("SELECT id FROM issues WHERE id=?", (issue_id,)).fetchone():
            conn.close()
            return jsonify({"error": "Issue not found"}), 404
    cur = conn.execute(
        """INSERT INTO expenses (title, category, amount, issue_id, notes, created_at)
           VALUES (?,?,?,?,?,?)""",
        (title[:120], category, amount, issue_id, notes, utcnow()))
    conn.commit()
    row = conn.execute("SELECT * FROM expenses WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(serialize(row)), 201


@app.delete("/api/expenses/<int:expense_id>")
def delete_expense(expense_id):
    conn = db()
    cur = conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "Expense not found"}), 404
    return jsonify({"deleted": expense_id})


@app.get("/api/funds")
def funds_summary():
    conn = db()
    total_donated = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM donations WHERE status='Paid'").fetchone()[0]
    donation_count = conn.execute("SELECT COUNT(*) FROM donations WHERE status='Paid'").fetchone()[0]
    total_spent = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
    recent_donations = conn.execute(
        "SELECT * FROM donations ORDER BY created_at DESC LIMIT 6").fetchall()
    recent_expenses = conn.execute(
        "SELECT * FROM expenses ORDER BY created_at DESC LIMIT 6").fetchall()
    by_category = conn.execute("""
        SELECT category, COALESCE(SUM(amount),0) AS total
        FROM expenses GROUP BY category ORDER BY total DESC""").fetchall()
    conn.close()
    return jsonify({
        "total_raised": round(total_donated, 2),
        "total_spent": round(total_spent, 2),
        "balance": round(total_donated - total_spent, 2),
        "donation_count": donation_count,
        "recent_donations": [serialize(r) for r in recent_donations],
        "recent_expenses": [serialize(r) for r in recent_expenses],
        "spend_by_category": [dict(r) for r in by_category]
    })


@app.get("/api/stats")
def stats():
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    reported = conn.execute("SELECT COUNT(*) FROM issues WHERE status='Reported'").fetchone()[0]
    progress = conn.execute("SELECT COUNT(*) FROM issues WHERE status='In Progress'").fetchone()[0]
    resolved = conn.execute("SELECT COUNT(*) FROM issues WHERE status='Resolved'").fetchone()[0]
    avg = conn.execute("SELECT COALESCE(AVG(priority),0) FROM issues").fetchone()[0]
    top = conn.execute("""
        SELECT category, COUNT(*) count
        FROM issues GROUP BY category ORDER BY count DESC LIMIT 5
    """).fetchall()
    conn.close()
    resolution_rate = round((resolved / total) * 100) if total else 0
    return jsonify({
        "total": total, "reported": reported, "in_progress": progress,
        "resolved": resolved, "average_priority": round(avg, 1),
        "resolution_rate": resolution_rate,
        "top_categories": [dict(x) for x in top]
    })


@app.get("/api/analytics")
def analytics():
    conn = db()
    categories = conn.execute("""
        SELECT category, COUNT(*) AS count FROM issues
        GROUP BY category ORDER BY count DESC
    """).fetchall()
    priority_dist = conn.execute("""
        SELECT
          SUM(CASE WHEN priority >= 80 THEN 1 ELSE 0 END) AS critical,
          SUM(CASE WHEN priority >= 60 AND priority < 80 THEN 1 ELSE 0 END) AS high,
          SUM(CASE WHEN priority >= 40 AND priority < 60 THEN 1 ELSE 0 END) AS medium,
          SUM(CASE WHEN priority < 40 THEN 1 ELSE 0 END) AS low
        FROM issues
    """).fetchone()

    # Last 7 days of activity, filling in empty days
    trend_rows = conn.execute("""
        SELECT date(created_at) AS day, COUNT(*) AS count
        FROM issues
        WHERE date(created_at) >= date('now', '-6 days')
        GROUP BY day
    """).fetchall()
    trend_map = {row["day"]: row["count"] for row in trend_rows}
    from datetime import date, timedelta
    today = datetime.now(timezone.utc).date()
    trend = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        key = day.isoformat()
        trend.append({"day": key, "label": day.strftime("%a"), "count": trend_map.get(key, 0)})
    conn.close()

    return jsonify({
        "categories": [dict(x) for x in categories],
        "priority_distribution": dict(priority_dist),
        "weekly_trend": trend
    })

# --------------------------------------------------------------------------- #
# Google Maps Platform — server side proxy
# The key stays on the server, and every call falls back to free OpenStreetMap
# services when no key is configured.
# --------------------------------------------------------------------------- #

def _parse_latlng(value):
    if not value:
        return None
    parts = str(value).replace(" ", "").split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


@app.get("/api/maps/status")
def maps_status():
    return jsonify(google_maps.status())


@app.get("/api/maps/geocode")
def maps_geocode():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "An address or place name is required"}), 400
    result = google_maps.geocode(query, region=request.args.get("region", "in"))
    if not result:
        return jsonify({"error": "No matching location found"}), 404
    return jsonify(result)


@app.get("/api/maps/reverse")
def maps_reverse():
    point = _parse_latlng(f"{request.args.get('lat')},{request.args.get('lng')}")
    if not point:
        return jsonify({"error": "lat and lng are required"}), 400
    result = google_maps.reverse_geocode(point[0], point[1])
    if not result:
        return jsonify({"error": "No address found for these coordinates"}), 404
    return jsonify(result)


@app.get("/api/maps/autocomplete")
def maps_autocomplete():
    query = request.args.get("q", "").strip()
    if len(query) < 3:
        return jsonify({"predictions": []})
    predictions = google_maps.autocomplete(query, region=request.args.get("region", "in"))
    return jsonify({"predictions": predictions, "provider": google_maps.provider_name()})


@app.get("/api/maps/place/<path:place_id>")
def maps_place(place_id):
    result = google_maps.place_details(place_id)
    if not result:
        return jsonify({"error": "Place details not found"}), 404
    return jsonify(result)


@app.get("/api/maps/directions")
def maps_directions():
    origin = _parse_latlng(request.args.get("origin"))
    destination = _parse_latlng(request.args.get("destination"))
    if not origin or not destination:
        return jsonify({"error": "origin and destination must be 'lat,lng'"}), 400
    mode = request.args.get("mode", "driving")
    if mode not in ("driving", "walking", "bicycling", "transit"):
        mode = "driving"
    result = google_maps.directions(origin, destination, mode)
    if not result:
        return jsonify({"error": "Could not find a route"}), 404
    return jsonify(result)


@app.get("/api/maps/nearby")
def maps_nearby():
    point = _parse_latlng(f"{request.args.get('lat')},{request.args.get('lng')}")
    if not point:
        return jsonify({"error": "lat and lng are required"}), 400
    kind = request.args.get("kind", "hospital")
    try:
        radius = int(request.args.get("radius", 3000))
    except ValueError:
        radius = 3000
    places = google_maps.nearby_places(point[0], point[1], kind=kind, radius=radius)
    return jsonify({"kind": kind, "label": google_maps.NEARBY_KINDS.get(kind, {}).get("label", kind),
                    "places": places, "provider": google_maps.provider_name()})


# --------------------------------------------------------------------------- #
# AI assistant — triage, resolution plans, chat and city insights
# --------------------------------------------------------------------------- #

def _issue_digest(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "description": row["description"][:400],
        "location": row["location"],
        "state": row["state"],
        "status": row["status"],
        "severity": row["severity"],
        "impact": row["impact"],
        "urgency": row["urgency"],
        "priority": row["priority"],
        "upvotes": row["upvotes"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "created_at": row["created_at"],
    }


def _collect_ai_context(message=""):
    """Ground the assistant in the real database before asking the model."""
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    reported = conn.execute("SELECT COUNT(*) FROM issues WHERE status='Reported'").fetchone()[0]
    progress = conn.execute("SELECT COUNT(*) FROM issues WHERE status='In Progress'").fetchone()[0]
    resolved = conn.execute("SELECT COUNT(*) FROM issues WHERE status='Resolved'").fetchone()[0]
    avg_priority = conn.execute("SELECT COALESCE(AVG(priority),0) FROM issues").fetchone()[0]

    top_rows = conn.execute(
        "SELECT * FROM issues WHERE status != 'Resolved' ORDER BY priority DESC, upvotes DESC LIMIT 5"
    ).fetchall()
    category_rows = conn.execute(
        "SELECT category, COUNT(*) AS count FROM issues GROUP BY category ORDER BY count DESC"
    ).fetchall()
    priority_dist = conn.execute("""
        SELECT
          SUM(CASE WHEN priority >= 80 THEN 1 ELSE 0 END) AS critical,
          SUM(CASE WHEN priority >= 60 AND priority < 80 THEN 1 ELSE 0 END) AS high,
          SUM(CASE WHEN priority >= 40 AND priority < 60 THEN 1 ELSE 0 END) AS medium,
          SUM(CASE WHEN priority < 40 THEN 1 ELSE 0 END) AS low
        FROM issues
    """).fetchone()

    total_raised = conn.execute("SELECT COALESCE(SUM(amount),0) FROM donations WHERE status='Paid'").fetchone()[0]
    total_spent = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
    donors = conn.execute("SELECT COUNT(*) FROM donations WHERE status='Paid'").fetchone()[0]

    mentioned_issue = None
    match = re.search(r"#?\b(\d{1,6})\b", message or "")
    if match:
        row = conn.execute("SELECT * FROM issues WHERE id=?", (int(match.group(1)),)).fetchone()
        if row:
            mentioned_issue = _issue_digest(row)

    text = (message or "").lower()
    mentioned_category = None
    for category in sorted(CATEGORIES, key=len, reverse=True):
        if category.lower() in text:
            mentioned_category = category
            break

    category_issues = []
    if mentioned_category:
        category_issues = [
            _issue_digest(r) for r in conn.execute(
                "SELECT * FROM issues WHERE category=? AND status != 'Resolved' ORDER BY priority DESC LIMIT 5",
                (mentioned_category,)
            ).fetchall()
        ]

    conn.close()

    playbook = None
    if mentioned_category:
        source = ai_assistant.PLAYBOOKS.get(mentioned_category, ai_assistant.PLAYBOOKS["Other"])
        playbook = dict(source)
        playbook["label"] = mentioned_category

    return {
        "stats": {
            "total": total,
            "reported": reported,
            "in_progress": progress,
            "resolved": resolved,
            "resolution_rate": round((resolved / total) * 100) if total else 0,
            "average_priority": round(avg_priority, 1),
        },
        "funds": {
            "total_raised": round(total_raised, 2),
            "total_spent": round(total_spent, 2),
            "balance": round(total_raised - total_spent, 2),
            "donation_count": donors,
        },
        "analytics": {
            "categories": [dict(r) for r in category_rows],
            "priority_distribution": dict(priority_dist),
        },
        "top_categories": [r["category"] for r in category_rows],
        "top_issues": [_issue_digest(r) for r in top_rows],
        "mentioned_issue": mentioned_issue,
        "mentioned_category": mentioned_category,
        "category_issues": category_issues,
        "playbook": playbook,
    }


@app.get("/api/ai/status")
def ai_status():
    return jsonify(ai_assistant.status())


@app.post("/api/ai/triage")
def ai_triage():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    location = str(data.get("location", "")).strip()
    state = str(data.get("state", "")).strip()
    if not title and not description:
        return jsonify({"error": "Add a title or a description first"}), 400

    result = ai_assistant.triage(title, description, location, state)
    result["priority"] = calculate_priority(result["severity"], result["impact"], result["urgency"])

    # Flag near-duplicates so residents do not open the same ticket twice.
    conn = db()
    if result["category"]:
        neighbours = conn.execute(
            "SELECT * FROM issues WHERE category=? AND status != 'Resolved' ORDER BY priority DESC LIMIT 5",
            (result["category"],)
        ).fetchall()
        words = {w for w in re.findall(r"[a-z]{4,}", (title + " " + description).lower())}
        duplicates = []
        for row in neighbours:
            other = {w for w in re.findall(r"[a-z]{4,}", row["title"].lower())}
            if words and other and len(words & other) >= 2:
                duplicates.append({"id": row["id"], "title": row["title"], "status": row["status"]})
        result["possible_duplicates"] = duplicates[:3]
    conn.close()

    return jsonify(result)


@app.post("/api/ai/describe")
def ai_describe():
    data = request.get_json(silent=True) or {}
    text = str(data.get("description", "")).strip()
    if not text:
        return jsonify({"error": "Write a rough description first"}), 400
    category = str(data.get("category", "Other")).strip() or "Other"
    return jsonify(ai_assistant.describe(text, category))


@app.post("/api/ai/resolve")
def ai_resolve():
    data = request.get_json(silent=True) or {}
    issue_id = data.get("issue_id")

    conn = db()
    if issue_id in (None, ""):
        conn.close()
        return jsonify({"error": "issue_id is required"}), 400
    row = conn.execute("SELECT * FROM issues WHERE id=?", (int(issue_id),)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Issue not found"}), 404

    issue = _issue_digest(row)
    comments = [dict(c) for c in conn.execute(
        "SELECT author, message FROM comments WHERE issue_id=? ORDER BY created_at DESC LIMIT 5",
        (issue["id"],)
    ).fetchall()]
    donations = [dict(d) for d in conn.execute(
        "SELECT donor_name, amount, message FROM donations WHERE issue_id=?", (issue["id"],)
    ).fetchall()]
    conn.close()

    issue["comments"] = comments
    issue["support"] = donations

    plan = ai_assistant.resolve_plan(issue)
    plan["issue_id"] = issue["id"]

    conn = db()
    conn.execute("DELETE FROM issue_plans WHERE issue_id=?", (issue["id"],))
    conn.execute(
        "INSERT INTO issue_plans (issue_id, plan, source, model, created_at) VALUES (?,?,?,?,?)",
        (issue["id"], json.dumps(plan), plan.get("source", "rules"), plan.get("model", ""), utcnow()))
    conn.commit()
    conn.close()
    return jsonify(plan)


@app.get("/api/ai/insights")
def ai_insights():
    context = _collect_ai_context("")
    return jsonify(ai_assistant.insights(context))


@app.post("/api/ai/assistant")
def ai_assistant_chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400
    history = data.get("history") or []
    if not isinstance(history, list):
        history = []

    context = _collect_ai_context(message)
    result = ai_assistant.assist(message, history, context)
    result["data_used"] = {
        "mentioned_issue": context["mentioned_issue"]["id"] if context["mentioned_issue"] else None,
        "mentioned_category": context["mentioned_category"],
        "top_issues": [i["id"] for i in context["top_issues"]],
    }
    return jsonify(result)


init_db()

if __name__ == "__main__":
    # Debug mode is ON by default for local development (auto-reload on edits).
    # ALWAYS run hosted/publicly with FLASK_DEBUG=0 — the debugger must never be public.
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=debug_mode)
