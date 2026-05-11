from flask import Flask, render_template, request, redirect, make_response, session, jsonify
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from io import BytesIO
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, time, re

app = Flask(__name__)
app.secret_key = "resume_secret_key_change_in_production"

UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── DATABASE ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS resume_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT, name TEXT, email TEXT, phone TEXT, linkedin TEXT,
        location TEXT, objective TEXT, skills TEXT, education TEXT,
        experience TEXT, photo TEXT, template TEXT, version_number INTEGER,
        score INTEGER, label TEXT, is_autosave INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()

create_tables()

# ── HELPERS ───────────────────────────────────────────────────────────────────

def calculate_score(skills, education, experience, photo):
    s = min(len(skills or "") // 2, 35)
    s += min(len(education or "") // 3, 25)
    s += min(len(experience or "") // 3, 30)
    if photo and photo != "noimage.png": s += 10
    return min(s, 100)

def get_rank(score):
    if score >= 90: return "Excellent"
    if score >= 70: return "Good"
    if score >= 50: return "Average"
    return "Poor"

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session: return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def next_version_number():
    conn = get_db()
    last = conn.execute("SELECT MAX(version_number) FROM resume_versions").fetchone()[0]
    conn.close()
    return 1 if last is None else last + 1

def clean_text(text):
    if not text: return ""
    for bad, good in {'\u2013':'-','\u2014':'-','\u2018':"'",'\u2019':"'",
                      '\u201c':'"','\u201d':'"','\u2022':'-','\u00a0':' ',
                      '\u2003':' ','\u2026':'...'}.items():
        text = text.replace(bad, good)
    text = ''.join(ch if 32 <= ord(ch) <= 126 else ' ' for ch in str(text))
    return re.sub(r' {2,}', ' ', text).strip()

# ── AUTH ──────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    if "user" in session: return redirect('/dashboard')
    return render_template("index.html", logged_out=False)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get("email","").strip()
        password = request.form.get("password","")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session["user"] = user["name"]
            session["user_email"] = user["email"]
            return redirect("/dashboard")
        return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html")

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip()
        password = request.form.get("password","")
        if not name or not email or not password:
            return render_template("register.html", error="All fields are required.")
        hashed = generate_password_hash(password)
        try:
            conn = get_db()
            conn.execute("INSERT INTO users (name,email,password) VALUES (?,?,?)", (name,email,hashed))
            conn.commit(); conn.close()
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Email already registered.")
        return redirect("/login")
    return render_template("register.html")

@app.route('/logout')
def logout():
    session.clear()
    return render_template("index.html", logged_out=True)   # ← lands on home, NOT /login

# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    total_users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_resumes = conn.execute("SELECT COUNT(*) FROM resume_versions").fetchone()[0]
    conn.close()
    return render_template("dashboard.html", total_users=total_users, total_resumes=total_resumes)

# ── RESUME BUILDER ────────────────────────────────────────────────────────────

@app.route('/resume', methods=['GET','POST'])
@login_required
def resume():
    if request.method == "POST":
        fields = {k: request.form.get(k,"") for k in
                  ["name","email","phone","linkedin","objective","skills",
                   "education","experience","location","template","jobtitle"]}
        photo_file = request.files.get("photo")
        filename = "noimage.png"
        if photo_file and photo_file.filename:
            filename = f"{int(time.time())}_{photo_file.filename}"
            photo_file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        score   = calculate_score(fields["skills"], fields["education"], fields["experience"], filename)
        version = next_version_number()
        conn = get_db()
        conn.execute("""INSERT INTO resume_versions
            (user_email,name,email,phone,linkedin,location,objective,skills,
             education,experience,photo,template,version_number,score,label,is_autosave)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (session.get("user_email"), fields["name"], fields["email"], fields["phone"],
             fields["linkedin"], fields["location"], fields["objective"], fields["skills"],
             fields["education"], fields["experience"], filename, fields["template"],
             version, score, f"{fields['name']}'s Resume" if fields["name"] else "Draft"))
        conn.commit(); conn.close()
        return redirect("/versions")
    return render_template("resume.html")

# ── AUTOSAVE ──────────────────────────────────────────────────────────────────

@app.route('/autosave', methods=['POST'])
@login_required
def autosave():
    data = request.get_json(silent=True) or {}
    name = data.get("name","")
    score   = calculate_score(data.get("skills",""), data.get("education",""), data.get("experience",""), None)
    version = next_version_number()
    conn = get_db()
    conn.execute("""INSERT INTO resume_versions
        (user_email,name,email,phone,linkedin,location,objective,skills,
         education,experience,photo,template,version_number,score,label,is_autosave)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (session.get("user_email"), name, data.get("email",""), data.get("phone",""),
         data.get("linkedin",""), data.get("location",""), data.get("objective",""),
         data.get("skills",""), data.get("education",""), data.get("experience",""),
         "noimage.png", data.get("template","AI Modern"), version, score,
         data.get("label", f"{name}'s Resume" if name else "Auto-save")))
    conn.commit(); conn.close()
    return jsonify({"status":"saved","version":version,"score":score})

# ── VERSIONS ──────────────────────────────────────────────────────────────────

@app.route('/versions')
@login_required
def versions():
    conn = get_db()
    resumes = conn.execute("SELECT * FROM resume_versions ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("versions.html", resume_data=[(r, get_rank(r["score"])) for r in resumes])

# ── PREVIEW ───────────────────────────────────────────────────────────────────

@app.route('/preview/<int:id>')
@login_required
def preview(id):
    conn = get_db()
    r = conn.execute("SELECT * FROM resume_versions WHERE id=?", (id,)).fetchone()
    conn.close()
    if not r: return redirect("/versions")
    r = dict(r)
    return render_template("preview.html", resume=r, rank=get_rank(r["score"]))

# ── DELETE ────────────────────────────────────────────────────────────────────

@app.route('/delete/<int:id>')
@login_required
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM resume_versions WHERE id=?", (id,))
    conn.commit(); conn.close()
    return redirect("/versions")

# ── SETTINGS ──────────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    if request.method == 'POST':
        session['settings'] = request.get_json(silent=True) or {}
        return jsonify({"status":"saved"})
    return render_template("settings.html")

@app.route('/delete-all-versions', methods=['POST'])
@login_required
def delete_all_versions():
    conn = get_db()
    conn.execute("DELETE FROM resume_versions WHERE user_email=?", (session.get("user_email"),))
    conn.commit(); conn.close()
    return jsonify({"status":"deleted"})

@app.route('/delete-account')
@login_required
def delete_account():
    email = session.get("user_email")
    conn  = get_db()
    conn.execute("DELETE FROM resume_versions WHERE user_email=?", (email,))
    conn.execute("DELETE FROM users WHERE email=?", (email,))
    conn.commit(); conn.close()
    session.clear()
    return redirect("/")

# ── PDF GENERATION ────────────────────────────────────────────────────────────

C_DARK  = HexColor("#1a1410")
C_GOLD  = HexColor("#c9a84c")
C_CREAM = HexColor("#f5f0e8")
C_MUTED = HexColor("#9a8f82")
C_RULE  = HexColor("#e8e0d0")

def draw_wrapped(c, text, x, y, max_width, font_name, font_size, line_height, color=None):
    if color: c.setFillColor(color)
    c.setFont(font_name, font_size)
    words, line = (text or "").split(), ""
    for word in words:
        test = (line + " " + word).strip()
        if c.stringWidth(test, font_name, font_size) <= max_width:
            line = test
        else:
            if line: c.drawString(x, y, line); y -= line_height
            line = word
    if line: c.drawString(x, y, line); y -= line_height
    return y

def section_heading(c, title, x, y, width, text_color, rule_color):
    c.setFont("Helvetica-Bold", 8); c.setFillColor(text_color)
    c.drawString(x, y, title.upper()); y -= 5
    c.setStrokeColor(rule_color); c.setLineWidth(0.5)
    c.line(x, y, x + width, y)
    return y - 10

@app.route('/pdf/<int:id>')
@login_required
def pdf(id):
    conn = get_db()
    r    = conn.execute("SELECT * FROM resume_versions WHERE id=?", (id,)).fetchone()
    conn.close()
    if not r: return redirect("/versions")
    r = dict(r)

    buf = BytesIO()
    c   = canvas.Canvas(buf)

    PW, PH = 595, 842
    SBW    = 195
    MAR    = 24
    CX     = SBW + 28
    CW     = PW - SBW - 28 - MAR

    # Sidebar bg + gold stripe
    c.setFillColor(C_DARK);  c.rect(0, 0, SBW, PH, fill=1, stroke=0)
    c.setFillColor(C_GOLD);  c.rect(SBW, 0, 3, PH, fill=1, stroke=0)

    # Photo
    ps = 90
    px = (SBW - ps) // 2
    py = PH - 30 - ps
    if r["photo"] and r["photo"] != "noimage.png":
        img_path = os.path.join(app.config["UPLOAD_FOLDER"], r["photo"])
        if os.path.exists(img_path):
            c.saveState()
            p  = c.beginPath()
            p.circle(px + ps/2, py + ps/2, ps/2)
            c.clipPath(p, stroke=0)
            c.drawImage(img_path, px, py, width=ps, height=ps,
                        preserveAspectRatio=True, mask='auto')
            c.restoreState()
            c.setStrokeColor(C_GOLD); c.setLineWidth(2)
            c.circle(px + ps/2, py + ps/2, ps/2, fill=0, stroke=1)
    else:
        c.setFillColor(HexColor("#2a2520")); c.setStrokeColor(C_GOLD); c.setLineWidth(1.5)
        c.circle(px + ps/2, py + ps/2, ps/2, fill=1, stroke=1)

    # Sidebar name
    sy = py - 16
    nl = clean_text(r["name"] or "")
    c.setFillColor(C_CREAM); c.setFont("Helvetica-Bold", 13)
    c.drawString((SBW - c.stringWidth(nl, "Helvetica-Bold", 13)) / 2, sy, nl); sy -= 14

    jt = clean_text(r.get("jobtitle") or "")
    if jt:
        c.setFillColor(C_GOLD); c.setFont("Helvetica", 8)
        c.drawString((SBW - c.stringWidth(jt.upper(), "Helvetica", 8)) / 2, sy, jt.upper()); sy -= 10

    c.setStrokeColor(C_GOLD); c.setLineWidth(0.5)
    c.line(MAR, sy, SBW - MAR, sy); sy -= 12

    # Contact
    c.setFillColor(C_GOLD); c.setFont("Helvetica-Bold", 7)
    c.drawString(MAR, sy, "CONTACT"); sy -= 10
    c.setStrokeColor(C_GOLD); c.setLineWidth(0.4)
    c.line(MAR, sy, SBW - MAR, sy); sy -= 10

    for icon, val in [("-", r["email"]), ("T", r["phone"]),
                      ("L", r["location"]), ("in", r["linkedin"])]:
        val = clean_text(val or "")
        if val:
            c.setFillColor(C_GOLD); c.setFont("Helvetica-Bold", 8)
            c.drawString(MAR, sy, icon)
            sy = draw_wrapped(c, val, MAR+14, sy, SBW-MAR-20, "Helvetica", 8, 11, C_MUTED)
            sy -= 2
    sy -= 6

    # Summary
    c.setFillColor(C_GOLD); c.setFont("Helvetica-Bold", 7)
    c.drawString(MAR, sy, "SUMMARY"); sy -= 10
    c.setStrokeColor(C_GOLD); c.setLineWidth(0.4)
    c.line(MAR, sy, SBW - MAR, sy); sy -= 10
    draw_wrapped(c, clean_text(r["objective"]), MAR, sy, SBW-MAR*2, "Helvetica", 8, 12, C_MUTED)

    # ── Score badge intentionally removed ──

    # Right column
    ry = PH - MAR
    c.setFillColor(C_DARK); c.setFont("Helvetica-Bold", 26)
    c.drawString(CX, ry - 26, r["name"] or ""); ry -= 30
    if jt:
        c.setFillColor(C_GOLD); c.setFont("Helvetica", 9)
        c.drawString(CX, ry - 4, jt.upper()); ry -= 10
    ry -= 4
    c.setStrokeColor(C_DARK); c.setLineWidth(1.5)
    c.line(CX, ry, PW - MAR, ry); ry -= 14

    def right_sec(title, content, is_skills=False):
        nonlocal ry
        ry = section_heading(c, title, CX, ry, CW, C_MUTED, C_RULE)
        if is_skills:
            chips    = [clean_text(s).strip() for s in re.split(r'[,;|\n]+', content or "") if clean_text(s).strip()]
            xc       = CX
            ch, cp   = 14, 6
            for sk in chips:
                c.setFont("Helvetica", 8)
                sw = c.stringWidth(sk, "Helvetica", 8) + cp * 2
                if xc + sw > PW - MAR: xc = CX; ry -= ch + 4
                c.setFillColor(HexColor("#f5f0e8")); c.setStrokeColor(HexColor("#d4c89a")); c.setLineWidth(0.5)
                c.roundRect(xc, ry - ch + 2, sw, ch, 3, fill=1, stroke=1)
                c.setFillColor(HexColor("#4a3c20"))
                c.drawString(xc + cp, ry - ch + 6, sk)
                xc += sw + 5
            ry -= ch + 12
        else:
            ry = draw_wrapped(c, clean_text(content), CX, ry, CW, "Helvetica", 9, 13, C_DARK)
            ry -= 10

    right_sec("Professional Summary", r["objective"])
    right_sec("Core Skills",          r["skills"], is_skills=True)
    right_sec("Work Experience",      r["experience"])
    right_sec("Education",            r["education"])

    c.save()
    pdf_bytes = buf.getvalue(); buf.close()
    safe = clean_text(r["name"] or "resume")
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"]        = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe}_v{r["version_number"]}.pdf"'
    return resp

if __name__ == "__main__":
    app.run(debug=True)