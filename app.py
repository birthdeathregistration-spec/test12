# app.py  –  Civic-Tech Verification Demo
import io, time, secrets, sqlite3, threading
import numpy as np
import face_recognition
import qrcode
from PIL import Image
from pyzbar.pyzbar import decode
from flask import (Flask, request, render_template, redirect,
                   url_for, session, flash, send_file)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

DB               = "users.db"
FACE_TOLERANCE   = 0.5
OTP_TTL          = 180
OTP_MAX_ATTEMPTS = 5

# ── FIX 1: per-thread SQLite connection ──────────────────────────────────────
_local = threading.local()

def get_db():
    if not hasattr(_local, "con"):
        _local.con = sqlite3.connect(DB, check_same_thread=False)
        _local.con.row_factory = sqlite3.Row
    return _local.con

def init_db():
    con = sqlite3.connect(DB)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            email         TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            face_encoding BLOB,
            qr_token      TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     REAL    NOT NULL,
            event  TEXT    NOT NULL,
            email  TEXT    NOT NULL,
            ip     TEXT,
            detail TEXT
        );
    """)
    con.commit()
    con.close()

def audit(event, email, detail=""):
    ip = request.remote_addr if request else "system"
    db = get_db()
    db.execute("INSERT INTO audit_log(ts,event,email,ip,detail) VALUES(?,?,?,?,?)",
               (time.time(), event, email, ip, detail))
    db.commit()

# ── FIX 2: numpy writable copy ───────────────────────────────────────────────
def encoding_from_image(file_storage):
    try:
        raw   = face_recognition.load_image_file(file_storage)
        image = np.array(raw, dtype=np.uint8)
        encs  = face_recognition.face_encodings(image)
        return encs[0] if encs else None
    except Exception as e:
        print(f"[encoding_from_image] {e}")
        return None

def load_ref_encoding(email):
    row = get_db().execute(
        "SELECT face_encoding FROM users WHERE email=?", (email,)).fetchone()
    if not row or not row["face_encoding"]:
        return None
    return np.frombuffer(row["face_encoding"], dtype=np.float64).copy()

# ── FIX 3: token-based QR validation ─────────────────────────────────────────
def validate_qr(file_storage, email):
    try:
        img     = Image.open(file_storage)
        decoded = decode(img)
        if not decoded:
            return False
        scanned = decoded[0].data.decode("utf-8").strip()
        row = get_db().execute(
            "SELECT qr_token FROM users WHERE email=?", (email,)).fetchone()
        if not row or not row["qr_token"]:
            return False
        return secrets.compare_digest(scanned, row["qr_token"])
    except Exception as e:
        print(f"[validate_qr] {e}")
        return False

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name  = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        photo = request.files.get("photo")
        enc   = encoding_from_image(photo) if photo else None
        if enc is None:
            flash("ছবিতে স্পষ্ট মুখ পাওয়া যায়নি। আবার চেষ্টা করুন।", "error")
            return redirect(url_for("signup"))
        qr_token = secrets.token_urlsafe(32)
        get_db().execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?)",
                         (email, name, enc.tobytes(), qr_token))
        get_db().commit()
        audit("SIGNUP", email, f"name={name}")
        flash("Signup সফল! নিচে আপনার QR কোড দেওয়া হয়েছে।", "success")
        return redirect(url_for("show_qr", email=email))
    return render_template("signup.html")

@app.route("/qr/<email>")
def show_qr(email):
    email = email.lower()
    row   = get_db().execute(
        "SELECT name FROM users WHERE email=?", (email,)).fetchone()
    if not row:
        flash("ইউজার পাওয়া যায়নি।", "error")
        return redirect(url_for("signup"))
    return render_template("show_qr.html", email=email, name=row["name"])

@app.route("/qr-image/<email>")
def qr_image(email):
    email = email.lower()
    row   = get_db().execute(
        "SELECT qr_token FROM users WHERE email=?", (email,)).fetchone()
    if not row:
        return "Not found", 404
    img = qrcode.make(row["qr_token"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

@app.route("/verify/<email>", methods=["GET", "POST"])
def verify(email):
    email = email.lower()
    if request.method == "POST":
        qr_file = request.files.get("qr_code")
        photo   = request.files.get("photo")
        if qr_file and qr_file.filename:
            if validate_qr(qr_file, email):
                session.clear()
                session["verified_email"] = email
                session["method"]         = "QR"
                audit("QR_OK", email)
                flash("QR কোড সফলভাবে যাচাই হয়েছে!", "success")
                return redirect(url_for("success"))
            audit("QR_FAIL", email, "token_mismatch")
            flash("QR কোড মেলেনি। এই email-এর QR ব্যবহার করুন।", "error")
            return redirect(url_for("verify", email=email))
        new_enc = encoding_from_image(photo) if photo else None
        if new_enc is None:
            audit("FACE_FAIL", email, "no_face_detected")
            flash("মুখ শনাক্ত হয়নি। স্পষ্ট ছবি দিন অথবা QR / OTP ব্যবহার করুন।", "error")
            return redirect(url_for("verify", email=email))
        ref_enc = load_ref_encoding(email)
        if ref_enc is None:
            flash("ইউজার পাওয়া যায়নি।", "error")
            return redirect(url_for("signup"))
        distance = face_recognition.face_distance([ref_enc], new_enc)[0]
        if distance <= FACE_TOLERANCE:
            session.clear()
            session["verified_email"] = email
            session["method"]         = "FACE"
            audit("FACE_OK", email, f"distance={distance:.4f}")
            return redirect(url_for("success"))
        audit("FACE_FAIL", email, f"distance={distance:.4f}")
        flash(f"মুখ মিলেনি (দূরত্ব {distance:.2f})। QR বা OTP দিয়ে চেষ্টা করুন।", "error")
        return redirect(url_for("verify", email=email))
    return render_template("verify.html", email=email)

@app.route("/otp/<email>", methods=["GET", "POST"])
def otp(email):
    email = email.lower()
    if request.method == "POST":
        entered = request.form.get("otp", "").strip()
        data    = session.get("otp_data")
        if not data or time.time() > data["expires"]:
            audit("OTP_FAIL", email, "expired")
            flash("OTP-এর মেয়াদ শেষ। নতুন কোড নিন।", "error")
            return redirect(url_for("otp", email=email))
        if data["attempts"] >= OTP_MAX_ATTEMPTS:
            audit("OTP_FAIL", email, f"max_attempts={OTP_MAX_ATTEMPTS}")
            session.pop("otp_data", None)
            flash("অনেকবার ভুল হয়েছে। নতুন কোড নিন।", "error")
            return redirect(url_for("otp", email=email))
        if secrets.compare_digest(entered, data["code"]):
            session.pop("otp_data", None)
            session["verified_email"] = email
            session["method"]         = "OTP"
            audit("OTP_OK", email)
            return redirect(url_for("success"))
        data["attempts"] += 1
        session["otp_data"] = data
        remaining = OTP_MAX_ATTEMPTS - data["attempts"]
        audit("OTP_FAIL", email, f"wrong_code attempt={data['attempts']}")
        flash(f"ভুল OTP। বাকি চেষ্টা: {remaining}", "error")
        return redirect(url_for("otp", email=email))
    code = f"{secrets.randbelow(1_000_000):06d}"
    session["otp_data"] = {"code": code, "expires": time.time() + OTP_TTL, "attempts": 0}
    print(f"[OTP] {email} → {code}")
    flash("OTP পাঠানো হয়েছে (ডেমোতে server console দেখুন)।", "info")
    return render_template("otp.html", email=email)

@app.route("/success")
def success():
    if not session.get("verified_email"):
        return redirect(url_for("signup"))
    return render_template("success.html",
                           email=session["verified_email"],
                           method=session.get("method", "Unknown"))

@app.route("/audit")
def audit_view():
    rows = get_db().execute(
        "SELECT ts,event,email,ip,detail FROM audit_log ORDER BY ts DESC LIMIT 200"
    ).fetchall()
    html = ("<h2 style=\'font-family:sans-serif\'>Audit Log</h2>"
            "<table border=\'1\' cellpadding=\'8\' style=\'border-collapse:collapse;font-family:monospace\'>"
            "<tr><th>সময়</th><th>ঘটনা</th><th>ইমেইল</th><th>IP</th><th>বিস্তারিত</th></tr>")
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"]))
        html += (f"<tr><td>{t}</td><td><b>{r['event']}</b></td><td>{r['email']}</td>"
                 f"<td>{r['ip']}</td><td>{r['detail']}</td></tr>")
    return html + "</table>"

if __name__ == "__main__":
    init_db()
    app.run(debug=True, threaded=True)
