from flask import Flask, render_template, request, redirect, make_response, session, jsonify
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from io import BytesIO
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import time

app = Flask(__name__)
app.secret_key = "resume_secret_key_change_in_production"

UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────

def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    conn = get_db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT
    )""")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS resume_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        name TEXT,
        email TEXT,
        phone TEXT,
        linkedin TEXT,
        location TEXT,
        objective TEXT,
        skills TEXT,
        education TEXT,
        experience TEXT,
        photo TEXT,
        template TEXT,
        version_number INTEGER,
        score INTEGER,
        label TEXT,
        is_autosave INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

create_tables()

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return wrap

def calculate_score(skills, education, experience, photo):
    score = min(len(skills or "") // 2, 35)
    score += min(len(education or "") // 3, 25)
    score += min(len(experience or "") // 3, 30)
    if photo and photo != "noimage.png":
        score += 10
    return min(score, 100)

def get_rank(score):
    if score >= 90: return "Excellent"
    if score >= 70: return "Good"
    if score >= 50: return "Average"
    return "Poor"

def next_version_number():
    conn = get_db()
    last = conn.execute("SELECT MAX(version_number) FROM resume_versions").fetchone()[0]
    conn.close()
    return 1 if last is None else last + 1

# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────

@app.route("/")
def home():
    return redirect("/login")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session["user"] = user["name"]
            session["user_email"] = user["email"]
            return redirect("/dashboard")
        return render_template("login.html", error="Invalid login")
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        hashed = generate_password_hash(request.form["password"])
        try:
            conn = get_db()
            conn.execute("INSERT INTO users VALUES (NULL,?,?,?)",
                (request.form["name"], request.form["email"], hashed))
            conn.commit()
            conn.close()
            return redirect("/login")
        except:
            return render_template("register.html", error="Email exists")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ─────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    resumes = conn.execute("SELECT COUNT(*) FROM resume_versions").fetchone()[0]
    conn.close()
    return render_template("dashboard.html", total_users=users, total_resumes=resumes)

# ─────────────────────────────────────────
# SETTINGS  ✅ (THIS FIXES YOUR 404 ERROR)
# ─────────────────────────────────────────

@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")

# ─────────────────────────────────────────
# RESUME BUILDER
# ─────────────────────────────────────────

@app.route("/resume", methods=["GET","POST"])
@login_required
def resume():
    if request.method == "POST":
        photo = request.files.get("photo")
        filename = "noimage.png"
        if photo and photo.filename:
            filename = str(int(time.time())) + photo.filename
            photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        score = calculate_score(
            request.form["skills"],
            request.form["education"],
            request.form["experience"],
            filename
        )

        conn = get_db()
        conn.execute("""
        INSERT INTO resume_versions VALUES
        (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP)
        """, (
            session["user_email"],
            request.form["name"],
            request.form["email"],
            request.form["phone"],
            request.form["linkedin"],
            request.form["location"],
            request.form["objective"],
            request.form["skills"],
            request.form["education"],
            request.form["experience"],
            filename,
            request.form["template"],
            next_version_number(),
            score,
            "Resume"
        ))
        conn.commit()
        conn.close()
        return redirect("/versions")
    return render_template("resume.html")

# ─────────────────────────────────────────
# VERSIONS
# ─────────────────────────────────────────

@app.route("/versions")
@login_required
def versions():
    conn = get_db()
    data = conn.execute("SELECT * FROM resume_versions ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("versions.html", resume_data=data)

# ─────────────────────────────────────────
# RUN
# ─────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True)