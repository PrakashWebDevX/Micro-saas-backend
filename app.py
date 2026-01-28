# app.py (Flask Version)

import os
import time
import requests
import smtplib
from email.mime.text import MIMEText
from typing import List
from flask_cors import CORS
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# ---------------------------
# Load environment variables
# ---------------------------
load_dotenv()

WHOISXML_API_KEY = os.getenv("WHOISXML_API_KEY")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_EMAIL)
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///domains.db")

if not WHOISXML_API_KEY:
    raise RuntimeError("WHOISXML_API_KEY missing in .env")

# ---------------------------
# Flask App + Database
# ---------------------------
app = Flask(__name__)

# Replace your current CORS(...) call with this:

CORS(
    app,
    resources={r"/*": {
        "origins": [
            "https://preview--brand-name-pilot.lovable.app",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://10.173.63.66:8000",
            "http://127.0.0.1:5173"
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"],
        # If your fetch uses credentials (cookies/Authorization), set this True
        "supports_credentials": False,
        "max_age": 86400
    }}
)


app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
CORS(app, origins=["https://id-preview--b652694e-c835-4745-a283-068f601b5bb3.lovable.app"])

db = SQLAlchemy(app)

# ---------------------------
# Database Model
# ---------------------------
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    notified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.Float, default=time.time)

with app.app_context():
    db.create_all()

# ---------------------------
# WHOIS API Check
# ---------------------------
def whoisxml_check(domain: str) -> bool:
    url = "https://domain-availability.whoisxmlapi.com/api/v1"
    params = {
        "apiKey": WHOISXML_API_KEY,
        "domainName": domain
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        # Handles multiple response styles
        if "DomainInfo" in data:
            status = data["DomainInfo"].get("domainAvailability")
        else:
            status = data.get("domainAvailability")

        if status:
            return status.upper() == "AVAILABLE"

    except Exception as e:
        raise RuntimeError(f"WHOIS API error: {e}")

    return False


# ---------------------------
# Domain Suggestions
# ---------------------------
COMMON_TLDS = [".com", ".net", ".org", ".io", ".co", ".ai", ".xyz", ".tech", ".dev", ".app", ".tv", ".me", ".link", ".shop"]

def generate_suggestions(query: str, max_suggestions: int = 6) -> List[str]:
    query = query.lower()
    label = query.split(".")[0] if "." in query else query

    candidates = []

    # TLD variations
    for tld in COMMON_TLDS:
        candidates.append(f"{label}{tld}")

    # Prefixes
    prefixes = ["get", "try", "the", "my", "use", "go", "hey", "join", "is", "do", "pro", "ultra"]
    for p in prefixes:
        candidates.append(f"{p}{label}.com")

    # Suffixes
    suffixes = ["app", "hq", "space", "online", "site", "hub", "labs", "io", "dev", "pro", "tv", "cloud"]
    for s in suffixes:
        candidates.append(f"{label}{s}.com")

    # Double suffixes
    double_suffixes = ["hub.io", "labs.io", "dev.io", "app.io", "online.io"]
    for ds in double_suffixes:
        candidates.append(f"{label}.{ds}")

    # Character variations (add numbers/dashes)
    variations = [f"{label}app.com", f"{label}pro.com", f"{label}online.com", f"{label}ai.com"]
    for var in variations:
        candidates.append(var)

    # Remove duplicates and return
    return list(dict.fromkeys(candidates))[:max_suggestions * 3]


# ---------------------------
# Send Email (SMTP)
# ---------------------------
def send_email(to_email: str, subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
        smtp.sendmail(FROM_EMAIL, [to_email], msg.as_string())


# ---------------------------
# API Routes (Flask)
# ---------------------------

@app.route("/")
def home():
    return {"service": "Domain Suggester SaaS - Flask", "status": "running"}

@app.route("/check", methods=["POST"])
def check_domain():
    data = request.json
    query = data.get("query", "").strip().lower()
    max_suggestions = int(data.get("max_suggestions", 6))

    if "." in query:
        domain = query
    else:
        domain = f"{query}.com"

    try:
        available = whoisxml_check(domain)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Generate suggestions ALWAYS (remove the "if not available:" condition)
    suggestions = []
    for cand in generate_suggestions(query, max_suggestions):
        try:
            if whoisxml_check(cand):
                suggestions.append(cand)
            if len(suggestions) >= max_suggestions:
                break
        except Exception:
            continue

    return jsonify({
        "domain": domain,
        "available": available,
        "suggestions": suggestions
    })



@app.route("/notify", methods=["POST"])
def notify():
    data = request.json
    domain = data.get("domain", "").strip().lower()
    email = data.get("email", "").strip().lower()

    if "." not in domain:
        domain = f"{domain}.com"

    exists = Notification.query.filter_by(
        domain=domain, email=email, notified=False
    ).first()

    if exists:
        return jsonify({"message": "Already registered for this domain"}), 200

    record = Notification(domain=domain, email=email)
    db.session.add(record)
    db.session.commit()

    return jsonify({"message": "Notification registered"}), 201


# ---------------------------
# Background Checker
# ---------------------------
def process_notifications():
    with app.app_context():  # <-- add this
        pending = Notification.query.filter_by(notified=False).all()
        for note in pending:
            try:
                if whoisxml_check(note.domain):
                    try:
                        send_email(
                            note.email,
                            f"Domain Available: {note.domain}",
                            f"Good news! The domain {note.domain} is now available."
                        )
                        note.notified = True
                        db.session.commit()
                        print(f"[Scheduler] Email sent → {note.email}")
                    except Exception as e:
                        print(f"Email send failed: {e}")
            except Exception as e:
                print(f"WHOIS failed: {e}")


# Start scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(process_notifications, "interval", seconds=CHECK_INTERVAL_SECONDS)
scheduler.start()

# ---------------------------
# Run Flask App
# ---------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
