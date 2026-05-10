from flask import Flask, render_template, request, redirect, make_response, session, jsonify
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from io import BytesIO
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import time
import textwrap

app = Flask(__name__)
app.secret_key = "resume_secret_key_change_in_production"

UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ─────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────

def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    conn = get_db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT    NOT NULL,
        email    TEXT    UNIQUE NOT NULL,
        password TEXT    NOT NULL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS resume_versions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email     TEXT,
        name           TEXT,
        email          TEXT,
        phone          TEXT,
        linkedin       TEXT,
        location       TEXT,
        objective      TEXT,
        skills         TEXT,
        education      TEXT,
        experience     TEXT,
        photo          TEXT,
        template       TEXT,
        version_number INTEGER,
        score          INTEGER,
        label          TEXT,
        is_autosave    INTEGER DEFAULT 0,
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


create_tables()


# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────

def calculate_score(skills, education, experience, photo):
    skills     = skills     or ""
    education  = education  or ""
    experience = experience or ""

    score  = min(len(skills)     // 2, 35)
    score += min(len(education)  // 3, 25)
    score += min(len(experience) // 3, 30)
    if photo and photo != "noimage.png":
        score += 10

    return min(score, 100)


def get_rank(score):
    if score >= 90: return "Excellent"
    if score >= 70: return "Good"
    if score >= 50: return "Average"
    return "Poor"


def login_required(f):
    """Simple session guard decorator."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def next_version_number():
    conn = get_db()
    last = conn.execute("SELECT MAX(version_number) FROM resume_versions").fetchone()[0]
    conn.close()
    return 1 if last is None else last + 1


# ─────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────

@app.route('/')
def home():
    return redirect('/login')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user"]       = user["name"]
            session["user_email"] = user["email"]
            return redirect("/dashboard")

        return render_template("login.html", error="Invalid email or password.")

    return render_template("login.html")


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not name or not email or not password:
            return render_template("register.html", error="All fields are required.")

        hashed = generate_password_hash(password)

        try:
            conn = get_db()
            conn.execute("INSERT INTO users (name, email, password) VALUES (?,?,?)",
                         (name, email, hashed))
            conn.commit()
            conn.close()
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Email already registered.")

        return redirect("/login")

    return render_template("register.html")


@app.route('/logout')
def logout():
    session.clear()
    return redirect("/login")


# ─────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    total_users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_resumes = conn.execute("SELECT COUNT(*) FROM resume_versions").fetchone()[0]
    conn.close()

    return render_template("dashboard.html",
                           total_users=total_users,
                           total_resumes=total_resumes)


# ─────────────────────────────────────────
#  RESUME BUILDER
# ─────────────────────────────────────────

@app.route('/resume', methods=['GET', 'POST'])
@login_required
def resume():
    if request.method == "POST":
        name       = request.form.get("name", "")
        email      = request.form.get("email", "")
        phone      = request.form.get("phone", "")
        linkedin   = request.form.get("linkedin", "")
        objective  = request.form.get("objective", "")
        skills     = request.form.get("skills", "")
        education  = request.form.get("education", "")
        experience = request.form.get("experience", "")
        location   = request.form.get("location", "")
        template   = request.form.get("template", "AI Modern")
        jobtitle   = request.form.get("jobtitle", "")

        photo_file = request.files.get("photo")
        filename   = "noimage.png"

        if photo_file and photo_file.filename:
            filename = f"{int(time.time())}_{photo_file.filename}"
            photo_file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        score   = calculate_score(skills, education, experience, filename)
        version = next_version_number()
        label   = f"{name}'s Resume" if name else "Draft"

        conn = get_db()
        conn.execute("""
            INSERT INTO resume_versions
                (user_email, name, email, phone, linkedin, location,
                 objective, skills, education, experience, photo,
                 template, version_number, score, label, is_autosave)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
        """, (
            session.get("user_email"), name, email, phone, linkedin, location,
            objective, skills, education, experience, filename,
            template, version, score, label
        ))
        conn.commit()
        conn.close()

        return redirect("/versions")

    return render_template("resume.html")


# ─────────────────────────────────────────
#  AUTO-SAVE API  (called by JS every 5 min)
# ─────────────────────────────────────────

@app.route('/autosave', methods=['POST'])
@login_required
def autosave():
    """Receives JSON from the frontend and saves a version snapshot."""
    data = request.get_json(silent=True) or {}

    name       = data.get("name", "")
    email      = data.get("email", "")
    phone      = data.get("phone", "")
    linkedin   = data.get("linkedin", "")
    objective  = data.get("objective", "")
    skills     = data.get("skills", "")
    education  = data.get("education", "")
    experience = data.get("experience", "")
    location   = data.get("location", "")
    template   = data.get("template", "AI Modern")
    label      = data.get("label", f"{name}'s Resume" if name else "Auto-save")

    score   = calculate_score(skills, education, experience, None)
    version = next_version_number()

    conn = get_db()
    conn.execute("""
        INSERT INTO resume_versions
            (user_email, name, email, phone, linkedin, location,
             objective, skills, education, experience, photo,
             template, version_number, score, label, is_autosave)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
    """, (
        session.get("user_email"), name, email, phone, linkedin, location,
        objective, skills, education, experience, "noimage.png",
        template, version, score, label
    ))
    conn.commit()
    conn.close()

    return jsonify({"status": "saved", "version": version, "score": score})


# ─────────────────────────────────────────
#  VERSIONS LIST
# ─────────────────────────────────────────

@app.route('/versions')
@login_required
def versions():
    conn = get_db()
    resumes = conn.execute(
        "SELECT * FROM resume_versions ORDER BY id DESC"
    ).fetchall()
    conn.close()

    data = [(r, get_rank(r["score"])) for r in resumes]
    return render_template("versions.html", resume_data=data)


# ─────────────────────────────────────────
#  PREVIEW
# ─────────────────────────────────────────

@app.route('/preview/<int:id>')
@login_required
def preview(id):
    conn = get_db()
    resume = conn.execute("SELECT * FROM resume_versions WHERE id=?", (id,)).fetchone()
    conn.close()

    if not resume:
        return redirect("/versions")

    resume = dict(resume)  # convert sqlite3.Row to dict so .get() works
    return render_template("preview.html",
                           resume=resume,
                           rank=get_rank(resume["score"]))


# ─────────────────────────────────────────
#  DELETE
# ─────────────────────────────────────────

@app.route('/delete/<int:id>')
@login_required
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM resume_versions WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/versions")


# ─────────────────────────────────────────
#  PDF GENERATION  (attractive two-column)
# ─────────────────────────────────────────

# Color palette
C_DARK    = HexColor("#1a1410")   # sidebar background
C_GOLD    = HexColor("#c9a84c")   # accent
C_CREAM   = HexColor("#f5f0e8")   # light text
C_MUTED   = HexColor("#9a8f82")   # muted text
C_WHITE   = white
C_RULE    = HexColor("#e8e0d0")   # horizontal rule


def draw_wrapped(c, text, x, y, max_width, font_name, font_size,
                 line_height, color=None, leading=None):
    """
    Word-wrap `text` inside `max_width` points.
    Returns the Y position after the last line drawn.
    """
    if color:
        c.setFillColor(color)
    c.setFont(font_name, font_size)

    words  = (text or "").split()
    line   = ""
    lh     = leading or line_height

    for word in words:
        test = (line + " " + word).strip()
        if c.stringWidth(test, font_name, font_size) <= max_width:
            line = test
        else:
            if line:
                c.drawString(x, y, line)
                y -= lh
            line = word
    if line:
        c.drawString(x, y, line)
        y -= lh

    return y


def section_heading(c, title, x, y, width, text_color, rule_color):
    """Draw a labelled section heading with a rule underneath."""
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(text_color)
    c.drawString(x, y, title.upper())
    y -= 5
    c.setStrokeColor(rule_color)
    c.setLineWidth(0.5)
    c.line(x, y, x + width, y)
    return y - 10


@app.route('/pdf/<int:id>')
@login_required
def pdf(id):
    conn = get_db()
    r    = conn.execute("SELECT * FROM resume_versions WHERE id=?", (id,)).fetchone()
    conn.close()

    if not r:
        return redirect("/versions")

    r = dict(r)  # convert sqlite3.Row to dict so .get() works

    buffer = BytesIO()
    c      = canvas.Canvas(buffer)

    PAGE_W, PAGE_H = 595, 842   # A4 portrait
    SB_W           = 195        # sidebar width
    MARGIN         = 24
    COL_X          = SB_W + 28  # right column start X
    COL_W          = PAGE_W - SB_W - 28 - MARGIN  # right column width

    # ── Sidebar background ──────────────────────────────────────────
    c.setFillColor(C_DARK)
    c.rect(0, 0, SB_W, PAGE_H, fill=1, stroke=0)

    # Gold left accent stripe on right column
    c.setFillColor(C_GOLD)
    c.rect(SB_W, 0, 3, PAGE_H, fill=1, stroke=0)

    # ── Photo ───────────────────────────────────────────────────────
    photo_size = 90
    photo_x    = (SB_W - photo_size) // 2
    photo_y    = PAGE_H - 30 - photo_size

    if r["photo"] and r["photo"] != "noimage.png":
        img_path = os.path.join(app.config["UPLOAD_FOLDER"], r["photo"])
        if os.path.exists(img_path):
            # Circular clip mask via ellipse path
            c.saveState()
            p = c.beginPath()
            cx = photo_x + photo_size / 2
            cy = photo_y + photo_size / 2
            p.circle(cx, cy, photo_size / 2)
            c.clipPath(p, stroke=0)
            c.drawImage(img_path, photo_x, photo_y,
                        width=photo_size, height=photo_size,
                        preserveAspectRatio=True, mask='auto')
            c.restoreState()
            # Gold circle border
            c.setStrokeColor(C_GOLD)
            c.setLineWidth(2)
            c.circle(photo_x + photo_size / 2, photo_y + photo_size / 2,
                     photo_size / 2, fill=0, stroke=1)
    else:
        # Placeholder circle
        c.setStrokeColor(C_GOLD)
        c.setLineWidth(1.5)
        c.setFillColor(HexColor("#2a2520"))
        c.circle(photo_x + photo_size / 2, photo_y + photo_size / 2,
                 photo_size / 2, fill=1, stroke=1)

    # ── Sidebar name + title ─────────────────────────────────────────
    sb_y = photo_y - 16
    c.setFillColor(C_CREAM)
    c.setFont("Helvetica-Bold", 13)
    name_line = (r["name"] or "").strip()
    # centre-wrap name
    name_w = c.stringWidth(name_line, "Helvetica-Bold", 13)
    c.drawString((SB_W - name_w) / 2, sb_y, name_line)
    sb_y -= 14

    jobtitle = (r.get("jobtitle") or "").strip()
    if jobtitle:
        c.setFillColor(C_GOLD)
        c.setFont("Helvetica", 8)
        jt_w = c.stringWidth(jobtitle.upper(), "Helvetica", 8)
        c.drawString((SB_W - jt_w) / 2, sb_y, jobtitle.upper())
        sb_y -= 10

    # Thin gold rule
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(0.5)
    c.line(MARGIN, sb_y, SB_W - MARGIN, sb_y)
    sb_y -= 12

    # ── Contact block ────────────────────────────────────────────────
    c.setFillColor(C_GOLD)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(MARGIN, sb_y, "CONTACT")
    sb_y -= 10
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(0.4)
    c.line(MARGIN, sb_y, SB_W - MARGIN, sb_y)
    sb_y -= 10

    contact_lines = [
        ("✉", r["email"]    or ""),
        ("☎", r["phone"]    or ""),
        ("⌖", r["location"] or ""),
        ("in", r["linkedin"] or ""),
    ]

    for icon, val in contact_lines:
        if val.strip():
            c.setFillColor(C_GOLD)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(MARGIN, sb_y, icon)
            sb_y = draw_wrapped(c, val, MARGIN + 14, sb_y,
                                SB_W - MARGIN - 14 - 6,
                                "Helvetica", 8, 11, C_MUTED)
            sb_y -= 2

    sb_y -= 6

    # ── Objective (sidebar) ──────────────────────────────────────────
    c.setFillColor(C_GOLD)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(MARGIN, sb_y, "SUMMARY")
    sb_y -= 10
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(0.4)
    c.line(MARGIN, sb_y, SB_W - MARGIN, sb_y)
    sb_y -= 10

    sb_y = draw_wrapped(c, r["objective"], MARGIN, sb_y,
                        SB_W - MARGIN * 2, "Helvetica", 8, 12, C_MUTED)

    # ── AI Score badge ────────────────────────────────────────────────
    score_y = 55
    c.setFillColor(C_GOLD)
    c.roundRect(MARGIN, score_y, SB_W - MARGIN * 2, 36, 4, fill=1, stroke=0)
    c.setFillColor(C_DARK)
    c.setFont("Helvetica-Bold", 18)
    score_txt = f"{r['score']}/100"
    sw = c.stringWidth(score_txt, "Helvetica-Bold", 18)
    c.drawString((SB_W - sw) / 2, score_y + 20, score_txt)
    c.setFont("Helvetica", 7)
    rank_txt = f"AI SCORE — {get_rank(r['score']).upper()}"
    rw = c.stringWidth(rank_txt, "Helvetica", 7)
    c.drawString((SB_W - rw) / 2, score_y + 8, rank_txt)

    # ═══════════════════════════════════════════
    #  RIGHT COLUMN
    # ═══════════════════════════════════════════

    rc_y = PAGE_H - MARGIN

    # Big name heading
    c.setFillColor(C_DARK)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(COL_X, rc_y - 26, r["name"] or "")
    rc_y -= 30

    # Job title
    if jobtitle:
        c.setFillColor(C_GOLD)
        c.setFont("Helvetica", 9)
        c.drawString(COL_X, rc_y - 4, jobtitle.upper())
        rc_y -= 10

    # Rule under name
    rc_y -= 4
    c.setStrokeColor(C_DARK)
    c.setLineWidth(1.5)
    c.line(COL_X, rc_y, PAGE_W - MARGIN, rc_y)
    rc_y -= 14

    def right_section(title, content, is_skills=False):
        nonlocal rc_y
        rc_y = section_heading(c, title, COL_X, rc_y, COL_W, C_MUTED, C_RULE)

        if is_skills:
            # Render skills as pill chips
            skill_list = [s.strip() for s in (content or "").split(",") if s.strip()]
            x_cursor   = COL_X
            chip_h     = 14
            chip_pad   = 6

            for skill in skill_list:
                c.setFont("Helvetica", 8)
                sw = c.stringWidth(skill, "Helvetica", 8) + chip_pad * 2
                if x_cursor + sw > PAGE_W - MARGIN:
                    x_cursor = COL_X
                    rc_y    -= chip_h + 4

                # Chip background
                c.setFillColor(HexColor("#f5f0e8"))
                c.setStrokeColor(HexColor("#d4c89a"))
                c.setLineWidth(0.5)
                c.roundRect(x_cursor, rc_y - chip_h + 2, sw, chip_h, 3, fill=1, stroke=1)

                # Chip text
                c.setFillColor(HexColor("#4a3c20"))
                c.drawString(x_cursor + chip_pad, rc_y - chip_h + 6, skill)
                x_cursor += sw + 5

            rc_y -= chip_h + 12
        else:
            rc_y = draw_wrapped(c, content, COL_X, rc_y, COL_W,
                                "Helvetica", 9, 13, C_DARK)
            rc_y -= 10

    right_section("Professional Summary", r["objective"])
    right_section("Core Skills",          r["skills"], is_skills=True)
    right_section("Work Experience",      r["experience"])
    right_section("Education",            r["education"])

    c.save()

    pdf_bytes = buffer.getvalue()
    buffer.close()

    response = make_response(pdf_bytes)
    response.headers["Content-Type"]        = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{r["name"] or "resume"}_v{r["version_number"]}.pdf"'
    return response


# ─────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True)