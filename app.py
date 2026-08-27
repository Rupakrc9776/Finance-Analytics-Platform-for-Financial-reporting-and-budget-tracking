from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify, abort, g
import sqlite3
import os
import csv
import json
import re
import secrets
import uuid
import tempfile
import logging
import smtplib
from pathlib import Path
from datetime import datetime, timedelta
from services.time_utils import format_ist, ist_now, utc_now
from dotenv import load_dotenv
from flask import send_file
from flask_mail import Mail, Message
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from io import StringIO, BytesIO
from flask import make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from reportlab.lib.units import inch
from reportlab.platypus import Spacer
from collections import defaultdict
from functools import wraps
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from services.gemini_service import get_gemini_client

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")

app.jinja_env.filters["ist_datetime"] = format_ist
app.permanent_session_lifetime = timedelta(minutes=30)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER", "")
app.config["MAIL_DEBUG_LOG_OTP"] = os.getenv("MAIL_DEBUG_LOG_OTP", "false").lower() == "true"

mail = Mail(app)

from routes.jarvis import jarvis_bp

app.register_blueprint(jarvis_bp)

BASE_DIR = Path(__file__).resolve().parent
DATABASE = os.getenv("DATABASE_PATH", str(BASE_DIR / "finance.db"))
# ---------------- REGISTER ---------------- #

@app.route("/register", methods=["GET", "POST"])

def register():

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/register")

        name = request.form["name"]
        email = request.form["email"]
        phone = request.form["phone"]
        income = request.form["income"]
        password = request.form["password"]
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect("/register")

        if not password_is_strong(password):
            flash("Password must be at least 8 characters and include uppercase, lowercase, number, and symbol.", "danger")
            return redirect("/register")

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        user = cur.fetchone()

        if user:
            conn.close()
            flash("Email already exists!")
            return redirect("/register")

        cur.execute("""
        INSERT INTO users(
            name,
            email,
            phone,
            income,
            password
        )
        VALUES(?,?,?,?,?)
        """, (
            name,
            email,
            phone,
            income,
            hash_password(password)
        ))

        conn.commit()
        conn.close()

        send_welcome_email(name, email)

        flash("Registration Successful")
        return redirect("/login")

    return render_template("register.html")

PROFILE_UPLOAD_FOLDER = BASE_DIR / "static" / "uploads" / "profile"
PROFILE_UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ALLOWED_PROFILE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_PROFILE_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
SESSION_TIMEOUT_SECONDS = 30 * 60
DEFAULT_DEVELOPER_EMAIL = "chatterjeerupak6@gmail.com"
DEVELOPER_EMAIL_ENV_KEYS = ("DEVELOPER_ADMIN_EMAIL", "DEVELOPER_EMAIL")


def login_required():

    return "user_id" in session


def get_configured_developer_email():

    for env_key in DEVELOPER_EMAIL_ENV_KEYS:
        raw_value = os.getenv(env_key, "")
        if not raw_value:
            continue

        for item in re.split(r"[,;\s]+", raw_value):
            email = item.strip().lower()
            if email and "@" in email:
                return email

    return DEFAULT_DEVELOPER_EMAIL


def get_configured_developer_emails():

    return {get_configured_developer_email()}


def is_suspended_user(user):

    if not user or "is_suspended" not in user.keys():
        return False

    return bool(int(user["is_suspended"] or 0))


def is_developer_user(user):

    if not user:
        return False

    email = str(user["email"] or "").strip().lower() if "email" in user.keys() else ""
    return email == get_configured_developer_email()


# ---------------- DATABASE CONNECTION ---------------- #

def get_db():

    conn = sqlite3.connect(DATABASE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def safe_float(value, default=0.0):

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_datetime_value(raw_value):

    if not raw_value:
        return None

    normalized = str(raw_value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized[:26], fmt)
        except ValueError:
            continue
    return None


def format_time_ago(raw_value):

    dt = parse_datetime_value(raw_value)
    if not dt:
        return "just now"

    now = utc_now()
    delta = now - dt
    seconds = int(max(delta.total_seconds(), 0))

    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} min ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hr ago"
    if seconds < 604800:
        days = seconds // 86400
        return f"{days} day ago" if days == 1 else f"{days} days ago"
    return dt.strftime("%d %b %Y")


def log_activity(cur, user_id, activity_type, title, description, icon="fa-solid fa-circle-info", related_route=None):

    cur.execute("""
        INSERT INTO activity_logs(user_id, activity_type, title, description, icon, related_route)
        VALUES(?,?,?,?,?,?)
    """, (user_id, activity_type, title, description, icon, related_route))


def parse_dashboard_preferences(hidden_cards_raw, card_order_raw):

    hidden_cards = []
    if hidden_cards_raw:
        hidden_cards = [item.strip() for item in str(hidden_cards_raw).split(",") if item.strip()]

    card_order = []
    if card_order_raw:
        card_order = [item.strip() for item in str(card_order_raw).split(",") if item.strip()]

    return hidden_cards, card_order


def ensure_column(cur, table_name, column_name, column_definition):

    cur.execute(f"PRAGMA table_info({table_name})")
    existing_columns = [row[1] for row in cur.fetchall()]

    if column_name not in existing_columns:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def is_hashed_password(password_value):

    return isinstance(password_value, str) and password_value.startswith(("scrypt:", "pbkdf2:", "argon2:"))


def verify_password(stored_password, provided_password):

    if is_hashed_password(stored_password):
        try:
            return check_password_hash(stored_password, provided_password)
        except ValueError:
            return False

    return stored_password == provided_password


def hash_password(password_value):

    return generate_password_hash(password_value)


def generate_csrf_token():

    token = session.get("csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token

    return token


def validate_csrf_token():

    return request.form.get("csrf_token") == session.get("csrf_token")


def password_is_strong(password_value):

    if len(password_value) < 8:
        return False

    return bool(
        re.search(r"[A-Z]", password_value)
        and re.search(r"[a-z]", password_value)
        and re.search(r"[0-9]", password_value)
        and re.search(r"[^A-Za-z0-9]", password_value)
    )


def allowed_profile_image(filename):

    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_PROFILE_EXTENSIONS


def normalize_profile_image_db_value(raw_value):

    if not raw_value:
        return None

    cleaned = str(raw_value).strip().replace("\\", "/")
    if not cleaned:
        return None

    lowered = cleaned.lower()
    if lowered.endswith("images/avatar.png") or "/images/avatar.png" in lowered:
        return None

    filename = Path(cleaned).name
    if not filename:
        return None

    if not allowed_profile_image(filename):
        return None

    return f"uploads/profile/{filename}"


def profile_image_storage_path(raw_value):

    normalized = normalize_profile_image_db_value(raw_value)
    if not normalized:
        return None

    filename = Path(normalized).name
    return PROFILE_UPLOAD_FOLDER / filename


def get_user_agent_summary():

    browser = getattr(request.user_agent, "browser", None) or "Unknown Browser"
    platform = getattr(request.user_agent, "platform", None) or "Unknown Device"

    if platform.lower() == "windows" or platform.lower() == "linux" or platform.lower() == "macos":
        device = f"Desktop ({platform})"
    else:
        device = platform

    return browser, device


def save_profile_upload(uploaded_file, user_id):

    if not uploaded_file or not uploaded_file.filename:
        return None, "No file was selected."

    if not allowed_profile_image(uploaded_file.filename):
        return None, "Only JPG, JPEG, PNG, and WEBP images are allowed."

    mime_type = (uploaded_file.mimetype or "").lower().strip()
    if mime_type and mime_type not in ALLOWED_PROFILE_MIME_TYPES:
        return None, "Only JPG, JPEG, PNG, and WEBP images are allowed."

    filename = secure_filename(uploaded_file.filename)
    suffix = Path(filename).suffix.lower()
    timestamp = ist_now().strftime("%Y%m%d%H%M%S%f")
    unique_name = f"profile_{int(user_id)}_{timestamp}{suffix}"
    target_path = PROFILE_UPLOAD_FOLDER / unique_name
    try:
        uploaded_file.save(target_path)
    except OSError:
        logger.exception("Profile upload save failed user_id=%s target=%s", user_id, target_path)
        return None, "Unable to save the uploaded image. Please try again."

    return f"uploads/profile/{unique_name}", None


def get_user_by_id(user_id):

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, email, phone, password, income, profile_image, bio, github_url, linkedin_url,
               email_verified, created_at, updated_at, is_developer, is_suspended,
               notify_email_notifications, notify_budget_alerts, notify_monthly_report,
             notify_goal_alerts, notify_investment_alerts, theme_mode
        FROM users
        WHERE id=?
    """, (user_id,))
    user = cur.fetchone()
    conn.close()
    return user


def get_user_profile_image(user):

    if user and user["profile_image"]:
        normalized = normalize_profile_image_db_value(user["profile_image"])
        target_path = profile_image_storage_path(normalized)
        if normalized and target_path and target_path.exists():
            return url_for("static", filename=normalized)

        return None


def prepare_user_for_template(user):

    if not user:
        return None

    user_data = dict(user)
    normalized = normalize_profile_image_db_value(user_data.get("profile_image"))
    target_path = profile_image_storage_path(normalized)
    user_data["profile_image"] = normalized if normalized and target_path and target_path.exists() else None
    return user_data


def admin_access_required():

    if not login_required():
        return None, redirect("/login")

    current_user = get_user_by_id(session["user_id"])
    if not current_user:
        session.clear()
        flash("Your session is no longer valid. Please log in again.", "warning")
        return None, redirect("/login")

    if is_suspended_user(current_user):
        session.clear()
        flash("Your account has been suspended. Please contact the developer.", "danger")
        return None, redirect("/login")

    if not is_developer_user(current_user):
        return None, abort(403)

    return current_user, None


def developer_required(view_func):

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        developer_user, access_response = admin_access_required()
        if access_response:
            return access_response

        g.developer_user = developer_user
        return view_func(*args, **kwargs)

    return wrapped


def format_bytes(num_bytes):

    size_value = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_value < 1024 or unit == "TB":
            return f"{size_value:.1f} {unit}"
        size_value /= 1024

    return "0 B"


def get_directory_usage(path_value):

    path_obj = Path(path_value)
    if not path_obj.exists():
        return {"bytes": 0, "files": 0, "formatted": "0.0 B"}

    total_bytes = 0
    file_count = 0
    for child in path_obj.rglob("*"):
        if child.is_file():
            file_count += 1
            try:
                total_bytes += child.stat().st_size
            except OSError:
                continue

    return {
        "bytes": total_bytes,
        "files": file_count,
        "formatted": format_bytes(total_bytes),
    }


def build_recent_day_series(rows, days=7, value_key="count"):

    today = ist_now().date()
    row_map = {
        str(row["bucket"]): int(row[value_key] or 0)
        for row in rows
    }

    labels = []
    values = []
    for offset in range(days - 1, -1, -1):
        bucket = (today - timedelta(days=offset)).isoformat()
        labels.append(bucket[5:])
        values.append(row_map.get(bucket, 0))

    return {"labels": labels, "values": values}


def build_recent_month_series(rows, months=6, value_key="count"):

    cursor = ist_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_keys = []
    for _ in range(months):
        month_keys.append(cursor.strftime("%Y-%m"))
        if cursor.month == 1:
            cursor = cursor.replace(year=cursor.year - 1, month=12)
        else:
            cursor = cursor.replace(month=cursor.month - 1)
    month_keys.reverse()

    row_map = {
        str(row["bucket"]): int(row[value_key] or 0)
        for row in rows
    }

    return {
        "labels": [datetime.strptime(bucket, "%Y-%m").strftime("%b %Y") for bucket in month_keys],
        "values": [row_map.get(bucket, 0) for bucket in month_keys],
    }


def get_admin_dashboard_metrics(cur):

    cur.execute("SELECT COUNT(*) AS count FROM users")
    total_users = int(cur.fetchone()["count"] or 0)

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM users
        WHERE (is_suspended IS NULL OR is_suspended=0)
          AND last_login_at IS NOT NULL
          AND datetime(substr(replace(last_login_at, 'T', ' '), 1, 19)) >= datetime('now', '-30 day')
    """)
    active_users = int(cur.fetchone()["count"] or 0)

    cur.execute("SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total_expenses FROM expenses")
    expense_summary = cur.fetchone()

    cur.execute("SELECT COALESCE(SUM(income), 0) AS total_income FROM users")
    income_summary = cur.fetchone()

    cur.execute("SELECT COUNT(*) AS count FROM goals")
    total_goals = int(cur.fetchone()["count"] or 0)

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM jarvis_history
        WHERE date(substr(replace(created_at, 'T', ' '), 1, 19)) = date('now')
    """)
    ai_requests_today = int(cur.fetchone()["count"] or 0)

    return {
        "total_users": total_users,
        "active_users": active_users,
        "total_transactions": int(expense_summary["count"] or 0),
        "total_expenses": float(expense_summary["total_expenses"] or 0),
        "total_income": float(income_summary["total_income"] or 0),
        "total_goals": total_goals,
        "ai_requests_today": ai_requests_today,
    }


def get_admin_chart_payload(cur):

    cur.execute("""
        SELECT date(substr(replace(created_at, 'T', ' '), 1, 19)) AS bucket, COUNT(*) AS count
        FROM users
        WHERE created_at IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket DESC
        LIMIT 7
    """)
    daily_registrations = build_recent_day_series(list(cur.fetchall()), days=7)

    cur.execute("""
        SELECT substr(replace(created_at, 'T', ' '), 1, 7) AS bucket, COUNT(*) AS count
        FROM users
        WHERE created_at IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket DESC
        LIMIT 6
    """)
    monthly_users = build_recent_month_series(list(cur.fetchall()), months=6)

    cur.execute("""
        SELECT COALESCE(category, 'Uncategorized') AS category, COALESCE(SUM(amount), 0) AS total_amount
        FROM expenses
        GROUP BY COALESCE(category, 'Uncategorized')
        ORDER BY total_amount DESC, category ASC
        LIMIT 6
    """)
    expense_rows = cur.fetchall()
    expense_analytics = {
        "labels": [row["category"] for row in expense_rows],
        "values": [round(float(row["total_amount"] or 0), 2) for row in expense_rows],
    }

    cur.execute("""
        SELECT date(substr(replace(created_at, 'T', ' '), 1, 19)) AS bucket, COUNT(*) AS count
        FROM jarvis_history
        WHERE created_at IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket DESC
        LIMIT 7
    """)
    ai_usage = build_recent_day_series(list(cur.fetchall()), days=7)

    return {
        "daily_registrations": daily_registrations,
        "monthly_users": monthly_users,
        "expense_analytics": expense_analytics,
        "ai_usage": ai_usage,
    }


def get_admin_activity_feed(cur, limit=14):

    cur.execute("""
        SELECT a.id, a.activity_type, a.title, a.description, a.icon, a.related_route, a.created_at,
               u.name AS user_name, u.email AS user_email
        FROM activity_logs a
        JOIN users u ON u.id = a.user_id
        ORDER BY datetime(substr(replace(a.created_at, 'T', ' '), 1, 19)) DESC, a.id DESC
        LIMIT ?
    """, (limit,))

    rows = []
    for row in cur.fetchall():
        rows.append({
            "id": row["id"],
            "activity_type": row["activity_type"],
            "title": row["title"],
            "description": row["description"],
            "icon": row["icon"] or "fa-solid fa-circle-info",
            "related_route": row["related_route"],
            "created_at": row["created_at"],
            "time_ago": format_time_ago(row["created_at"]),
            "user_name": row["user_name"],
            "user_email": row["user_email"],
        })

    return rows


def get_admin_notifications(cur, limit=10):

    cur.execute("""
        SELECT n.id, n.title, n.message, n.priority, n.created_at, n.is_read,
               u.name AS user_name, u.email AS user_email
        FROM notifications n
        JOIN users u ON u.id = n.user_id
        ORDER BY datetime(substr(replace(n.created_at, 'T', ' '), 1, 19)) DESC, n.id DESC
        LIMIT ?
    """, (limit,))

    return [dict(row) for row in cur.fetchall()]


def get_admin_logs(cur, limit=12):

    cur.execute("""
        SELECT a.id, a.activity_type, a.title, a.created_at, a.description,
               u.email AS user_email
        FROM activity_logs a
        JOIN users u ON u.id = a.user_id
        ORDER BY datetime(substr(replace(a.created_at, 'T', ' '), 1, 19)) DESC, a.id DESC
        LIMIT ?
    """, (limit,))

    return [dict(row) for row in cur.fetchall()]


def get_admin_feedback_summary(cur, recent_limit=10):

    cur.execute("SELECT COUNT(*) AS count, COALESCE(AVG(rating), 0) AS avg_rating FROM feedback")
    overview = cur.fetchone()
    total_feedback = int(overview["count"] or 0)
    average_rating = round(float(overview["avg_rating"] or 0), 2)

    cur.execute("""
        SELECT rating, COUNT(*) AS count
        FROM feedback
        GROUP BY rating
    """)
    distribution_map = {int(row["rating"]): int(row["count"] or 0) for row in cur.fetchall()}
    distribution = {
        5: distribution_map.get(5, 0),
        4: distribution_map.get(4, 0),
        3: distribution_map.get(3, 0),
        2: distribution_map.get(2, 0),
        1: distribution_map.get(1, 0),
    }

    cur.execute("""
        SELECT f.id, f.rating, f.comment, f.created_at, u.name AS user_name, u.email AS user_email
        FROM feedback f
        JOIN users u ON u.id = f.user_id
        ORDER BY datetime(substr(replace(f.created_at, 'T', ' '), 1, 19)) DESC, f.id DESC
        LIMIT ?
    """, (recent_limit,))
    recent_feedback = [dict(row) for row in cur.fetchall()]

    return {
        "total_feedback": total_feedback,
        "average_rating": average_rating,
        "distribution": distribution,
        "recent_feedback": recent_feedback,
    }


def get_admin_user_rows(cur, search_term=""):

    params = []
    where_clause = ""
    normalized_search = str(search_term or "").strip().lower()
    if normalized_search:
        where_clause = "WHERE lower(u.name) LIKE ? OR lower(u.email) LIKE ?"
        like_value = f"%{normalized_search}%"
        params.extend([like_value, like_value])

    cur.execute(f"""
        SELECT u.id, u.name, u.email, u.phone, u.income, u.email_verified, u.created_at, u.updated_at,
               u.last_login_at, u.current_browser, u.current_device, u.is_developer, u.is_suspended,
               (SELECT COUNT(*) FROM expenses e WHERE e.user_id = u.id) AS transaction_count,
               (SELECT COALESCE(SUM(amount), 0) FROM expenses e WHERE e.user_id = u.id) AS total_expense,
               (SELECT COUNT(*) FROM goals g WHERE g.user_id = u.id) AS goals_count,
               (SELECT COUNT(*) FROM jarvis_history j WHERE j.user_id = u.id) AS ai_requests
        FROM users u
        {where_clause}
        ORDER BY datetime(substr(replace(u.created_at, 'T', ' '), 1, 19)) DESC, u.id DESC
    """, params)

    return [dict(row) for row in cur.fetchall()]


def get_admin_user_detail(cur, user_id):

    cur.execute("""
        SELECT u.*, 
               (SELECT COUNT(*) FROM expenses e WHERE e.user_id = u.id) AS transaction_count,
               (SELECT COALESCE(SUM(amount), 0) FROM expenses e WHERE e.user_id = u.id) AS total_expense,
               (SELECT COUNT(*) FROM goals g WHERE g.user_id = u.id) AS goals_count,
               (SELECT COUNT(*) FROM notifications n WHERE n.user_id = u.id) AS notifications_count,
               (SELECT COUNT(*) FROM jarvis_history j WHERE j.user_id = u.id) AS ai_requests
        FROM users u
        WHERE u.id=?
    """, (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def delete_user_related_records(cur, user_id):

    user_row = get_admin_user_detail(cur, user_id)
    if user_row:
        existing_profile_file = profile_image_storage_path(user_row.get("profile_image"))
        if existing_profile_file and existing_profile_file.exists():
            try:
                existing_profile_file.unlink()
            except OSError:
                logger.warning("Could not remove profile image during admin delete user_id=%s", user_id)

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    table_names = [row["name"] for row in cur.fetchall() if row["name"] != "users"]

    for table_name in table_names:
        cur.execute(f'PRAGMA table_info("{table_name}")')
        columns = [row[1] for row in cur.fetchall()]
        if "user_id" in columns:
            cur.execute(f'DELETE FROM "{table_name}" WHERE user_id=?', (user_id,))

    cur.execute("DELETE FROM users WHERE id=?", (user_id,))


def build_admin_system_health(cur):

    cur.execute("PRAGMA integrity_check")
    integrity_result = cur.fetchone()[0]

    cur.execute("PRAGMA journal_mode")
    journal_mode = cur.fetchone()[0]

    database_path = Path(DATABASE)
    database_size = database_path.stat().st_size if database_path.exists() else 0
    profile_usage = get_directory_usage(PROFILE_UPLOAD_FOLDER)
    jarvis_upload_usage = get_directory_usage(BASE_DIR / "static" / "uploads" / "jarvis")
    total_upload_files = int(profile_usage["files"] + jarvis_upload_usage["files"])

    gemini_api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    gemini_client = get_gemini_client() if gemini_api_key else None
    gemini_status = "Configured" if gemini_api_key and gemini_client else "Unavailable" if gemini_api_key else "Missing API key"

    return {
        "database_health": "Healthy" if integrity_result == "ok" else integrity_result,
        "database_journal_mode": str(journal_mode).upper(),
        "database_size": format_bytes(database_size),
        "database_size_bytes": database_size,
        "gemini_api_status": gemini_status,
        "storage_usage": format_bytes(profile_usage["bytes"] + jarvis_upload_usage["bytes"]),
        "storage_usage_bytes": int(profile_usage["bytes"] + jarvis_upload_usage["bytes"]),
        "profile_uploads_count": int(profile_usage["files"]),
        "jarvis_uploads_count": int(jarvis_upload_usage["files"]),
        "uploads_count": total_upload_files,
        "profile_uploads_size": profile_usage["formatted"],
        "jarvis_uploads_size": jarvis_upload_usage["formatted"],
    }


def build_admin_settings_snapshot(cur):

    cur.execute("SELECT COUNT(*) AS count FROM users WHERE is_developer=1")
    explicit_developers = int(cur.fetchone()["count"] or 0)

    return {
        "developer_emails": sorted(get_configured_developer_emails()),
        "explicit_developer_count": explicit_developers,
        "session_timeout_minutes": int(SESSION_TIMEOUT_SECONDS / 60),
        "max_upload_mb": int(app.config["MAX_CONTENT_LENGTH"] / (1024 * 1024)),
    }


def build_admin_reports_payload(cur):

    metrics = get_admin_dashboard_metrics(cur)
    cur.execute("""
        SELECT u.id, u.name, u.email, u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM expenses e WHERE e.user_id=u.id) AS transactions,
               (SELECT COALESCE(SUM(amount), 0) FROM expenses e WHERE e.user_id=u.id) AS expenses_total,
               (SELECT COUNT(*) FROM jarvis_history j WHERE j.user_id=u.id) AS ai_requests
        FROM users u
        ORDER BY datetime(substr(replace(u.created_at, 'T', ' '), 1, 19)) DESC, u.id DESC
        LIMIT 50
    """)
    user_rows = [dict(row) for row in cur.fetchall()]

    cur.execute("""
        SELECT a.title, a.activity_type, a.created_at, u.name AS user_name
        FROM activity_logs a
        JOIN users u ON u.id = a.user_id
        WHERE a.activity_type IN ('pdf_generated', 'csv_exported', 'profile_updated', 'login')
        ORDER BY datetime(substr(replace(a.created_at, 'T', ' '), 1, 19)) DESC, a.id DESC
        LIMIT 15
    """)
    recent_report_activity = [dict(row) for row in cur.fetchall()]

    return {
        "metrics": metrics,
        "users": user_rows,
        "recent_report_activity": recent_report_activity,
        "generated_at": ist_now().strftime("%Y-%m-%d %H:%M:%S IST"),
    }


def build_admin_csv_response(report_payload):

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(["Finance Analytics Platform for Financial Reporting and Budget Tracking - Developer Admin Report"])
    writer.writerow(["Generated", report_payload["generated_at"]])
    writer.writerow([])
    writer.writerow(["Metric", "Value"])
    for label, key in (
        ("Total Users", "total_users"),
        ("Active Users", "active_users"),
        ("Total Transactions", "total_transactions"),
        ("Total Expenses", "total_expenses"),
        ("Total Income", "total_income"),
        ("Total Goals", "total_goals"),
        ("AI Requests Today", "ai_requests_today"),
    ):
        writer.writerow([label, report_payload["metrics"][key]])

    writer.writerow([])
    writer.writerow(["Users"])
    writer.writerow(["User ID", "Name", "Email", "Created At", "Last Login", "Transactions", "Expenses Total", "AI Requests"])
    for user_row in report_payload["users"]:
        writer.writerow([
            user_row["id"],
            user_row["name"],
            user_row["email"],
            user_row["created_at"],
            user_row["last_login_at"] or "Never",
            user_row["transactions"],
            f"{float(user_row['expenses_total'] or 0):.2f}",
            user_row["ai_requests"],
        ])

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=admin_report.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


def build_admin_pdf_response(report_payload):

    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=42,
        bottomMargin=36,
        title="Finance Analytics Platform for Financial Reporting and Budget Tracking Developer Admin Report",
    )

    styles = getSampleStyleSheet()
    elements = [
        Paragraph("Finance Analytics Platform for Financial Reporting and Budget Tracking", styles["Title"]),
        Spacer(1, 10),
        Paragraph("Developer Admin Report", styles["Heading2"]),
        Paragraph(f"Generated: {report_payload['generated_at']}", styles["BodyText"]),
        Spacer(1, 14),
    ]

    metric_rows = [["Metric", "Value"]]
    for label, key in (
        ("Total Users", "total_users"),
        ("Active Users", "active_users"),
        ("Total Transactions", "total_transactions"),
        ("Total Expenses", "total_expenses"),
        ("Total Income", "total_income"),
        ("Total Goals", "total_goals"),
        ("AI Requests Today", "ai_requests_today"),
    ):
        value = report_payload["metrics"][key]
        metric_rows.append([label, f"{value:,.2f}" if isinstance(value, float) else str(value)])

    metric_table = Table(metric_rows, colWidths=[210, 250])
    metric_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(metric_table)
    elements.append(Spacer(1, 14))

    user_rows = [["User", "Email", "Transactions", "Expenses", "AI Requests"]]
    for user_row in report_payload["users"][:25]:
        user_rows.append([
            user_row["name"],
            user_row["email"],
            str(user_row["transactions"]),
            f"Rs. {float(user_row['expenses_total'] or 0):,.0f}",
            str(user_row["ai_requests"]),
        ])

    user_table = Table(user_rows, colWidths=[100, 170, 70, 80, 70])
    user_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eff6ff")]),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ]))
    elements.append(Paragraph("User Snapshot", styles["Heading2"]))
    elements.append(user_table)

    pdf.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="admin_report.pdf", mimetype="application/pdf")


def get_user_initial(user):

    if not user:
        return "U"

    name = str(user["name"] or "").strip()
    if not name:
        return "U"

    for char in name:
        if char.isalnum():
            return char.upper()

    return "U"


def calculate_profile_completion(user):

    if not user:
        return 0

    fields = [
        user["name"],
        user["phone"],
        user["bio"],
        user["github_url"],
        user["linkedin_url"],
        normalize_profile_image_db_value(user["profile_image"]),
        user["email_verified"],
    ]

    filled = 0
    for value in fields:
        if isinstance(value, int):
            filled += 1 if value else 0
        elif value:
            filled += 1

    return round((filled / len(fields)) * 100) if fields else 0


def render_email_template(template_name, **context):

    # Render directly from Jinja environment so email rendering does not invoke
    # request context processors that can open additional DB write paths.
    template = app.jinja_env.get_template(f"emails/{template_name}")
    return template.render(**context)


def mask_email(email_value):

    if not email_value or "@" not in email_value:
        return "<invalid-email>"

    local_part, domain_part = email_value.split("@", 1)
    if len(local_part) <= 2:
        masked_local = "*" * len(local_part)
    else:
        masked_local = f"{local_part[:2]}***"

    return f"{masked_local}@{domain_part}"


def validate_mail_configuration():

    config_issues = []
    suggestions = []

    mail_server = app.config.get("MAIL_SERVER")
    mail_port = app.config.get("MAIL_PORT")
    mail_use_tls = app.config.get("MAIL_USE_TLS")
    mail_use_ssl = app.config.get("MAIL_USE_SSL")
    mail_username = app.config.get("MAIL_USERNAME")
    mail_password = app.config.get("MAIL_PASSWORD")
    mail_default_sender = app.config.get("MAIL_DEFAULT_SENDER")

    if not mail_server:
        config_issues.append("MAIL_SERVER is missing")
        suggestions.append("Set MAIL_SERVER=smtp.gmail.com for Gmail SMTP")

    if not mail_port:
        config_issues.append("MAIL_PORT is missing")
        suggestions.append("Set MAIL_PORT=587 for TLS or 465 for SSL")

    if mail_use_tls and mail_use_ssl:
        config_issues.append("Both MAIL_USE_TLS and MAIL_USE_SSL are enabled")
        suggestions.append("Enable only one: TLS with port 587, or SSL with port 465")

    if not mail_username:
        config_issues.append("MAIL_USERNAME is missing")
        suggestions.append("Set MAIL_USERNAME to your Gmail address")

    if not mail_password:
        config_issues.append("MAIL_PASSWORD is missing")
        suggestions.append("Use a 16-character Google App Password in MAIL_PASSWORD")
    elif isinstance(mail_password, str) and len(mail_password.strip()) < 16:
        suggestions.append("MAIL_PASSWORD appears short; for Gmail use a 16-character App Password")

    if not mail_default_sender:
        config_issues.append("MAIL_DEFAULT_SENDER is missing")
        suggestions.append("Set MAIL_DEFAULT_SENDER to the same address as MAIL_USERNAME")

    return config_issues, suggestions


def verify_smtp_connection(return_error=False):

    mail_server = app.config.get("MAIL_SERVER")
    mail_port = int(app.config.get("MAIL_PORT", 0) or 0)
    mail_use_tls = app.config.get("MAIL_USE_TLS")
    mail_use_ssl = app.config.get("MAIL_USE_SSL")
    mail_username = app.config.get("MAIL_USERNAME")
    mail_password = app.config.get("MAIL_PASSWORD")

    logger.info(
        "SMTP check: server=%s port=%s tls=%s ssl=%s username=%s",
        mail_server,
        mail_port,
        mail_use_tls,
        mail_use_ssl,
        mask_email(mail_username),
    )

    try:
        if mail_use_ssl:
            smtp_conn = smtplib.SMTP_SSL(mail_server, mail_port, timeout=15)
        else:
            smtp_conn = smtplib.SMTP(mail_server, mail_port, timeout=15)

        with smtp_conn as smtp:
            ehlo_status = smtp.ehlo()
            logger.info("SMTP EHLO response: %s", ehlo_status)

            if mail_use_tls and not mail_use_ssl:
                smtp.starttls()
                logger.info("SMTP TLS negotiation successful")
                ehlo_after_tls = smtp.ehlo()
                logger.info("SMTP EHLO after STARTTLS: %s", ehlo_after_tls)

            if mail_username and mail_password:
                smtp.login(mail_username, mail_password)
                logger.info("SMTP authentication successful")
            else:
                logger.warning("SMTP authentication skipped due to missing credentials")

        logger.info("SMTP connection established and closed cleanly")
        result = (True, None) if return_error else True
        return result

    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP authentication failed. For Gmail, use App Password instead of regular password.")
        result = (False, "SMTP authentication failed. For Gmail, use a Gmail App Password.") if return_error else False
        return result
    except smtplib.SMTPException:
        logger.exception("SMTP protocol error while validating connection")
        result = (False, "The SMTP server rejected the connection or authentication request.") if return_error else False
        return result
    except Exception as exc:
        logger.exception("Unexpected error while validating SMTP connection")
        result = (False, f"Could not connect to the SMTP server: {exc}") if return_error else False
        return result


def send_email_message(subject, recipients, html_body, attachments=None, return_error=False):

    config_issues, suggestions = validate_mail_configuration()

    if config_issues:
        logger.error("Email config validation failed: %s", "; ".join(config_issues))
        for suggestion in suggestions:
            logger.error("Configuration suggestion: %s", suggestion)
        result = (False, "; ".join(config_issues)) if return_error else False
        return result

    smtp_result = verify_smtp_connection(return_error=return_error)
    smtp_ready = smtp_result[0] if return_error else smtp_result
    if not smtp_ready:
        logger.error("Skipping mail.send because SMTP preflight check failed")
        result = smtp_result if return_error else False
        return result

    try:
        masked_recipients = [mask_email(recipient) for recipient in recipients]
        logger.info("Preparing email send: subject=%s recipients=%s", subject, masked_recipients)
        message = Message(
            subject=subject,
            recipients=recipients,
            html=html_body,
            sender=app.config.get("MAIL_DEFAULT_SENDER"),
        )

        for attachment in attachments or []:
            filename = attachment.get("filename")
            data = attachment.get("data")
            mimetype = attachment.get("mimetype", "application/octet-stream")
            if filename and data is not None:
                message.attach(filename, mimetype, data)

        mail.send(message)
        logger.info("Email sent successfully to %s", masked_recipients)
        result = (True, None) if return_error else True
        return result
    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP authentication failed while sending email")
        result = (False, "SMTP authentication failed. For Gmail, use a Gmail App Password.") if return_error else False
        return result
    except smtplib.SMTPException:
        logger.exception("SMTP error while sending email")
        result = (False, "The SMTP server rejected the email.") if return_error else False
        return result
    except Exception:
        logger.exception("mail.send failed with an exception")
        result = (False, "The email could not be sent. Check the Flask terminal for details.") if return_error else False
        return result


def send_welcome_email(user_name, recipient_email):

    html_body = render_email_template(
        "welcome.html",
        user_name=user_name,
    )
    return send_email_message("Welcome to Finance Analytics Platform for Financial Reporting and Budget Tracking", [recipient_email], html_body)


def send_password_changed_email(user_name, recipient_email):

    html_body = render_email_template(
        "password_changed.html",
        user_name=user_name,
    )
    return send_email_message("Your Finance Analytics Platform for Financial Reporting and Budget Tracking Password Was Changed", [recipient_email], html_body)


def send_password_reset_otp_email(user_name, recipient_email, otp_code):

    html_body = render_email_template(
        "password_reset_otp.html",
        user_name=user_name,
        otp_code=otp_code,
    )
    return send_email_message("Finance Analytics Platform for Financial Reporting and Budget Tracking Password Reset OTP", [recipient_email], html_body)


def send_profile_updated_email(user_name, recipient_email):

    html_body = render_email_template(
        "profile_updated.html",
        user_name=user_name,
    )
    return send_email_message("Finance Analytics Platform for Financial Reporting and Budget Tracking Profile Updated", [recipient_email], html_body)


def send_monthly_summary_email(user_name, recipient_email, report_context):

    html_body = render_email_template(
        "monthly_summary.html",
        user_name=user_name,
        income_summary=report_context["income_summary"],
        expense_summary=report_context["expense_summary"],
        savings_summary=report_context["savings_summary"],
        budget_usage_percent=report_context["budget_usage_percent"],
        health_score=report_context["health_score"],
        report_generated_at=report_context["report_generated_at"],
    )
    return send_email_message("Your Monthly Finance Analytics Platform for Financial Reporting and Budget Tracking Summary", [recipient_email], html_body)


def send_budget_exceeded_email(user_name, recipient_email, month_value, expense_total, budget_total, budget_percent):

    html_body = render_email_template(
        "budget_exceeded.html",
        user_name=user_name,
        month_value=month_value,
        expense_total=expense_total,
        budget_total=budget_total,
        budget_percent=budget_percent,
        exceeded_by=max(expense_total - budget_total, 0),
    )
    return send_email_message("Budget Exceeded Alert", [recipient_email], html_body)


def send_goal_completed_email(user_name, recipient_email, goal_name, target_amount):

    html_body = render_email_template(
        "goal_completed.html",
        user_name=user_name,
        goal_name=goal_name,
        target_amount=target_amount,
    )
    return send_email_message("Goal Completed - Congratulations", [recipient_email], html_body)


def send_investment_added_email(user_name, recipient_email, investment_name, asset_name, invested_amount, current_value):

    html_body = render_email_template(
        "investment_added.html",
        user_name=user_name,
        investment_name=investment_name,
        asset_name=asset_name,
        invested_amount=invested_amount,
        current_value=current_value,
    )
    return send_email_message("Investment Added Confirmation", [recipient_email], html_body)


def send_report_generated_email(user_name, recipient_email, report_generated_at, report_id):

    html_body = render_email_template(
        "report_generated.html",
        user_name=user_name,
        report_generated_at=report_generated_at,
        report_id=report_id,
    )
    return send_email_message("Your Financial Report Is Ready", [recipient_email], html_body)


def send_report_emailed_email(user_name, recipient_email, report_generated_at, report_id):

    html_body = render_email_template(
        "report_emailed.html",
        user_name=user_name,
        report_generated_at=report_generated_at,
        report_id=report_id,
    )
    return send_email_message("Report Emailed Successfully", [recipient_email], html_body)


def generate_otp_code():

    return f"{secrets.randbelow(1000000):06d}"


# ---------------- DATABASE INITIALIZATION ---------------- #

def init_db():

    conn = get_db()
    cur = conn.cursor()

    # Enable WAL journaling for better read/write concurrency with SQLite.
    cur.execute("PRAGMA journal_mode=WAL")

    # Users Table
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        password TEXT NOT NULL,
        income REAL DEFAULT 0
    )
    """)

    ensure_column(cur, "users", "profile_image", "TEXT")
    ensure_column(cur, "users", "bio", "TEXT")
    ensure_column(cur, "users", "github_url", "TEXT")
    ensure_column(cur, "users", "linkedin_url", "TEXT")
    ensure_column(cur, "users", "email_verified", "INTEGER DEFAULT 1")
    ensure_column(cur, "users", "created_at", "TEXT")
    ensure_column(cur, "users", "updated_at", "TEXT")
    ensure_column(cur, "users", "last_login_at", "TEXT")
    ensure_column(cur, "users", "current_browser", "TEXT")
    ensure_column(cur, "users", "current_device", "TEXT")
    ensure_column(cur, "users", "privacy_private_profile", "INTEGER DEFAULT 0")
    ensure_column(cur, "users", "privacy_public_developer_profile", "INTEGER DEFAULT 0")
    ensure_column(cur, "users", "privacy_search_visibility", "INTEGER DEFAULT 0")
    ensure_column(cur, "users", "notify_email_notifications", "INTEGER DEFAULT 1")
    ensure_column(cur, "users", "notify_budget_alerts", "INTEGER DEFAULT 1")
    ensure_column(cur, "users", "notify_monthly_report", "INTEGER DEFAULT 1")
    ensure_column(cur, "users", "notify_goal_alerts", "INTEGER DEFAULT 1")
    ensure_column(cur, "users", "notify_investment_alerts", "INTEGER DEFAULT 1")
    ensure_column(cur, "users", "appearance_theme", "TEXT DEFAULT 'default'")
    ensure_column(cur, "users", "theme_mode", "TEXT DEFAULT 'light'")
    ensure_column(cur, "users", "export_format_preference", "TEXT DEFAULT 'csv'")
    ensure_column(cur, "users", "email_report_frequency", "TEXT DEFAULT 'monthly'")
    ensure_column(cur, "users", "search_preference", "TEXT DEFAULT 'all'")
    ensure_column(cur, "users", "layout_preference", "TEXT DEFAULT 'default'")
    ensure_column(cur, "users", "date_of_birth", "TEXT")
    ensure_column(cur, "users", "location", "TEXT")
    ensure_column(cur, "users", "city", "TEXT")
    ensure_column(cur, "users", "state", "TEXT")
    ensure_column(cur, "users", "country", "TEXT")
    ensure_column(cur, "users", "occupation", "TEXT")
    ensure_column(cur, "users", "organization", "TEXT")
    ensure_column(cur, "users", "is_developer", "INTEGER DEFAULT 0")
    ensure_column(cur, "users", "is_suspended", "INTEGER DEFAULT 0")

    cur.execute("UPDATE users SET email_verified=1 WHERE email_verified IS NULL")
    cur.execute("UPDATE users SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at='' ")
    cur.execute("UPDATE users SET updated_at=created_at WHERE updated_at IS NULL OR updated_at='' ")
    cur.execute("UPDATE users SET privacy_private_profile=0 WHERE privacy_private_profile IS NULL")
    cur.execute("UPDATE users SET privacy_public_developer_profile=0 WHERE privacy_public_developer_profile IS NULL")
    cur.execute("UPDATE users SET privacy_search_visibility=0 WHERE privacy_search_visibility IS NULL")
    cur.execute("UPDATE users SET notify_email_notifications=1 WHERE notify_email_notifications IS NULL")
    cur.execute("UPDATE users SET notify_budget_alerts=1 WHERE notify_budget_alerts IS NULL")
    cur.execute("UPDATE users SET notify_monthly_report=1 WHERE notify_monthly_report IS NULL")
    cur.execute("UPDATE users SET notify_goal_alerts=1 WHERE notify_goal_alerts IS NULL")
    cur.execute("UPDATE users SET notify_investment_alerts=1 WHERE notify_investment_alerts IS NULL")
    cur.execute("UPDATE users SET appearance_theme='default' WHERE appearance_theme IS NULL OR appearance_theme='' ")
    cur.execute("UPDATE users SET theme_mode='light' WHERE theme_mode IS NULL OR theme_mode='' ")
    cur.execute("UPDATE users SET export_format_preference='csv' WHERE export_format_preference IS NULL OR export_format_preference='' ")
    cur.execute("UPDATE users SET email_report_frequency='monthly' WHERE email_report_frequency IS NULL OR email_report_frequency='' ")
    cur.execute("UPDATE users SET search_preference='all' WHERE search_preference IS NULL OR search_preference='' ")
    cur.execute("UPDATE users SET layout_preference='default' WHERE layout_preference IS NULL OR layout_preference='' ")
    cur.execute("UPDATE users SET is_developer=0 WHERE is_developer IS NULL")
    cur.execute("UPDATE users SET is_suspended=0 WHERE is_suspended IS NULL")

    for developer_email in get_configured_developer_emails():
        cur.execute("UPDATE users SET is_developer=1 WHERE lower(email)=?", (developer_email,))

    cur.execute("SELECT id, profile_image FROM users")
    for row in cur.fetchall():
        normalized = normalize_profile_image_db_value(row["profile_image"])
        current_value = (str(row["profile_image"]).strip().replace("\\", "/") if row["profile_image"] else None)
        if normalized != current_value:
            cur.execute("UPDATE users SET profile_image=? WHERE id=?", (normalized, row["id"]))

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        category TEXT,
        amount REAL,
        description TEXT,
        expense_date TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    # Budget Table
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS budget(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        monthly_budget REAL,
        month TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    # Investment Table
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS investments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        asset TEXT NOT NULL,
        investment_name TEXT NOT NULL,
        invested_amount REAL NOT NULL,
        current_value REAL NOT NULL,
        purchase_date TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    # Goals Table

    cur.execute("""
    CREATE TABLE IF NOT EXISTS goals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        goal_name TEXT NOT NULL,
        target_amount REAL NOT NULL,
        saved_amount REAL NOT NULL,
        target_date TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_resets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        otp_hash TEXT NOT NULL,
        attempts INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        is_used INTEGER DEFAULT 0,
        expires_at TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        notification_type TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        priority TEXT DEFAULT 'info',
        event_key TEXT,
        is_read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    ensure_column(cur, "notifications", "event_key", "TEXT")
    ensure_column(cur, "notifications", "priority", "TEXT DEFAULT 'info'")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS health_scores(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        score INTEGER NOT NULL,
        status TEXT NOT NULL,
        score_date TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, score_date),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS recommendations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        recommendation_type TEXT NOT NULL,
        message TEXT NOT NULL,
        generated_on TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS achievements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        achievement_key TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        icon TEXT NOT NULL,
        target_value REAL NOT NULL DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_achievements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        achievement_id INTEGER NOT NULL,
        progress REAL NOT NULL DEFAULT 0,
        is_unlocked INTEGER NOT NULL DEFAULT 0,
        unlocked_at TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, achievement_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(achievement_id) REFERENCES achievements(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        activity_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        icon TEXT DEFAULT 'fa-solid fa-circle-info',
        related_route TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dashboard_preferences(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        hidden_cards TEXT DEFAULT '',
        card_order TEXT DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reminders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        reminder_date TEXT NOT NULL,
        reminder_type TEXT NOT NULL,
        is_completed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jarvis_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jarvis_feedback(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        conversation_id INTEGER NOT NULL,
        feedback TEXT NOT NULL CHECK(feedback IN ('like', 'dislike')),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, conversation_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(conversation_id) REFERENCES jarvis_history(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS feedback(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
        comment TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jarvis_settings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        response_style TEXT NOT NULL DEFAULT 'balanced',
        language TEXT NOT NULL DEFAULT 'en',
        voice_enabled INTEGER NOT NULL DEFAULT 1,
        voice_gender TEXT NOT NULL DEFAULT 'auto',
        speech_rate REAL NOT NULL DEFAULT 1.0,
        pitch REAL NOT NULL DEFAULT 1.0,
        temperature REAL NOT NULL DEFAULT 0.3,
        theme_preference TEXT NOT NULL DEFAULT 'system',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jarvis_attachments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        original_name TEXT NOT NULL,
        stored_name TEXT NOT NULL,
        mime_type TEXT,
        file_size INTEGER NOT NULL DEFAULT 0,
        extracted_text TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    achievement_seed = [
        ("first_expense", "First Expense", "Record your very first expense.", "fa-solid fa-receipt", 1),
        ("budget_planner", "Budget Planner", "Create your first monthly budget.", "fa-solid fa-chart-pie", 1),
        ("savings_started", "Savings Started", "Maintain positive monthly savings.", "fa-solid fa-piggy-bank", 1),
        ("investment_added", "Investment Added", "Add your first investment entry.", "fa-solid fa-chart-line", 1),
        ("goal_completed", "Goal Completed", "Complete at least one financial goal.", "fa-solid fa-bullseye", 1),
        ("expense_tracker", "Expense Tracker", "Track 10 or more transactions.", "fa-solid fa-list-check", 10),
        ("transactions_30", "30 Transactions", "Track 30 expense transactions.", "fa-solid fa-layer-group", 30),
        ("savings_champion", "Savings Champion", "Reach a savings ratio of 25% or more.", "fa-solid fa-award", 25),
        ("investment_expert", "Investment Expert", "Maintain ROI >= 12% with 3+ investments.", "fa-solid fa-trophy", 12),
    ]

    for achievement in achievement_seed:
        cur.execute("""
            INSERT OR IGNORE INTO achievements(achievement_key, title, description, icon, target_value)
            VALUES(?,?,?,?,?)
        """, achievement)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_expenses_user_date ON expenses(user_id, expense_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_read_date ON notifications(user_id, is_read, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_user_date ON recommendations(user_id, generated_on)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_health_scores_user_date ON health_scores(user_id, score_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_achievements_user ON user_achievements(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_user_date ON activity_logs(user_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user_date ON reminders(user_id, reminder_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jarvis_history_user_date ON jarvis_history(user_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jarvis_feedback_user_conversation ON jarvis_feedback(user_id, conversation_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jarvis_settings_user ON jarvis_settings(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jarvis_attachments_user_date ON jarvis_attachments(user_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user_date ON feedback(user_id, created_at)")

    conn.commit()
    conn.close()
    
# ---------------- HOME ---------------- #

@app.route("/")

def home():

    return render_template("index.html")

# ---------------- LOGIN ---------------- #

@app.route("/login", methods=["GET", "POST"])

def login():

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/login")

        email = request.form["email"]
        password = request.form["password"]

        conn = None
        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                SELECT *
                FROM users
                WHERE email=?
            """, (email,))

            user = cur.fetchone()

            if user and verify_password(user["password"], password):

                if is_suspended_user(user):
                    flash("Your account has been suspended. Please contact the developer.", "danger")
                    return redirect("/login")

                if not is_hashed_password(user["password"]):
                    cur.execute("""
                        UPDATE users
                        SET password=?
                        WHERE id=?
                    """, (hash_password(password), user["id"]))
                    conn.commit()

                browser_name, device_name = get_user_agent_summary()
                cur.execute("""
                    UPDATE users
                    SET last_login_at=?, current_browser=?, current_device=?
                    WHERE id=?
                """, (
                    datetime.utcnow().isoformat(),
                    browser_name,
                    device_name,
                    user["id"],
                ))

                log_activity(
                    cur,
                    user["id"],
                    "login",
                    "Login Activity",
                    f"Signed in from {browser_name} on {device_name}.",
                    "fa-solid fa-right-to-bracket",
                    "/dashboard",
                )
                conn.commit()

                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["theme_mode"] = (user["theme_mode"] if "theme_mode" in user.keys() and user["theme_mode"] in {"light", "dark"} else "light")
                session["last_activity"] = datetime.utcnow().timestamp()
                session["jarvis_coach_pending"] = True
                session.permanent = True

                return redirect("/dashboard")

            flash("Invalid Email or Password!", "danger")

        finally:
            if conn is not None:
                conn.close()

    return render_template("login.html")

# ---------------- LOGOUT ---------------- #

@app.route("/logout")

def logout():

    session.clear()

    return redirect("/")

@app.before_request
def enforce_session_timeout():

    if request.endpoint == "static":
        return

    user_id = session.get("user_id")

    if not user_id:
        return

    current_user = get_user_by_id(user_id)
    if not current_user:
        session.clear()
        flash("Your session is no longer valid. Please log in again.", "warning")
        return redirect("/login")

    if is_suspended_user(current_user):
        session.clear()
        flash("Your account has been suspended. Please contact the developer.", "danger")
        return redirect("/login")

    last_activity = session.get("last_activity")
    now_timestamp = datetime.utcnow().timestamp()

    if last_activity and now_timestamp - last_activity > SESSION_TIMEOUT_SECONDS:
        session.clear()
        flash("Your session expired due to inactivity. Please log in again.", "warning")
        return redirect("/login")

    session["last_activity"] = now_timestamp
    session.permanent = True


@app.context_processor
def inject_account_context():

    current_user = None

    if session.get("user_id"):
        current_user = get_user_by_id(session["user_id"])

    show_footer = request.endpoint in {"home", "profile"}
    unread_notifications_count = 0
    recent_notifications = []

    if current_user:
        conn = get_db()
        try:
            cur = conn.cursor()

            financial_snapshot = get_financial_snapshot(cur, current_user["id"])
            spending_analysis = get_spending_analysis(cur, current_user["id"])
            goal_summary = get_goal_summary(cur, current_user["id"])
            investment_summary = get_investment_summary(cur, current_user["id"])
            budget_recommendations = get_budget_recommendations(
                cur,
                current_user["id"],
                financial_snapshot["income"],
                spending_analysis,
                financial_snapshot,
                investment_summary,
                goal_summary,
            )

            report_flow_endpoints = {"reports", "download_pdf", "export_csv", "email_report"}
            if request.endpoint not in report_flow_endpoints:
                refresh_notifications(
                    cur,
                    current_user,
                    financial_snapshot,
                    spending_analysis,
                    budget_recommendations,
                    goal_summary,
                    investment_summary,
                )

            unread_notifications_count, recent_notifications = get_notification_feed(cur, current_user["id"], limit=6)

            conn.commit()
        finally:
            conn.close()

    return {
        "current_user": current_user,
        "current_user_is_developer": is_developer_user(current_user),
        "current_profile_image": get_user_profile_image(current_user) if current_user else None,
        "current_profile_initial": get_user_initial(current_user),
        "current_profile_image_version": (current_user["updated_at"] if current_user and current_user["updated_at"] else current_user["created_at"] if current_user else ""),
        "current_theme_mode": session.get("theme_mode", "light"),
        "csrf_token": generate_csrf_token,
        "current_year": ist_now().year,
        "show_footer": show_footer,
        "unread_notifications_count": unread_notifications_count,
        "recent_notifications": recent_notifications,
    }


def get_investment_summary(cur, user_id):

    cur.execute("""
        SELECT
            IFNULL(SUM(invested_amount), 0) AS total_investment,
            IFNULL(SUM(current_value), 0) AS current_value
        FROM investments
        WHERE user_id=?
    """, (user_id,))

    summary = cur.fetchone()

    total_investment = float(summary["total_investment"])
    current_value = float(summary["current_value"])
    total_profit = current_value - total_investment
    roi = round((total_profit / total_investment) * 100, 2) if total_investment else 0

    return {
        "total_investment": round(total_investment, 2),
        "current_value": round(current_value, 2),
        "total_profit": round(total_profit, 2),
        "roi": roi
    }


def get_goal_summary(cur, user_id):

    cur.execute("""
        SELECT
            COUNT(*) AS total_goals,
            SUM(CASE WHEN saved_amount >= target_amount THEN 1 ELSE 0 END) AS completed_goals,
            IFNULL(SUM(target_amount), 0) AS total_target,
            IFNULL(SUM(saved_amount), 0) AS total_saved
        FROM goals
        WHERE user_id=?
    """, (user_id,))

    summary = cur.fetchone()

    total_goals = int(summary["total_goals"] or 0)
    completed_goals = int(summary["completed_goals"] or 0)
    active_goals = total_goals - completed_goals
    overall_progress = round((float(summary["total_saved"]) / float(summary["total_target"])) * 100, 2) if summary["total_target"] else 0

    return {
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "active_goals": active_goals,
        "overall_progress": overall_progress
    }


def clamp_value(value, minimum, maximum):

    return max(minimum, min(maximum, value))


def month_key(date_obj):

    return f"{date_obj.year:04d}-{date_obj.month:02d}"


def month_label(date_obj):

    return date_obj.strftime("%b %Y")


def shift_to_previous_month(date_obj):

    first_day = date_obj.replace(day=1)
    return first_day - timedelta(days=1)


def get_financial_snapshot(cur, user_id):

    cur.execute("""
        SELECT IFNULL(income, 0) AS income
        FROM users
        WHERE id=?
    """, (user_id,))
    income_row = cur.fetchone()
    income = float(income_row["income"] if income_row else 0)

    cur.execute("""
        SELECT IFNULL(SUM(amount), 0) AS total_expense
        FROM expenses
        WHERE user_id=?
    """, (user_id,))
    total_expense = float(cur.fetchone()["total_expense"])

    cur.execute("""
        SELECT monthly_budget
        FROM budget
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    budget_row = cur.fetchone()
    latest_budget = float(budget_row["monthly_budget"]) if budget_row else 0.0

    return {
        "income": round(income, 2),
        "total_expense": round(total_expense, 2),
        "total_savings": round(income - total_expense, 2),
        "latest_budget": round(latest_budget, 2),
    }


def get_spending_analysis(cur, user_id):

    current_month_date = ist_now().replace(day=1)
    previous_month_date = shift_to_previous_month(current_month_date)
    current_month = month_key(current_month_date)
    previous_month = month_key(previous_month_date)

    cur.execute("""
        SELECT IFNULL(SUM(amount), 0) AS monthly_expense
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7)=?
    """, (user_id, current_month))
    total_monthly_expenses = float(cur.fetchone()["monthly_expense"])

    cur.execute("""
        SELECT IFNULL(SUM(amount), 0) AS monthly_expense
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7)=?
    """, (user_id, previous_month))
    previous_month_expenses = float(cur.fetchone()["monthly_expense"])

    month_cursor = current_month_date
    requested_month_keys = []
    for _ in range(6):
        requested_month_keys.append(month_key(month_cursor))
        month_cursor = shift_to_previous_month(month_cursor)

    requested_month_keys = list(reversed(requested_month_keys))

    placeholders = ",".join("?" for _ in requested_month_keys)
    cur.execute(f"""
        SELECT substr(expense_date, 1, 7) AS month_key, IFNULL(SUM(amount), 0) AS total
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7) IN ({placeholders})
        GROUP BY month_key
        ORDER BY month_key
    """, [user_id, *requested_month_keys])

    month_totals = {row["month_key"]: float(row["total"]) for row in cur.fetchall()}
    monthly_trend_labels = []
    monthly_trend_values = []

    for month_value in requested_month_keys:
        year_part, month_part = month_value.split("-")
        date_obj = datetime(int(year_part), int(month_part), 1)
        monthly_trend_labels.append(month_label(date_obj))
        monthly_trend_values.append(round(month_totals.get(month_value, 0.0), 2))

    cur.execute("""
        SELECT category, IFNULL(SUM(amount), 0) AS total
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7)=?
        GROUP BY category
        ORDER BY total DESC
    """, (user_id, current_month))
    category_rows = cur.fetchall()

    if not category_rows:
        cur.execute("""
            SELECT category, IFNULL(SUM(amount), 0) AS total
            FROM expenses
            WHERE user_id=?
            GROUP BY category
            ORDER BY total DESC
        """, (user_id,))
        category_rows = cur.fetchall()

    category_breakdown = []
    category_map = {}

    for row in category_rows:
        amount = round(float(row["total"]), 2)
        category_breakdown.append({
            "category": row["category"],
            "amount": amount,
        })
        category_map[row["category"]] = amount

    highest_category = category_breakdown[0] if category_breakdown else {"category": "No Data", "amount": 0.0}
    lowest_category = category_breakdown[-1] if category_breakdown else {"category": "No Data", "amount": 0.0}

    cur.execute("""
        SELECT COUNT(*) AS txn_count
        FROM expenses
        WHERE user_id=?
    """, (user_id,))
    transaction_count = int(cur.fetchone()["txn_count"])

    cur.execute("""
        SELECT id, category, amount, IFNULL(description, '') AS description, expense_date
        FROM expenses
        WHERE user_id=?
        ORDER BY amount DESC, expense_date DESC
        LIMIT 5
    """, (user_id,))
    top_expenses = [
        {
            "id": row["id"],
            "category": row["category"],
            "amount": round(float(row["amount"]), 2),
            "description": row["description"],
            "expense_date": row["expense_date"],
        }
        for row in cur.fetchall()
    ]

    cur.execute("""
        SELECT category, COUNT(*) AS usage_count
        FROM expenses
        WHERE user_id=?
        GROUP BY category
        ORDER BY usage_count DESC, category ASC
        LIMIT 1
    """, (user_id,))
    freq_row = cur.fetchone()
    most_frequent_category = {
        "category": freq_row["category"] if freq_row else "No Data",
        "count": int(freq_row["usage_count"]) if freq_row else 0,
    }

    cur.execute("""
        SELECT IFNULL(SUM(amount), 0) AS rolling_total,
               COUNT(DISTINCT expense_date) AS active_days
        FROM expenses
        WHERE user_id=? AND date(expense_date) >= date('now', '-30 day')
    """, (user_id,))
    rolling_row = cur.fetchone()
    active_days = int(rolling_row["active_days"] or 0)
    daily_average_expense = round(float(rolling_row["rolling_total"] or 0) / active_days, 2) if active_days else 0.0

    cur.execute("""
        SELECT strftime('%Y-W%W', expense_date) AS week_key,
               IFNULL(SUM(amount), 0) AS total
        FROM expenses
        WHERE user_id=? AND date(expense_date) >= date('now', '-56 day')
        GROUP BY week_key
        ORDER BY week_key
    """, (user_id,))
    weekly_rows = cur.fetchall()
    weekly_trend_labels = [row["week_key"] for row in weekly_rows]
    weekly_trend_values = [round(float(row["total"]), 2) for row in weekly_rows]

    category_ranking = []
    rank_position = 1
    for item in category_breakdown:
        category_ranking.append({
            "rank": rank_position,
            "category": item["category"],
            "amount": item["amount"],
        })
        rank_position += 1

    biggest_transaction = top_expenses[0] if top_expenses else {
        "id": None,
        "category": "No Data",
        "amount": 0.0,
        "description": "",
        "expense_date": None,
    }

    monthly_change_amount = total_monthly_expenses - previous_month_expenses
    if previous_month_expenses > 0:
        monthly_change_percent = round((monthly_change_amount / previous_month_expenses) * 100, 2)
    else:
        monthly_change_percent = 0.0

    return {
        "current_month": current_month,
        "total_monthly_expense": round(total_monthly_expenses, 2),
        "previous_month_expense": round(previous_month_expenses, 2),
        "monthly_change_amount": round(monthly_change_amount, 2),
        "monthly_change_percent": monthly_change_percent,
        "monthly_trend_labels": monthly_trend_labels,
        "monthly_trend_values": monthly_trend_values,
        "category_breakdown": category_breakdown,
        "category_map": category_map,
        "highest_category": highest_category,
        "lowest_category": lowest_category,
        "transaction_count": transaction_count,
        "daily_average_expense": daily_average_expense,
        "weekly_trend_labels": weekly_trend_labels,
        "weekly_trend_values": weekly_trend_values,
        "category_ranking": category_ranking,
        "top_expenses": top_expenses,
        "biggest_transaction": biggest_transaction,
        "most_frequent_category": most_frequent_category,
    }


def get_budget_recommendations(cur, user_id, income, spending_analysis, financial_snapshot=None, investment_summary=None, goal_summary=None):

    cur.execute("""
        SELECT monthly_budget
        FROM budget
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    row = cur.fetchone()

    current_budget = float(row["monthly_budget"]) if row else 0.0
    if current_budget <= 0:
        current_budget = income * 0.7 if income > 0 else spending_analysis["total_monthly_expense"]

    total_monthly_expense = spending_analysis["total_monthly_expense"]
    budget_utilization = round((total_monthly_expense / current_budget) * 100, 2) if current_budget > 0 else 0.0

    expected_savings_target = income * 0.2 if income > 0 else 0.0
    suggested_budget = income - expected_savings_target if income > 0 else max(total_monthly_expense * 0.95, total_monthly_expense - 1000)
    suggested_budget = max(suggested_budget, 0)

    category_limits = {
        "Food": 0.20,
        "Shopping": 0.10,
        "Travel": 0.10,
        "Education": 0.12,
        "Bills": 0.25,
        "Medical": 0.10,
        "Entertainment": 0.08,
        "Others": 0.08,
    }

    overspending_categories = []
    reference_budget = income if income > 0 else current_budget
    for category_data in spending_analysis["category_breakdown"]:
        category_name = category_data["category"]
        spent_amount = category_data["amount"]
        category_limit = category_limits.get(category_name, 0.10)
        threshold = reference_budget * category_limit if reference_budget > 0 else 0
        if threshold > 0 and spent_amount > threshold:
            overspending_categories.append({
                "category": category_name,
                "amount": round(spent_amount, 2),
                "recommended_max": round(threshold, 2),
                "excess": round(spent_amount - threshold, 2),
            })

    if budget_utilization <= 75:
        budget_health_status = "Excellent"
    elif budget_utilization <= 90:
        budget_health_status = "Good"
    elif budget_utilization <= 105:
        budget_health_status = "Fair"
    else:
        budget_health_status = "Poor"

    recommendations = []

    if overspending_categories:
        top_over = overspending_categories[0]
        recommendations.append(
            f"Reduce {top_over['category']} spending by about Rs. {top_over['excess']:,.0f} this month to stay within target."
        )

    if income > 0:
        recommendations.append(
            f"Keep at least Rs. {expected_savings_target:,.0f} (20% of income) as a monthly savings goal."
        )

    recommendations.append(
        f"Suggested monthly budget is Rs. {suggested_budget:,.0f} based on your income and spending pattern."
    )

    monthly_savings = income - total_monthly_expense
    if income > 0 and monthly_savings < income * 0.2:
        recommendations.append("Increase monthly savings by reducing discretionary categories until you reach at least a 20% savings rate.")

    if financial_snapshot:
        emergency_fund_target = total_monthly_expense * 3
        emergency_fund_current = max(float(financial_snapshot.get("total_savings", 0)), 0)
        if emergency_fund_target > 0 and emergency_fund_current < emergency_fund_target:
            gap = emergency_fund_target - emergency_fund_current
            recommendations.append(
                f"Emergency fund should be increased by roughly Rs. {gap:,.0f} to cover at least 3 months of expenses."
            )

    if investment_summary:
        if investment_summary["total_investment"] <= 0 and income > 0:
            recommendations.append("Start a recurring monthly investment to improve long-term financial growth.")
        elif investment_summary["roi"] < 0:
            recommendations.append("Investment portfolio is underperforming; review loss-making assets and rebalance risk exposure.")
        elif investment_summary["roi"] >= 10:
            recommendations.append("Investment portfolio is performing well; maintain diversification and continue disciplined contributions.")

    if goal_summary and goal_summary["active_goals"] > 0 and goal_summary["overall_progress"] < 60:
        recommendations.append("Goal progress is below target pace; allocate a fixed savings transfer toward active goals each month.")

    return {
        "current_budget": round(current_budget, 2),
        "suggested_budget": round(suggested_budget, 2),
        "budget_utilization_percent": budget_utilization,
        "overspending_categories": overspending_categories,
        "budget_health_status": budget_health_status,
        "recommendations": recommendations,
    }


def get_financial_health_score(income, spending_analysis, investment_summary, budget_recommendations, goal_summary):

    monthly_expense = spending_analysis["total_monthly_expense"]
    monthly_savings = income - monthly_expense
    savings_ratio = (monthly_savings / income) * 100 if income > 0 else 0
    expense_ratio = (monthly_expense / income) * 100 if income > 0 else 0
    investment_ratio = (investment_summary["total_investment"] / income) * 100 if income > 0 else 0
    budget_utilization = float(budget_recommendations["budget_utilization_percent"])
    tx_count = int(spending_analysis["transaction_count"])
    goal_progress = float(goal_summary["overall_progress"])
    investment_roi = float(investment_summary["roi"])

    savings_score = clamp_value(round(savings_ratio * 1.2), 0, 25)
    expense_score = clamp_value(round((100 - expense_ratio) * 0.15), 0, 15)
    budget_score = clamp_value(round((110 - budget_utilization) * 0.25), 0, 15)
    tx_score = clamp_value(round(min(tx_count, 50) * 0.2), 0, 10)
    investment_score = clamp_value(round((investment_roi + 10) * 0.6), 0, 15)
    goal_score = clamp_value(round(goal_progress * 0.15), 0, 15)
    stability_score = 5 if monthly_savings >= 0 else 0

    score = clamp_value(
        savings_score + expense_score + budget_score + tx_score + investment_score + goal_score + stability_score,
        0,
        100,
    )

    if score >= 90:
        status = "Excellent"
    elif score >= 75:
        status = "Good"
    elif score >= 60:
        status = "Average"
    else:
        status = "Needs Improvement"

    explanation = []
    if savings_ratio >= 20:
        explanation.append("Savings rate is excellent and supports long-term growth.")
    elif savings_ratio >= 10:
        explanation.append("Savings rate is healthy but can be increased for stronger resilience.")
    else:
        explanation.append("Savings rate is below recommended levels.")

    if budget_utilization <= 90:
        explanation.append("Budget utilization is healthy and controlled.")
    else:
        explanation.append("Budget utilization is high and needs tighter spending control.")

    if investment_roi >= 8:
        explanation.append("Investment performance is contributing positively.")
    elif investment_summary["total_investment"] > 0:
        explanation.append("Investment performance is modest; rebalancing may help.")
    else:
        explanation.append("No active investment growth contribution yet.")

    improvement_tips = []
    if budget_utilization > 100:
        improvement_tips.append("Reduce non-essential spending categories by 10-15% until budget utilization drops below 90%.")
    if savings_ratio < 20:
        improvement_tips.append("Automate a monthly savings transfer to reach at least 20% of income.")
    if goal_progress < 60 and goal_summary["active_goals"] > 0:
        improvement_tips.append("Increase goal allocations to improve completion progress for active goals.")
    if investment_summary["total_investment"] <= 0 and income > 0:
        improvement_tips.append("Start a recurring investment plan to improve long-term financial health.")

    return {
        "score": score,
        "status": status,
        "savings_ratio": round(savings_ratio, 2),
        "expense_ratio": round(expense_ratio, 2),
        "investment_ratio": round(investment_ratio, 2),
        "monthly_savings": round(monthly_savings, 2),
        "budget_utilization": round(budget_utilization, 2),
        "goal_progress": round(goal_progress, 2),
        "transaction_count": tx_count,
        "investment_roi": round(investment_roi, 2),
        "explanation": explanation,
        "improvement_tips": improvement_tips,
    }


def build_rule_based_ai_insights(financial_snapshot, spending_analysis, budget_recommendations, investment_summary, goal_summary, health_score):

    highest_category = spending_analysis["highest_category"]["category"]
    highest_amount = spending_analysis["highest_category"]["amount"]
    monthly_expense = spending_analysis["total_monthly_expense"]
    monthly_change_percent = spending_analysis["monthly_change_percent"]

    summary_lines = [
        "Rule-Based Financial Intelligence generated from your actual transaction, budget, goal, and investment data.",
        f"Monthly income is Rs. {financial_snapshot['income']:,.0f} and current month spending is Rs. {monthly_expense:,.0f}.",
        f"Top expense category is {highest_category} at Rs. {highest_amount:,.0f}.",
        f"Budget utilization is {budget_recommendations['budget_utilization_percent']:.1f}% with {budget_recommendations['budget_health_status']} budget health.",
        f"Financial health score is {health_score['score']}/100 ({health_score['status']}).",
    ]

    saving_suggestions = [
        "Automate a monthly transfer into savings right after income is received.",
        "Keep non-essential categories under weekly limits to avoid month-end spikes.",
    ]
    if highest_category != "No Data" and highest_amount > 0:
        saving_suggestions.insert(0, f"Optimize {highest_category} spending; a 10% cut can save around Rs. {highest_amount * 0.1:,.0f} per month.")

    investment_suggestions = [
        "Review investment ROI monthly and rebalance assets with sustained underperformance.",
        "Direct part of monthly savings into long-term diversified investments.",
    ]
    if investment_summary["total_investment"] <= 0 and financial_snapshot["income"] > 0:
        investment_suggestions.insert(0, "Start with a small recurring investment plan to build long-term growth.")

    budget_suggestions = list(budget_recommendations["recommendations"])

    spending_tips = [
        "Track discretionary expenses weekly to catch overspending early.",
        "Bundle recurring payments and review subscriptions quarterly.",
    ]
    if monthly_change_percent > 10:
        spending_tips.insert(0, "Spending increased notably from last month; tighten variable categories this month.")
    elif monthly_change_percent < -10:
        spending_tips.insert(0, "Great reduction from last month; keep this spending discipline consistent.")

    monthly_comparison = {
        "current_month_expense": spending_analysis["total_monthly_expense"],
        "previous_month_expense": spending_analysis["previous_month_expense"],
        "change_amount": spending_analysis["monthly_change_amount"],
        "change_percent": spending_analysis["monthly_change_percent"],
    }

    if health_score["score"] >= 85:
        risk_indicator = "Low"
    elif health_score["score"] >= 70:
        risk_indicator = "Moderate"
    elif health_score["score"] >= 55:
        risk_indicator = "Elevated"
    else:
        risk_indicator = "High"

    top_opportunities = []
    if budget_recommendations["overspending_categories"]:
        top_category = budget_recommendations["overspending_categories"][0]
        top_opportunities.append(
            f"Optimize {top_category['category']} by about Rs. {top_category['excess']:,.0f} for immediate savings impact."
        )
    if health_score["savings_ratio"] < 20:
        top_opportunities.append("Increase monthly savings allocation toward a minimum 20% savings rate.")
    if investment_summary["total_investment"] <= 0:
        top_opportunities.append("Start a recurring investment contribution to improve long-term wealth growth.")
    if not top_opportunities:
        top_opportunities.append("Maintain your current discipline and continue monthly review for sustained performance.")

    return {
        "financial_summary": " ".join(summary_lines),
        "saving_suggestions": saving_suggestions,
        "investment_suggestions": investment_suggestions,
        "budget_suggestions": budget_suggestions,
        "spending_tips": spending_tips,
        "monthly_comparison": monthly_comparison,
        "goal_summary": goal_summary,
        "risk_indicator": risk_indicator,
        "top_opportunities": top_opportunities,
    }


def save_health_score_snapshot(cur, user_id, health_score):

    score_date = ist_now().strftime("%Y-%m-%d")
    cur.execute("""
        INSERT INTO health_scores(user_id, score, status, score_date)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id, score_date)
        DO UPDATE SET score=excluded.score, status=excluded.status
    """, (user_id, int(health_score["score"]), health_score["status"], score_date))


def store_recommendations_snapshot(cur, user_id, budget_recommendations, ai_insights):

    generated_on = ist_now().strftime("%Y-%m-%d")
    cur.execute("DELETE FROM recommendations WHERE user_id=? AND generated_on=?", (user_id, generated_on))

    bucketed_items = []
    for item in budget_recommendations["recommendations"][:4]:
        bucketed_items.append(("budget", item))
    for item in ai_insights["saving_suggestions"][:3]:
        bucketed_items.append(("savings", item))
    for item in ai_insights["investment_suggestions"][:3]:
        bucketed_items.append(("investment", item))

    for recommendation_type, message in bucketed_items:
        cur.execute("""
            INSERT INTO recommendations(user_id, recommendation_type, message, generated_on)
            VALUES(?,?,?,?)
        """, (user_id, recommendation_type, message, generated_on))


def get_savings_prediction(cur, user_id, financial_snapshot, spending_analysis, goal_summary):

    monthly_savings = round(financial_snapshot["income"] - spending_analysis["total_monthly_expense"], 2)
    monthly_savings_positive = max(monthly_savings, 0)

    prediction_3m = round(monthly_savings_positive * 3, 2)
    prediction_6m = round(monthly_savings_positive * 6, 2)
    prediction_12m = round(monthly_savings_positive * 12, 2)

    income = financial_snapshot["income"]
    growth_percentage = round((monthly_savings / income) * 100, 2) if income > 0 else 0

    cur.execute("""
        SELECT target_amount, saved_amount
        FROM goals
        WHERE user_id=? AND saved_amount < target_amount
        ORDER BY (target_amount - saved_amount) ASC
        LIMIT 1
    """, (user_id,))
    next_goal = cur.fetchone()

    expected_goal_achievement_date = "No active goal"
    if next_goal:
        gap = max(float(next_goal["target_amount"]) - float(next_goal["saved_amount"]), 0)
        if monthly_savings_positive > 0:
            months_to_complete = max(1, int(round(gap / monthly_savings_positive + 0.49)))
            eta_date = ist_now() + timedelta(days=months_to_complete * 30)
            expected_goal_achievement_date = eta_date.strftime("%d %b %Y")
        else:
            expected_goal_achievement_date = "Increase monthly savings to estimate"

    return {
        "current_savings": round(monthly_savings, 2),
        "prediction_3m": prediction_3m,
        "prediction_6m": prediction_6m,
        "prediction_12m": prediction_12m,
        "growth_percentage": growth_percentage,
        "expected_goal_achievement_date": expected_goal_achievement_date,
        "goal_progress": round(goal_summary["overall_progress"], 2),
    }


def get_financial_timeline(cur, user_id, monthly_income, months=6):

    month_end = ist_now().replace(day=1)
    month_keys = []
    walker = month_end
    for _ in range(months):
        month_keys.append(month_key(walker))
        walker = shift_to_previous_month(walker)
    month_keys = list(reversed(month_keys))

    placeholders = ",".join("?" for _ in month_keys)

    cur.execute(f"""
        SELECT substr(expense_date, 1, 7) AS month_value, IFNULL(SUM(amount), 0) AS total_expense
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7) IN ({placeholders})
        GROUP BY month_value
    """, [user_id, *month_keys])
    expense_map = {row["month_value"]: float(row["total_expense"]) for row in cur.fetchall()}

    cur.execute(f"""
        SELECT substr(COALESCE(purchase_date, created_at), 1, 7) AS month_value,
               IFNULL(SUM(invested_amount), 0) AS invested,
               IFNULL(SUM(current_value), 0) AS current
        FROM investments
        WHERE user_id=? AND substr(COALESCE(purchase_date, created_at), 1, 7) IN ({placeholders})
        GROUP BY month_value
    """, [user_id, *month_keys])
    investment_map = {
        row["month_value"]: {
            "invested": float(row["invested"]),
            "current": float(row["current"]),
        }
        for row in cur.fetchall()
    }

    labels = []
    health_scores = []
    income_growth = []
    expense_growth = []
    savings_growth = []
    investment_growth = []

    rolling_investment_value = 0.0
    for month_value in month_keys:
        year_part, month_part = month_value.split("-")
        labels.append(datetime(int(year_part), int(month_part), 1).strftime("%b %Y"))

        month_income = float(monthly_income)
        month_expense = expense_map.get(month_value, 0.0)
        month_savings = month_income - month_expense
        inv = investment_map.get(month_value, {"invested": 0.0, "current": 0.0})
        rolling_investment_value += inv["current"]

        savings_ratio = (month_savings / month_income) * 100 if month_income > 0 else 0
        expense_ratio = (month_expense / month_income) * 100 if month_income > 0 else 0
        monthly_health = clamp_value(round(50 + (savings_ratio * 1.1) + ((100 - min(expense_ratio, 120)) * 0.35)), 0, 100)

        health_scores.append(monthly_health)
        income_growth.append(round(month_income, 2))
        expense_growth.append(round(month_expense, 2))
        savings_growth.append(round(month_savings, 2))
        investment_growth.append(round(rolling_investment_value, 2))

    return {
        "labels": labels,
        "health_scores": health_scores,
        "income_growth": income_growth,
        "expense_growth": expense_growth,
        "savings_growth": savings_growth,
        "investment_growth": investment_growth,
    }


def get_user_achievements(cur, user_id, financial_snapshot, spending_analysis, budget_recommendations, investment_summary, goal_summary):

    cur.execute("""
        SELECT id, achievement_key, title, description, icon, target_value
        FROM achievements
        ORDER BY id ASC
    """)
    achievement_rows = cur.fetchall()

    cur.execute("""
        SELECT IFNULL(COUNT(*), 0) AS budget_count
        FROM budget
        WHERE user_id=?
    """, (user_id,))
    budget_count = int(cur.fetchone()["budget_count"] or 0)

    cur.execute("""
        SELECT IFNULL(COUNT(*), 0) AS investment_count
        FROM investments
        WHERE user_id=?
    """, (user_id,))
    investment_count = int(cur.fetchone()["investment_count"] or 0)

    cur.execute("""
        SELECT IFNULL(COUNT(*), 0) AS category_count
        FROM (
            SELECT category
            FROM expenses
            WHERE user_id=?
            GROUP BY category
        )
    """, (user_id,))
    category_count = int(cur.fetchone()["category_count"] or 0)

    savings_ratio = ((financial_snapshot["income"] - spending_analysis["total_monthly_expense"]) / financial_snapshot["income"] * 100) if financial_snapshot["income"] else 0

    progress_map = {
        "first_expense": float(spending_analysis["transaction_count"]),
        "budget_planner": float(budget_count),
        "savings_started": 1.0 if (financial_snapshot["income"] - spending_analysis["total_monthly_expense"]) > 0 else 0.0,
        "investment_added": float(investment_count),
        "goal_completed": float(goal_summary["completed_goals"]),
        "expense_tracker": float(spending_analysis["transaction_count"]),
        "transactions_30": float(spending_analysis["transaction_count"]),
        "savings_champion": float(max(0, savings_ratio)),
        "investment_expert": float(investment_summary["roi"] if investment_count >= 3 else 0),
    }

    achievements = []
    unlocked_count = 0
    recently_unlocked = []

    for row in achievement_rows:
        key = row["achievement_key"]
        target_value = float(row["target_value"] or 1)
        current_value = float(progress_map.get(key, 0))
        progress_percent = clamp_value(round((current_value / target_value) * 100), 0, 100) if target_value > 0 else 0
        unlocked = 1 if current_value >= target_value else 0

        cur.execute("""
            SELECT is_unlocked, unlocked_at
            FROM user_achievements
            WHERE user_id=? AND achievement_id=?
        """, (user_id, row["id"]))
        previous_state = cur.fetchone()
        was_unlocked = int(previous_state["is_unlocked"]) == 1 if previous_state else False

        cur.execute("""
            INSERT INTO user_achievements(user_id, achievement_id, progress, is_unlocked, unlocked_at, updated_at)
            VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, achievement_id)
            DO UPDATE SET
                progress=excluded.progress,
                is_unlocked=excluded.is_unlocked,
                unlocked_at=COALESCE(user_achievements.unlocked_at, excluded.unlocked_at),
                updated_at=CURRENT_TIMESTAMP
        """, (
            user_id,
            row["id"],
            current_value,
            unlocked,
            ist_now().strftime("%Y-%m-%d") if unlocked else None,
        ))

        cur.execute("""
            SELECT unlocked_at
            FROM user_achievements
            WHERE user_id=? AND achievement_id=?
        """, (user_id, row["id"]))
        unlock_row = cur.fetchone()

        if unlocked:
            unlocked_count += 1

        if unlocked and not was_unlocked:
            recently_unlocked.append({
                "key": key,
                "title": row["title"],
                "icon": row["icon"],
            })

        achievements.append({
            "key": key,
            "icon": row["icon"],
            "title": row["title"],
            "description": row["description"],
            "progress": progress_percent,
            "is_unlocked": unlocked,
            "unlock_date": unlock_row["unlocked_at"] if unlock_row and unlock_row["unlocked_at"] else "",
        })

    return {
        "items": achievements,
        "earned_count": unlocked_count,
        "total_count": len(achievement_rows),
        "recently_unlocked": recently_unlocked,
        "recent_achievement": recently_unlocked[0] if recently_unlocked else None,
    }


def create_achievement_notifications(cur, user_id, achievements_snapshot):

    for achievement in achievements_snapshot.get("recently_unlocked", []):
        create_notification_if_missing(
            cur,
            user_id,
            "achievement_unlocked",
            "New Achievement Unlocked",
            f"You unlocked '{achievement['title']}'. Keep the momentum going!",
            f"achievement_unlocked:{achievement['key']}",
            "success",
        )


def get_recent_activities(cur, user_id, limit=20):

    cur.execute("""
        SELECT id, activity_type, title, description, icon, related_route, created_at
        FROM activity_logs
        WHERE user_id=?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
    """, (user_id, limit))

    rows = []
    for row in cur.fetchall():
        rows.append({
            "id": row["id"],
            "activity_type": row["activity_type"],
            "title": row["title"],
            "description": row["description"],
            "icon": row["icon"] or "fa-solid fa-circle-info",
            "related_route": row["related_route"],
            "created_at": row["created_at"],
            "time_ago": format_time_ago(row["created_at"]),
        })

    return rows


def create_notification_if_missing(cur, user_id, notification_type, title, message, event_key, priority="info"):

    cur.execute("""
        SELECT id
        FROM notifications
        WHERE user_id=? AND notification_type=? AND event_key=?
        LIMIT 1
    """, (user_id, notification_type, event_key))

    if cur.fetchone():
        return

    cur.execute("""
        INSERT INTO notifications(user_id, notification_type, title, message, priority, event_key, is_read)
        VALUES(?,?,?,?,?,?,0)
    """, (user_id, notification_type, title, message, priority, event_key))


def refresh_notifications(cur, current_user, financial_snapshot, spending_analysis, budget_recommendations, goal_summary, investment_summary):

    user_id = current_user["id"]
    this_month_key = ist_now().strftime("%Y-%m")
    today_key = ist_now().strftime("%Y-%m-%d")

    if int(current_user["notify_budget_alerts"] or 0) and budget_recommendations["budget_utilization_percent"] > 100:
        create_notification_if_missing(
            cur,
            user_id,
            "budget_exceeded",
            "Budget Exceeded",
            f"You have used {budget_recommendations['budget_utilization_percent']:.1f}% of your budget this month.",
            f"budget_exceeded:{this_month_key}",
            "danger",
        )

    if int(current_user["notify_goal_alerts"] or 0) and goal_summary["active_goals"] > 0 and goal_summary["overall_progress"] < 70:
        create_notification_if_missing(
            cur,
            user_id,
            "goal_reminder",
            "Savings Goal Reminder",
            "Some goals need additional monthly contribution to stay on track.",
            f"goal_reminder:{this_month_key}",
            "warning",
        )

    if int(current_user["notify_goal_alerts"] or 0) and goal_summary["completed_goals"] > 0:
        create_notification_if_missing(
            cur,
            user_id,
            "goal_achieved",
            "Goal Achievement Unlocked",
            f"Great progress! You have completed {goal_summary['completed_goals']} financial goal(s).",
            f"goal_achieved:{this_month_key}",
            "success",
        )

    if int(current_user["notify_investment_alerts"] or 0) and investment_summary["total_investment"] > 0:
        investment_label = "gain" if investment_summary["total_profit"] >= 0 else "decline"
        create_notification_if_missing(
            cur,
            user_id,
            "investment_update",
            "Investment Update",
            f"Portfolio shows a {investment_label} of Rs. {abs(investment_summary['total_profit']):,.0f}.",
            f"investment_update:{this_month_key}",
            "info",
        )

    if int(current_user["notify_monthly_report"] or 0):
        create_notification_if_missing(
            cur,
            user_id,
            "monthly_reminder",
            "Monthly Financial Reminder",
            "Review your spending, savings, and budget recommendations this month.",
            f"monthly_reminder:{this_month_key}",
            "info",
        )

        create_notification_if_missing(
            cur,
            user_id,
            "monthly_report",
            "Monthly Report Available",
            "Your monthly financial report is ready for review and export.",
            f"monthly_report:{this_month_key}",
            "success",
        )

    monthly_savings = financial_snapshot["income"] - spending_analysis["total_monthly_expense"]
    if monthly_savings < 0:
        create_notification_if_missing(
            cur,
            user_id,
            "low_savings",
            "Low Savings Warning",
            "Current month expenses are above income. Reduce variable spending immediately.",
            f"low_savings:{this_month_key}",
            "danger",
        )
    elif monthly_savings >= (financial_snapshot["income"] * 0.2 if financial_snapshot["income"] > 0 else 0):
        create_notification_if_missing(
            cur,
            user_id,
            "savings_target",
            "Savings Target Reached",
            "You reached the recommended monthly savings target (20% of income).",
            f"savings_target:{this_month_key}",
            "success",
        )

    if spending_analysis["transaction_count"] > 0:
        create_notification_if_missing(
            cur,
            user_id,
            "recent_activity",
            "Recent Activity",
            f"You have {spending_analysis['transaction_count']} expense transactions recorded.",
            f"recent_activity:{today_key}",
            "info",
        )


def get_notification_feed(cur, user_id, limit=6):

    cur.execute("""
        SELECT COUNT(*) AS unread_count
        FROM notifications
        WHERE user_id=? AND is_read=0
    """, (user_id,))
    unread_count = int(cur.fetchone()["unread_count"])

    cur.execute("""
        SELECT id, notification_type, title, message, priority, is_read, created_at
        FROM notifications
        WHERE user_id=?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
    """, (user_id, limit))

    rows = []
    for row in cur.fetchall():
        rows.append({
            "id": row["id"],
            "notification_type": row["notification_type"],
            "title": row["title"],
            "message": row["message"],
            "priority": row["priority"] if row["priority"] else "info",
            "is_read": int(row["is_read"]),
            "created_at": row["created_at"],
            "time_ago": format_time_ago(row["created_at"]),
        })

    return unread_count, rows

# ---------------- DASHBOARD ---------------- #

@app.route("/dashboard")
def dashboard():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    user_id = session["user_id"]

    # ---------------- User Details ---------------- #

    cur.execute("""
        SELECT name, income
        FROM users
        WHERE id=?
    """, (user_id,))

    user = cur.fetchone()

    user_name = user["name"]
    income = float(user["income"])

    # ---------------- Total Expense ---------------- #

    cur.execute("""
        SELECT IFNULL(SUM(amount),0) AS total
        FROM expenses
        WHERE user_id=?
    """, (user_id,))

    expense = float(cur.fetchone()["total"])

    savings = income - expense

    # ---------------- Transaction Count ---------------- #

    cur.execute("""
        SELECT COUNT(*) AS total
        FROM expenses
        WHERE user_id=?
    """, (user_id,))

    transaction_count = cur.fetchone()["total"]

    # ---------------- Recent Expenses ---------------- #

    cur.execute("""
        SELECT category,
               amount,
               expense_date
        FROM expenses
        WHERE user_id=?
        ORDER BY expense_date DESC
        LIMIT 5
    """, (user_id,))

    recent_expenses = cur.fetchall()

    # ---------------- Monthly Bar Chart ---------------- #

    cur.execute("""
        SELECT
            substr(expense_date,6,2) AS month,
            SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
        GROUP BY month
        ORDER BY month
    """, (user_id,))

    chart = cur.fetchall()

    month_names = {
        "01":"Jan",
        "02":"Feb",
        "03":"Mar",
        "04":"Apr",
        "05":"May",
        "06":"Jun",
        "07":"Jul",
        "08":"Aug",
        "09":"Sep",
        "10":"Oct",
        "11":"Nov",
        "12":"Dec"
    }

    labels = []
    values = []

    for row in chart:
        labels.append(month_names.get(row["month"], row["month"]))
        values.append(float(row["total"]))

    # ---------------- Pie Chart ---------------- #

    cur.execute("""
        SELECT category,
               SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
        GROUP BY category
        ORDER BY total DESC
    """, (user_id,))

    pie_chart = cur.fetchall()

    pie_labels = []
    pie_values = []

    for row in pie_chart:
        pie_labels.append(row["category"])
        pie_values.append(float(row["total"]))

    # ---------------- Budget ---------------- #

    cur.execute("""
        SELECT monthly_budget
        FROM budget
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))

    budget = cur.fetchone()

    if budget:
        monthly_budget = float(budget["monthly_budget"])
    else:
        monthly_budget = income

    if monthly_budget > 0:
        budget_percent = round((expense / monthly_budget) * 100)
    else:
        budget_percent = 0

    # ---------------- Greeting ---------------- #

    now = ist_now()

    today = now.strftime("%d %B %Y")
    current_time = now.strftime("%I:%M %p")

    hour = now.hour

    if hour < 12:
        greeting = "Good Morning"
    elif hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    # ---------------- Income vs Expense Chart ---------------- #

    income_chart = [income for _ in labels]
    expense_chart = values

    # ---------------- Dashboard Preferences ---------------- #

    cur.execute("""
        SELECT hidden_cards, card_order
        FROM dashboard_preferences
        WHERE user_id=?
        LIMIT 1
    """, (user_id,))
    pref_row = cur.fetchone()

    hidden_cards, card_order = parse_dashboard_preferences(
        pref_row["hidden_cards"] if pref_row else "",
        pref_row["card_order"] if pref_row else "",
    )

    all_cards = [
        {"key": "income", "title": "Total Income", "icon": "fa-solid fa-wallet", "value": f"₹ {income:,.0f}"},
        {"key": "expense", "title": "Total Expense", "icon": "fa-solid fa-money-bill-wave", "value": f"₹ {expense:,.0f}"},
        {"key": "savings", "title": "Savings", "icon": "fa-solid fa-piggy-bank", "value": f"₹ {savings:,.0f}"},
        {"key": "transactions", "title": "Transactions", "icon": "fa-solid fa-file-invoice", "value": str(transaction_count)},
    ]

    card_map = {card["key"]: card for card in all_cards}
    ordered_keys = [key for key in card_order if key in card_map]
    for key in card_map:
        if key not in ordered_keys:
            ordered_keys.append(key)

    dashboard_cards = [card_map[key] for key in ordered_keys if key not in hidden_cards]

    # ---------------- Number Formatting ---------------- #

    income = f"{income:,.0f}"
    expense = f"{expense:,.0f}"
    savings = f"{savings:,.0f}"

    conn.close()

    return render_template(

        "dashboard.html",

        user_name=user_name,

        income=income,

        expense=expense,

        savings=savings,

        dashboard_cards=dashboard_cards,

        dashboard_hidden_cards=hidden_cards,

        dashboard_card_order=ordered_keys,

        transaction_count=transaction_count,

        recent_expenses=recent_expenses,

        labels=labels,

        values=values,

        pie_labels=pie_labels,

        pie_values=pie_values,

        income_chart=income_chart,

        expense_chart=expense_chart,

        budget_percent=budget_percent,

        today=today,

        current_time=current_time,

        greeting=greeting

    )
    # ---------------- INCOME ---------------- #

@app.route("/income")
def income():

    if not login_required():
        return redirect("/login")

    conn = get_db()

    cur = conn.cursor()

    cur.execute("""

    SELECT income

    FROM users

    WHERE id=?

    """,(session["user_id"],))

    income = cur.fetchone()["income"]

    conn.close()

    return render_template(

        "income.html",

        income=income

    )

@app.route("/update_income",methods=["POST"])
def update_income():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/income")

    income=request.form["income"]

    conn=get_db()

    cur=conn.cursor()

    cur.execute("""

    UPDATE users

    SET income=?

    WHERE id=?

    """,(income,session["user_id"]))

    log_activity(
        cur,
        session["user_id"],
        "income_added",
        "Income Added",
        f"Monthly income updated to Rs. {safe_float(income):,.0f}.",
        "fa-solid fa-wallet",
        "/income",
    )

    create_notification_if_missing(
        cur,
        session["user_id"],
        "income_updated",
        "Income Updated",
        f"Income has been updated to Rs. {safe_float(income):,.0f}.",
                f"income_updated:{ist_now().strftime('%Y-%m-%d')}",
        "success",
    )

    conn.commit()

    conn.close()

    flash("Income Updated Successfully")

    return redirect("/income")

# ---------------- EXPENSE ---------------- #

@app.route("/expense")
def expense():

    if not login_required():
        return redirect("/login")

    return render_template("expense.html")

@app.route("/add_expense",methods=["POST"])
def add_expense():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/expense")

    category=request.form["category"]

    amount=request.form["amount"]

    description=request.form["description"]

    expense_date=request.form["expense_date"]

    conn=get_db()

    cur=conn.cursor()

    cur.execute("""

    INSERT INTO expenses(

    user_id,

    category,

    amount,

    description,

    expense_date

    )

    VALUES(?,?,?,?,?)

    """,(

    session["user_id"],

    category,

    amount,

    description,

    expense_date

    ))

    log_activity(
        cur,
        session["user_id"],
        "expense_added",
        "Expense Added",
        f"{category} expense of Rs. {safe_float(amount):,.0f} added.",
        "fa-solid fa-money-bill-wave",
        "/transactions",
    )

    create_notification_if_missing(
        cur,
        session["user_id"],
        "expense_added",
        "Expense Recorded",
        f"New expense in {category} for Rs. {safe_float(amount):,.0f} was recorded.",
                f"expense_added:{ist_now().strftime('%Y-%m-%d')}:{category}:{safe_float(amount):.2f}",
        "info",
    )

    conn.commit()

    conn.close()

    flash("Expense Added Successfully")

    return redirect("/dashboard")

# ---------------- BUDGET ---------------- #

@app.route("/budget")
def budget():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    SELECT monthly_budget
    FROM budget
    WHERE user_id=?
    ORDER BY id DESC
    LIMIT 1
    """,(session["user_id"],))

    row = cur.fetchone()

    if row:
        monthly_budget = row["monthly_budget"]
    else:
        monthly_budget = 0

    # Total Expense
    cur.execute("""
    SELECT IFNULL(SUM(amount),0) AS total
    FROM expenses
    WHERE user_id=?
    """, (session["user_id"],))

    expense = float(cur.fetchone()["total"])

    remaining_budget = monthly_budget - expense

    if remaining_budget < 0:
        remaining_budget = 0

    if expense <= monthly_budget:
        budget_status = "✅ Within Budget "
    else:
        budget_status = "⚠ Budget Exceeded "

    if monthly_budget > 0:
        budget_percent = round((expense / monthly_budget) * 100)
    else:
        budget_percent = 0

    conn.close()

    return render_template(
        
        "budget.html",
        monthly_budget=monthly_budget,
        expense=expense,
        remaining_budget=remaining_budget,
        budget_status=budget_status,
        budget_percent=budget_percent
    )

@app.route("/save_budget",methods=["POST"])
def save_budget():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/budget")

    budget=request.form["budget"]

    month=request.form["month"]

    conn=get_db()

    cur=conn.cursor()

    cur.execute("""

    INSERT INTO budget(

    user_id,

    monthly_budget,

    month

    )

    VALUES(?,?,?)

    """,(

    session["user_id"],

    budget,

    month

    ))

    log_activity(
        cur,
        session["user_id"],
        "budget_updated",
        "Budget Updated",
        f"Budget set to Rs. {safe_float(budget):,.0f} for {month}.",
        "fa-solid fa-chart-pie",
        "/budget",
    )

    create_notification_if_missing(
        cur,
        session["user_id"],
        "budget_updated",
        "Budget Updated",
        f"Monthly budget for {month} is now Rs. {safe_float(budget):,.0f}.",
        f"budget_updated:{month}",
        "success",
    )

    cur.execute("SELECT IFNULL(SUM(amount), 0) AS total_expense FROM expenses WHERE user_id=?", (session["user_id"],))
    total_expense = float(cur.fetchone()["total_expense"])
    budget_value = safe_float(budget)
    budget_percent = round((total_expense / budget_value) * 100, 2) if budget_value > 0 else 0

    if budget_value > 0 and total_expense > budget_value:
        user = get_user_by_id(session["user_id"])
        if user and int(user["notify_budget_alerts"] or 0):
            send_budget_exceeded_email(
                user["name"],
                user["email"],
                month,
                total_expense,
                budget_value,
                budget_percent,
            )

    conn.commit()

    conn.close()

    flash("Budget Saved Successfully")

    return redirect("/budget")

# ---------------- TRANSACTIONS ---------------- #

@app.route("/transactions")
def transactions():

    if not login_required():
        return redirect("/login")

    conn=get_db()

    cur=conn.cursor()

    cur.execute("""

    SELECT *

    FROM expenses

    WHERE user_id=?

    ORDER BY expense_date DESC

    """,(session["user_id"],))

    expenses=cur.fetchall()

    conn.close()

    return render_template(

        "transactions.html",

        expenses=expenses

    )


@app.route("/dashboard/preferences", methods=["POST"])
def save_dashboard_preferences():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/dashboard")

    hidden_cards = request.form.getlist("hidden_cards")
    card_order = request.form.get("card_order", "")

    conn = get_db()
    cur = conn.cursor()

    hidden_value = ",".join([item.strip() for item in hidden_cards if item.strip()])
    order_value = ",".join([item.strip() for item in card_order.split(",") if item.strip()])

    cur.execute("""
        INSERT INTO dashboard_preferences(user_id, hidden_cards, card_order, updated_at)
        VALUES(?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(user_id)
        DO UPDATE SET hidden_cards=excluded.hidden_cards, card_order=excluded.card_order, updated_at=CURRENT_TIMESTAMP
    """, (session["user_id"], hidden_value, order_value))

    log_activity(
        cur,
        session["user_id"],
        "dashboard_customized",
        "Dashboard Customized",
        "Dashboard card visibility and order preferences were updated.",
        "fa-solid fa-sliders",
        "/dashboard",
    )

    conn.commit()
    conn.close()

    flash("Dashboard preferences saved.", "success")
    return redirect("/dashboard")


@app.route("/theme-mode", methods=["POST"])
def save_theme_mode():

    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if not validate_csrf_token():
        return jsonify({"ok": False, "error": "invalid_csrf"}), 400

    theme_mode = (request.form.get("theme_mode") or "").strip().lower()
    if theme_mode not in {"light", "dark"}:
        return jsonify({"ok": False, "error": "invalid_theme"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET theme_mode=? WHERE id=?", (theme_mode, session["user_id"]))
    conn.commit()
    conn.close()

    session["theme_mode"] = theme_mode
    return jsonify({"ok": True, "theme_mode": theme_mode})

@app.route("/edit_expense/<int:id>",methods=["GET","POST"])
def edit_expense(id):

    if not login_required():
        return redirect("/login")

    conn=get_db()

    cur=conn.cursor()

    if request.method=="POST":

        if not validate_csrf_token():
            conn.close()
            flash("Invalid security token. Please try again.", "danger")
            return redirect(f"/edit_expense/{id}")

        category=request.form["category"]

        amount=request.form["amount"]

        description=request.form["description"]

        expense_date=request.form["expense_date"]

        cur.execute("""

        UPDATE expenses

        SET

        category=?,

        amount=?,

        description=?,

        expense_date=?

        WHERE id=? AND user_id=?

        """,(

        category,

        amount,

        description,

        expense_date,

        id,

        session["user_id"]

        ))

        conn.commit()

        conn.close()

        flash("Expense Updated Successfully")

        return redirect("/transactions")

    cur.execute("""

    SELECT *

    FROM expenses

    WHERE id=? AND user_id=?

    """,(id, session["user_id"]))

    expense=cur.fetchone()

    conn.close()

    return render_template(

        "edit_expense.html",

        expense=expense

    )

@app.route("/delete_expense/<int:id>", methods=["POST"])
def delete_expense(id):

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/transactions")

    conn=get_db()

    cur=conn.cursor()

    cur.execute("""

    DELETE FROM expenses

    WHERE id=? AND user_id=?

    """,(id, session["user_id"]))

    conn.commit()

    conn.close()

    flash("Expense Deleted Successfully")

    return redirect("/transactions")

@app.route("/search")
def search():

    if not login_required():
        return redirect("/login")

    keyword=request.args.get("keyword","")

    conn=get_db()

    cur=conn.cursor()

    cur.execute("""

    SELECT *

    FROM expenses

    WHERE

    user_id=?

    AND

    category LIKE ?

    ORDER BY expense_date DESC

    """,(

    session["user_id"],

    "%"+keyword+"%"

    ))

    expenses=cur.fetchall()

    conn.close()

    return render_template(

        "transactions.html",

        expenses=expenses

    )

@app.route("/filter/<category>")
def filter_category(category):

    if not login_required():
        return redirect("/login")

    conn=get_db()

    cur=conn.cursor()

    cur.execute("""

    SELECT *

    FROM expenses

    WHERE

    user_id=?

    AND

    category=?

    ORDER BY expense_date DESC

    """,(

    session["user_id"],

    category

    ))

    expenses=cur.fetchall()

    conn.close()

    return render_template(

        "transactions.html",

        expenses=expenses

    )


@app.route("/activities")
def activities():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    activity_rows = get_recent_activities(cur, session["user_id"], limit=120)
    conn.close()

    return render_template("activities.html", activities=activity_rows)


@app.route("/feedback", methods=["GET", "POST"])
def feedback():

    if not login_required():
        return redirect("/login")

    user_id = session["user_id"]

    conn = get_db()
    try:
        cur = conn.cursor()

        if request.method == "POST":
            if not validate_csrf_token():
                flash("Invalid security token. Please try again.", "danger")
                return redirect("/feedback")

            rating_raw = (request.form.get("rating") or "").strip()
            comment = (request.form.get("comment") or "").strip()

            if not rating_raw.isdigit():
                flash("Please select a rating between 1 and 5 stars.", "danger")
                return redirect("/feedback")

            rating = int(rating_raw)
            if rating < 1 or rating > 5:
                flash("Rating must be between 1 and 5 stars.", "danger")
                return redirect("/feedback")

            if len(comment) > 1500:
                flash("Feedback comment is too long. Please keep it under 1500 characters.", "danger")
                return redirect("/feedback")

            cur.execute("""
                INSERT INTO feedback(user_id, rating, comment)
                VALUES(?,?,?)
            """, (user_id, rating, comment or None))

            log_activity(
                cur,
                user_id,
                "feedback_submitted",
                "Feedback Submitted",
                f"Submitted a {rating}-star application feedback entry.",
                "fa-solid fa-star",
                "/feedback",
            )

            conn.commit()
            flash("Thank you for your feedback. Your response has been submitted successfully.", "success")
            return redirect("/feedback")

        cur.execute("""
            SELECT rating, comment, created_at
            FROM feedback
            WHERE user_id=?
            ORDER BY datetime(substr(replace(created_at, 'T', ' '), 1, 19)) DESC, id DESC
            LIMIT 6
        """, (user_id,))
        user_feedback_rows = cur.fetchall()
    finally:
        conn.close()

    return render_template("feedback.html", user_feedback_rows=user_feedback_rows)


@app.route("/financial-calendar")
def financial_calendar():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    events = []

    cur.execute("""
        SELECT expense_date, category, amount
        FROM expenses
        WHERE user_id=? AND expense_date IS NOT NULL AND expense_date != ''
    """, (user_id,))
    for row in cur.fetchall():
        events.append({
            "title": f"Expense: {row['category']} (Rs. {safe_float(row['amount']):,.0f})",
            "start": row["expense_date"],
            "color": "#ef4444",
            "eventType": "expense",
        })

    cur.execute("""
        SELECT created_at, description
        FROM activity_logs
        WHERE user_id=? AND activity_type='income_added'
        ORDER BY datetime(created_at) DESC
    """, (user_id,))
    for row in cur.fetchall():
        income_date = str(row["created_at"])[:10]
        events.append({
            "title": "Income Updated",
            "start": income_date,
            "color": "#22c55e",
            "eventType": "income",
        })

    cur.execute("""
        SELECT month, monthly_budget
        FROM budget
        WHERE user_id=? AND month IS NOT NULL AND month != ''
    """, (user_id,))
    for row in cur.fetchall():
        events.append({
            "title": f"Budget Month ({row['month']}) - Rs. {safe_float(row['monthly_budget']):,.0f}",
            "start": f"{row['month']}-01" if len(str(row["month"])) == 7 else row["month"],
            "color": "#2563eb",
            "eventType": "budget",
        })

    cur.execute("""
        SELECT purchase_date, investment_name, invested_amount
        FROM investments
        WHERE user_id=? AND purchase_date IS NOT NULL AND purchase_date != ''
    """, (user_id,))
    for row in cur.fetchall():
        events.append({
            "title": f"Investment: {row['investment_name']} (Rs. {safe_float(row['invested_amount']):,.0f})",
            "start": row["purchase_date"],
            "color": "#14b8a6",
            "eventType": "investment",
        })

    cur.execute("""
        SELECT target_date, goal_name, target_amount
        FROM goals
        WHERE user_id=? AND target_date IS NOT NULL AND target_date != ''
    """, (user_id,))
    for row in cur.fetchall():
        events.append({
            "title": f"Goal Deadline: {row['goal_name']} (Rs. {safe_float(row['target_amount']):,.0f})",
            "start": row["target_date"],
            "color": "#f59e0b",
            "eventType": "goal",
        })

    cur.execute("""
        SELECT reminder_date, title, reminder_type
        FROM reminders
        WHERE user_id=? AND is_completed=0
        ORDER BY reminder_date ASC
    """, (user_id,))
    for row in cur.fetchall():
        events.append({
            "title": f"Reminder: {row['title']}",
            "start": row["reminder_date"],
            "color": "#7c3aed",
            "eventType": row["reminder_type"],
        })

    cur.execute("""
        SELECT created_at
        FROM activity_logs
        WHERE user_id=? AND activity_type='income_added'
        ORDER BY datetime(created_at) DESC
    """, (user_id,))
    for row in cur.fetchall():
        event_date = (row["created_at"] or "")[:10]
        if event_date:
            events.append({
                "title": "Income Update",
                "start": event_date,
                "color": "#16a34a",
                "eventType": "income",
            })

    cur.execute("""
        SELECT created_at, activity_type
        FROM activity_logs
        WHERE user_id=? AND activity_type IN ('pdf_generated', 'csv_exported')
        ORDER BY datetime(created_at) DESC
    """, (user_id,))
    for row in cur.fetchall():
        event_date = (row["created_at"] or "")[:10]
        if event_date:
            events.append({
                "title": "Report Generated" if row["activity_type"] == "pdf_generated" else "CSV Export Generated",
                "start": event_date,
                "color": "#0f766e",
                "eventType": "report",
            })

    conn.close()

    return render_template("financial_calendar.html", calendar_events=events)


@app.route("/global-search")
def global_search():

    if not login_required():
        return redirect("/login")

    query_text = request.args.get("q", "").strip()
    results = []

    if query_text:
        conn = get_db()
        cur = conn.cursor()
        user_id = session["user_id"]
        like_value = f"%{query_text}%"

        cur.execute("""
            SELECT id, category, amount, expense_date
            FROM expenses
            WHERE user_id=? AND (category LIKE ? OR IFNULL(description, '') LIKE ?)
            ORDER BY expense_date DESC, id DESC
            LIMIT 8
        """, (user_id, like_value, like_value))
        for row in cur.fetchall():
            results.append({
                "type": "Expense",
                "title": f"{row['category']} - Rs. {safe_float(row['amount']):,.0f}",
                "description": f"Date: {row['expense_date']}",
                "icon": "fa-solid fa-money-bill-wave",
                "url": "/transactions",
            })

        cur.execute("""
            SELECT income
            FROM users
            WHERE id=?
        """, (user_id,))
        income_row = cur.fetchone()
        if income_row and (query_text.lower() in "income" or query_text.lower() in str(int(safe_float(income_row["income"], 0)))):
            results.append({
                "type": "Income",
                "title": f"Monthly Income - Rs. {safe_float(income_row['income']):,.0f}",
                "description": "Open income settings and update monthly income.",
                "icon": "fa-solid fa-wallet",
                "url": "/income",
            })

        cur.execute("""
            SELECT monthly_budget, month
            FROM budget
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,))
        budget_row = cur.fetchone()
        if budget_row and (query_text.lower() in "budget" or query_text.lower() in str(budget_row["month"] or "").lower()):
            results.append({
                "type": "Budget",
                "title": f"Monthly Budget - Rs. {safe_float(budget_row['monthly_budget']):,.0f}",
                "description": f"Configured month: {budget_row['month'] or 'N/A'}",
                "icon": "fa-solid fa-chart-pie",
                "url": "/budget",
            })

        cur.execute("""
            SELECT goal_name, target_amount, saved_amount
            FROM goals
            WHERE user_id=? AND goal_name LIKE ?
            ORDER BY id DESC
            LIMIT 8
        """, (user_id, like_value))
        for row in cur.fetchall():
            results.append({
                "type": "Goal",
                "title": row["goal_name"],
                "description": f"Saved Rs. {safe_float(row['saved_amount']):,.0f} / Rs. {safe_float(row['target_amount']):,.0f}",
                "icon": "fa-solid fa-bullseye",
                "url": "/financial-goals",
            })

        cur.execute("""
            SELECT investment_name, asset, invested_amount
            FROM investments
            WHERE user_id=? AND (investment_name LIKE ? OR asset LIKE ?)
            ORDER BY id DESC
            LIMIT 8
        """, (user_id, like_value, like_value))
        for row in cur.fetchall():
            results.append({
                "type": "Investment",
                "title": row["investment_name"],
                "description": f"{row['asset']} - Rs. {safe_float(row['invested_amount']):,.0f}",
                "icon": "fa-solid fa-chart-line",
                "url": "/investment",
            })

        cur.execute("""
            SELECT title, message, priority
            FROM notifications
            WHERE user_id=? AND (title LIKE ? OR message LIKE ?)
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 8
        """, (user_id, like_value, like_value))
        for row in cur.fetchall():
            results.append({
                "type": "Notification",
                "title": row["title"],
                "description": row["message"],
                "icon": "fa-regular fa-bell",
                "url": "/notifications",
                "priority": row["priority"],
            })

        cur.execute("""
            SELECT a.title, ua.progress, ua.is_unlocked
            FROM user_achievements ua
            JOIN achievements a ON a.id=ua.achievement_id
            WHERE ua.user_id=? AND a.title LIKE ?
            ORDER BY ua.updated_at DESC
            LIMIT 8
        """, (user_id, like_value))
        for row in cur.fetchall():
            state = "Unlocked" if int(row["is_unlocked"]) else "In Progress"
            results.append({
                "type": "Achievement",
                "title": row["title"],
                "description": f"{state} - {round(safe_float(row['progress']), 2)}%",
                "icon": "fa-solid fa-award",
                "url": "/profile",
            })

        if "report" in query_text.lower():
            results.append({
                "type": "Report",
                "title": "Financial Reports",
                "description": "Open professional reports and exports.",
                "icon": "fa-solid fa-file-lines",
                "url": "/reports",
            })

        if "profile" in query_text.lower() or "account" in query_text.lower():
            results.append({
                "type": "Profile",
                "title": "Profile & Account Settings",
                "description": "View profile analytics and update account preferences.",
                "icon": "fa-solid fa-user",
                "url": "/profile",
            })

        route_catalog = [
            ("Dashboard", "/dashboard", "fa-solid fa-house"),
            ("Income", "/income", "fa-solid fa-wallet"),
            ("Expense", "/expense", "fa-solid fa-money-bill-wave"),
            ("Budget", "/budget", "fa-solid fa-chart-pie"),
            ("Reports", "/reports", "fa-solid fa-file-lines"),
            ("Investments", "/investment", "fa-solid fa-chart-line"),
            ("Goals", "/financial-goals", "fa-solid fa-bullseye"),
            ("Notifications", "/notifications", "fa-regular fa-bell"),
            ("Profile", "/profile", "fa-solid fa-user"),
            ("Settings", "/account-settings", "fa-solid fa-sliders"),
        ]

        for route_name, route_url, route_icon in route_catalog:
            if query_text.lower() in route_name.lower():
                results.append({
                    "type": "Route",
                    "title": route_name,
                    "description": f"Navigate to {route_name} page.",
                    "icon": route_icon,
                    "url": route_url,
                })

        conn.close()

    return render_template("global_search.html", query_text=query_text, results=results)


@app.route("/data-tools", methods=["GET", "POST"])
def data_tools():

    if not login_required():
        return redirect("/login")

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/data-tools")

        action = request.form.get("action", "").strip()
        uploaded_file = request.files.get("data_file")

        conn = get_db()
        cur = conn.cursor()
        user_id = session["user_id"]

        if action == "import_csv":
            if not uploaded_file or not uploaded_file.filename.lower().endswith(".csv"):
                conn.close()
                flash("Please upload a valid CSV file.", "danger")
                return redirect("/data-tools")

            decoded = uploaded_file.read().decode("utf-8", errors="ignore")
            reader = csv.DictReader(StringIO(decoded))
            inserted_count = 0

            for row in reader:
                category = (row.get("category") or row.get("Category") or "Others").strip()
                amount = safe_float(row.get("amount") or row.get("Amount"), 0)
                description = (row.get("description") or row.get("Description") or "").strip()
                expense_date = (row.get("expense_date") or row.get("Expense Date") or ist_now().strftime("%Y-%m-%d")).strip()

                if amount <= 0:
                    continue

                cur.execute("""
                    INSERT INTO expenses(user_id, category, amount, description, expense_date)
                    VALUES(?,?,?,?,?)
                """, (user_id, category, amount, description, expense_date))
                inserted_count += 1

            log_activity(
                cur,
                user_id,
                "data_imported",
                "CSV Imported",
                f"Imported {inserted_count} expense records from CSV.",
                "fa-solid fa-file-import",
                "/data-tools",
            )
            conn.commit()
            conn.close()
            flash(f"CSV import completed. {inserted_count} rows imported.", "success")
            return redirect("/data-tools")

        if action == "import_excel":
            if load_workbook is None:
                conn.close()
                flash("Excel import requires openpyxl. Please install dependency.", "danger")
                return redirect("/data-tools")

            if not uploaded_file or not uploaded_file.filename.lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
                conn.close()
                flash("Please upload a valid Excel file.", "danger")
                return redirect("/data-tools")

            wb = load_workbook(uploaded_file, data_only=True)
            ws = wb.active
            header = [str(cell.value).strip() if cell.value is not None else "" for cell in ws[1]]
            header_map = {name.lower(): index for index, name in enumerate(header)}

            def col_value(row_values, key, fallback=""):
                idx = header_map.get(key.lower())
                if idx is None or idx >= len(row_values):
                    return fallback
                return row_values[idx] if row_values[idx] is not None else fallback

            inserted_count = 0
            for excel_row in ws.iter_rows(min_row=2, values_only=True):
                row_values = list(excel_row)
                category = str(col_value(row_values, "category", "Others")).strip() or "Others"
                amount = safe_float(col_value(row_values, "amount", 0), 0)
                description = str(col_value(row_values, "description", "")).strip()
                expense_date = str(col_value(row_values, "expense_date", ist_now().strftime("%Y-%m-%d"))).strip()

                if amount <= 0:
                    continue

                cur.execute("""
                    INSERT INTO expenses(user_id, category, amount, description, expense_date)
                    VALUES(?,?,?,?,?)
                """, (user_id, category, amount, description, expense_date))
                inserted_count += 1

            log_activity(
                cur,
                user_id,
                "data_imported",
                "Excel Imported",
                f"Imported {inserted_count} expense records from Excel.",
                "fa-solid fa-file-excel",
                "/data-tools",
            )
            conn.commit()
            conn.close()
            flash(f"Excel import completed. {inserted_count} rows imported.", "success")
            return redirect("/data-tools")

        if action == "restore_backup":
            if not uploaded_file or not uploaded_file.filename.lower().endswith(".db"):
                conn.close()
                flash("Please upload a valid SQLite backup (.db).", "danger")
                return redirect("/data-tools")

            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            tmp_file_path = tmp_file.name
            tmp_file.close()
            try:
                uploaded_file.save(tmp_file_path)

                # Close the request-scoped connection before opening a dedicated
                # restore target connection to the same SQLite file on Windows.
                conn.close()
                conn = None

                with sqlite3.connect(tmp_file_path, timeout=30) as source:
                    with sqlite3.connect(DATABASE, timeout=30) as destination:
                        source.backup(destination)

                conn = get_db()
                cur = conn.cursor()
                log_activity(
                    cur,
                    user_id,
                    "backup_restored",
                    "Database Restored",
                    "SQLite backup was restored successfully.",
                    "fa-solid fa-database",
                    "/data-tools",
                )
                conn.commit()
                conn.close()
                conn = None
            finally:
                if conn is not None:
                    conn.close()
                try:
                    os.unlink(tmp_file_path)
                except OSError:
                    pass

            flash("Backup restored successfully.", "success")
            return redirect("/data-tools")

        conn.close()
        flash("Unsupported data tool action.", "danger")
        return redirect("/data-tools")

    return render_template("data_tools.html")


@app.route("/export_json")
def export_json():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    cur.execute("SELECT category, amount, IFNULL(description, '') AS description, expense_date FROM expenses WHERE user_id=? ORDER BY expense_date DESC", (user_id,))
    expenses = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT asset, investment_name, invested_amount, current_value, purchase_date FROM investments WHERE user_id=? ORDER BY id DESC", (user_id,))
    investments = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT goal_name, target_amount, saved_amount, target_date FROM goals WHERE user_id=? ORDER BY id DESC", (user_id,))
    goals = [dict(row) for row in cur.fetchall()]

    payload = {
        "exported_at": ist_now().isoformat(),
        "user_id": user_id,
        "expenses": expenses,
        "investments": investments,
        "goals": goals,
    }

    log_activity(
        cur,
        user_id,
        "json_exported",
        "JSON Backup Exported",
        "JSON backup export generated successfully.",
        "fa-solid fa-file-code",
        "/data-tools",
    )
    conn.commit()
    conn.close()

    response = make_response(json.dumps(payload, indent=2))
    response.headers["Content-Type"] = "application/json"
    response.headers["Content-Disposition"] = f"attachment; filename=smart_finance_backup_{ist_now().strftime('%Y%m%d_%H%M%S')}.json"
    return response


@app.route("/backup_sqlite")
def backup_sqlite():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    log_activity(
        cur,
        session["user_id"],
        "sqlite_backup",
        "SQLite Backup Exported",
        "SQLite database backup was downloaded.",
        "fa-solid fa-database",
        "/data-tools",
    )
    conn.commit()
    conn.close()

    backup_name = f"smart_finance_sqlite_backup_{ist_now().strftime('%Y%m%d_%H%M%S')}.db"
    return send_file(DATABASE, as_attachment=True, download_name=backup_name, mimetype="application/octet-stream")
    
# ---------------- AI INSIGHTS ---------------- #

@app.route("/analysis")
def analysis():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    financial_snapshot = get_financial_snapshot(cur, user_id)
    spending_analysis = get_spending_analysis(cur, user_id)
    investment_summary = get_investment_summary(cur, user_id)
    goal_summary = get_goal_summary(cur, user_id)
    budget_recommendations = get_budget_recommendations(
        cur,
        user_id,
        financial_snapshot["income"],
        spending_analysis,
        financial_snapshot,
        investment_summary,
        goal_summary,
    )
    health_score = get_financial_health_score(
        financial_snapshot["income"],
        spending_analysis,
        investment_summary,
        budget_recommendations,
        goal_summary,
    )
    ai_insights = build_rule_based_ai_insights(
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        investment_summary,
        goal_summary,
        health_score,
    )
    savings_prediction = get_savings_prediction(cur, user_id, financial_snapshot, spending_analysis, goal_summary)
    timeline = get_financial_timeline(cur, user_id, financial_snapshot["income"], months=6)
    achievements = get_user_achievements(
        cur,
        user_id,
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        investment_summary,
        goal_summary,
    )
    create_achievement_notifications(cur, user_id, achievements)

    save_health_score_snapshot(cur, user_id, health_score)
    store_recommendations_snapshot(cur, user_id, budget_recommendations, ai_insights)

    refresh_notifications(
        cur,
        get_user_by_id(user_id),
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        goal_summary,
        investment_summary,
    )

    conn.commit()
    conn.close()

    percentage = round((spending_analysis["total_monthly_expense"] / financial_snapshot["income"]) * 100) if financial_snapshot["income"] > 0 else 0

    if percentage >= 80:
        advice = "Warning! Your expenses are very high."
    elif percentage >= 60:
        advice = "You should reduce unnecessary expenses."
    else:
        advice = "Great! Your spending is under control."

    return render_template(
        "ai_insights.html",
        income=financial_snapshot["income"],
        expense=spending_analysis["total_monthly_expense"],
        savings=health_score["monthly_savings"],
        percentage=percentage,
        highest_category=spending_analysis["highest_category"]["category"],
        highest_amount=spending_analysis["highest_category"]["amount"],
        advice=advice,
        financial_summary=ai_insights["financial_summary"],
        saving_suggestions=ai_insights["saving_suggestions"],
        investment_suggestions=ai_insights["investment_suggestions"],
        budget_suggestions=ai_insights["budget_suggestions"],
        spending_tips=ai_insights["spending_tips"],
        monthly_comparison=ai_insights["monthly_comparison"],
        risk_indicator=ai_insights["risk_indicator"],
        top_opportunities=ai_insights["top_opportunities"],
        health_explanation=health_score["explanation"],
        improvement_tips=health_score["improvement_tips"],
        savings_prediction=savings_prediction,
        timeline=timeline,
        achievements=achievements,
    )


@app.route("/intelligence-dashboard")
def intelligence_dashboard():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]
    current_user = get_user_by_id(user_id)

    financial_snapshot = get_financial_snapshot(cur, user_id)
    spending_analysis = get_spending_analysis(cur, user_id)
    investment_summary = get_investment_summary(cur, user_id)
    goal_summary = get_goal_summary(cur, user_id)
    budget_recommendations = get_budget_recommendations(
        cur,
        user_id,
        financial_snapshot["income"],
        spending_analysis,
        financial_snapshot,
        investment_summary,
        goal_summary,
    )
    health_score = get_financial_health_score(
        financial_snapshot["income"],
        spending_analysis,
        investment_summary,
        budget_recommendations,
        goal_summary,
    )
    ai_insights = build_rule_based_ai_insights(
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        investment_summary,
        goal_summary,
        health_score,
    )
    savings_prediction = get_savings_prediction(cur, user_id, financial_snapshot, spending_analysis, goal_summary)
    timeline = get_financial_timeline(cur, user_id, financial_snapshot["income"], months=6)
    achievements = get_user_achievements(
        cur,
        user_id,
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        investment_summary,
        goal_summary,
    )
    create_achievement_notifications(cur, user_id, achievements)

    save_health_score_snapshot(cur, user_id, health_score)
    store_recommendations_snapshot(cur, user_id, budget_recommendations, ai_insights)

    cur.execute("""
        SELECT asset, IFNULL(SUM(invested_amount), 0) AS invested_total
        FROM investments
        WHERE user_id=?
        GROUP BY asset
        ORDER BY invested_total DESC
    """, (user_id,))
    portfolio_rows = cur.fetchall()
    portfolio_labels = [row["asset"] for row in portfolio_rows]
    portfolio_values = [float(row["invested_total"]) for row in portfolio_rows]

    savings_trend = [round(financial_snapshot["income"] - value, 2) for value in spending_analysis["monthly_trend_values"]]
    income_trend = [financial_snapshot["income"] for _ in spending_analysis["monthly_trend_labels"]]

    cur.execute("""
        SELECT 'Expense' AS activity_type,
               category AS title,
               amount AS amount,
               expense_date AS activity_date
        FROM expenses
        WHERE user_id=?
        UNION ALL
        SELECT 'Investment' AS activity_type,
               investment_name AS title,
               current_value AS amount,
               COALESCE(purchase_date, substr(created_at, 1, 10)) AS activity_date
        FROM investments
        WHERE user_id=?
        UNION ALL
        SELECT 'Goal' AS activity_type,
               goal_name AS title,
               saved_amount AS amount,
               target_date AS activity_date
        FROM goals
        WHERE user_id=?
        ORDER BY activity_date DESC
        LIMIT 10
    """, (user_id, user_id, user_id))
    recent_activity = cur.fetchall()

    refresh_notifications(
        cur,
        current_user,
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        goal_summary,
        investment_summary,
    )
    unread_count, notifications = get_notification_feed(cur, user_id, limit=20)

    conn.commit()
    conn.close()

    return render_template(
        "intelligence_dashboard.html",
        financial_snapshot=financial_snapshot,
        spending_analysis=spending_analysis,
        budget_recommendations=budget_recommendations,
        health_score=health_score,
        investment_summary=investment_summary,
        goal_summary=goal_summary,
        ai_insights=ai_insights,
        savings_prediction=savings_prediction,
        timeline=timeline,
        achievements=achievements,
        notifications=notifications,
        unread_count=unread_count,
        recent_activity=recent_activity,
        income_trend=income_trend,
        savings_trend=savings_trend,
        portfolio_labels=portfolio_labels,
        portfolio_values=portfolio_values,
    )


@app.route("/notifications")
def notifications_history():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    cur.execute("""
        SELECT id, notification_type, title, message, priority, is_read, created_at
        FROM notifications
        WHERE user_id=?
        ORDER BY datetime(created_at) DESC, id DESC
    """, (user_id,))
    notification_rows = []
    for row in cur.fetchall():
        notification_rows.append({
            "id": row["id"],
            "notification_type": row["notification_type"],
            "title": row["title"],
            "message": row["message"],
            "priority": row["priority"] if row["priority"] else "info",
            "is_read": int(row["is_read"]),
            "created_at": row["created_at"],
            "time_ago": format_time_ago(row["created_at"]),
        })

    cur.execute("""
        SELECT COUNT(*) AS unread_count
        FROM notifications
        WHERE user_id=? AND is_read=0
    """, (user_id,))
    unread_count = int(cur.fetchone()["unread_count"])

    conn.close()

    return render_template(
        "notifications.html",
        notifications=notification_rows,
        unread_count=unread_count,
    )


@app.route("/notifications/mark-read/<int:notification_id>", methods=["POST"])
def mark_notification_read(notification_id):

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/notifications")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT title
        FROM notifications
        WHERE id=? AND user_id=?
        LIMIT 1
    """, (notification_id, session["user_id"]))
    notification_row = cur.fetchone()

    cur.execute("""
        UPDATE notifications
        SET is_read=1
        WHERE id=? AND user_id=?
    """, (notification_id, session["user_id"]))

    if notification_row:
        log_activity(
            cur,
            session["user_id"],
            "notification_read",
            "Notification Read",
            f"Marked notification '{notification_row['title']}' as read.",
            "fa-regular fa-bell",
            "/notifications",
        )

    conn.commit()
    conn.close()

    return redirect(request.referrer or "/notifications")


@app.route("/notifications/mark-all-read", methods=["POST"])
def mark_all_notifications_read():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/notifications")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE notifications
        SET is_read=1
        WHERE user_id=?
    """, (session["user_id"],))

    log_activity(
        cur,
        session["user_id"],
        "notification_read",
        "Notifications Marked Read",
        "All notifications were marked as read.",
        "fa-regular fa-bell",
        "/notifications",
    )
    conn.commit()
    conn.close()

    flash("All notifications marked as read.", "success")
    return redirect(request.referrer or "/notifications")


@app.route("/notifications/clear-all", methods=["POST"])
def clear_all_notifications():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/notifications")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM notifications WHERE user_id=?", (session["user_id"],))

    log_activity(
        cur,
        session["user_id"],
        "notifications_cleared",
        "Notifications Cleared",
        "All notifications were cleared from history.",
        "fa-solid fa-broom",
        "/notifications",
    )

    conn.commit()
    conn.close()

    flash("All notifications cleared.", "success")
    return redirect("/notifications")

# ---------------- REPORTS ---------------- #

def get_reports_context(cur, user_id):

    cur.execute("""
        SELECT id, name, email, created_at, IFNULL(income, 0) AS income, IFNULL(notify_monthly_report, 0) AS notify_monthly_report
        FROM users
        WHERE id=?
    """, (user_id,))
    user = cur.fetchone()

    cur.execute("""
        SELECT id, category, amount, IFNULL(description, '') AS description, expense_date
        FROM expenses
        WHERE user_id=?
        ORDER BY expense_date DESC, id DESC
    """, (user_id,))
    expenses = cur.fetchall()

    cur.execute("""
        SELECT monthly_budget, month
        FROM budget
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    budget_row = cur.fetchone()

    cur.execute("""
        SELECT category, IFNULL(SUM(amount), 0) AS total_amount
        FROM expenses
        WHERE user_id=?
        GROUP BY category
        ORDER BY total_amount DESC, category ASC
    """, (user_id,))
    category_rows = cur.fetchall()

    investment_summary = get_investment_summary(cur, user_id)
    goal_summary = get_goal_summary(cur, user_id)
    financial_snapshot = get_financial_snapshot(cur, user_id)
    spending_analysis = get_spending_analysis(cur, user_id)
    budget_recommendations = get_budget_recommendations(
        cur,
        user_id,
        financial_snapshot["income"],
        spending_analysis,
        financial_snapshot,
        investment_summary,
        goal_summary,
    )
    health_score = get_financial_health_score(
        financial_snapshot["income"],
        spending_analysis,
        investment_summary,
        budget_recommendations,
        goal_summary,
    )
    ai_insights = build_rule_based_ai_insights(
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        investment_summary,
        goal_summary,
        health_score,
    )
    savings_prediction = get_savings_prediction(cur, user_id, financial_snapshot, spending_analysis, goal_summary)
    achievements = get_user_achievements(
        cur,
        user_id,
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        investment_summary,
        goal_summary,
    )
    create_achievement_notifications(cur, user_id, achievements)

    save_health_score_snapshot(cur, user_id, health_score)
    store_recommendations_snapshot(cur, user_id, budget_recommendations, ai_insights)

    cur.execute("""
        SELECT recommendation_type, message, generated_on
        FROM recommendations
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 10
    """, (user_id,))
    recommendations = cur.fetchall()

    cur.execute("""
        SELECT title, message, priority, created_at
        FROM notifications
        WHERE user_id=?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 8
    """, (user_id,))
    recent_alerts = cur.fetchall()

    cur.execute("""
        SELECT priority, COUNT(*) AS total
        FROM notifications
        WHERE user_id=?
        GROUP BY priority
    """, (user_id,))
    priority_counts = defaultdict(int)
    for row in cur.fetchall():
        priority_counts[(row["priority"] or "info")] = int(row["total"])

    user_income = float(user["income"] if user else 0)
    total_expense = round(sum(float(row["amount"] or 0) for row in expenses), 2)
    total_savings = round(user_income - total_expense, 2)
    monthly_budget = float(budget_row["monthly_budget"]) if budget_row else 0.0
    budget_usage_percent = round((total_expense / monthly_budget) * 100, 2) if monthly_budget else 0

    return {
        "user": user,
        "report_generated_at": ist_now().strftime("%d %b %Y, %I:%M %p"),
        "expenses": expenses,
        "category_rows": category_rows,
        "budget_row": budget_row,
        "investment_summary": investment_summary,
        "goal_summary": goal_summary,
        "financial_snapshot": financial_snapshot,
        "spending_analysis": spending_analysis,
        "budget_recommendations": budget_recommendations,
        "health_score": health_score,
        "ai_insights": ai_insights,
        "savings_prediction": savings_prediction,
        "achievements": achievements,
        "recommendations": recommendations,
        "recent_alerts": recent_alerts,
        "priority_counts": {
            "info": priority_counts["info"],
            "success": priority_counts["success"],
            "warning": priority_counts["warning"],
            "danger": priority_counts["danger"],
        },
        "income_summary": round(user_income, 2),
        "expense_summary": total_expense,
        "savings_summary": total_savings,
        "budget_summary": round(monthly_budget, 2),
        "budget_usage_percent": budget_usage_percent,
        "total_transactions": len(expenses),
    }

@app.route("/reports")
def reports():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    try:
        cur = conn.cursor()
        report_context = get_reports_context(cur, session["user_id"])
        month_key = ist_now().strftime("%Y-%m")
        monthly_event_key = f"monthly_summary_emailed:{month_key}"

        cur.execute("SELECT 1 FROM notifications WHERE user_id=? AND event_key=? LIMIT 1", (session["user_id"], monthly_event_key))
        already_emailed = cur.fetchone() is not None

        should_send_monthly_email = (
            not already_emailed
            and report_context.get("user") is not None
            and int(report_context["user"]["notify_monthly_report"] or 0)
        )

        # Finish DB transaction before external email operation.
        conn.commit()

        if should_send_monthly_email:
            email_sent = send_monthly_summary_email(
                report_context["user"]["name"],
                report_context["user"]["email"],
                report_context,
            )
            if email_sent:
                create_notification_if_missing(
                    cur,
                    session["user_id"],
                    "monthly_summary_email",
                    "Monthly Summary Emailed",
                    "Your monthly financial summary was delivered to your email.",
                    monthly_event_key,
                    "info",
                )
                conn.commit()
    finally:
        conn.close()

    return render_template("reports.html", **report_context)

@app.route("/export_csv")
def export_csv():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    try:
        cur = conn.cursor()

        report_context = get_reports_context(cur, session["user_id"])
        log_activity(
            cur,
            session["user_id"],
            "csv_exported",
            "CSV Exported",
            "Professional CSV report was exported.",
            "fa-solid fa-file-csv",
            "/reports",
        )
        create_notification_if_missing(
            cur,
            session["user_id"],
            "report_csv_exported",
            "CSV Report Generated",
            "Your CSV financial report has been generated successfully.",
                f"report_csv_exported:{ist_now().strftime('%Y-%m-%d')}",
            "success",
        )
        conn.commit()
        rows = report_context["expenses"]
    finally:
        conn.close()

    output = StringIO()

    writer = csv.writer(output)

    writer.writerow(["Finance Analytics Platform for Financial Reporting and Budget Tracking - Professional Financial Report"])
    writer.writerow(["Generated", report_context["report_generated_at"]])
    writer.writerow(["User", report_context["user"]["name"] if report_context["user"] else "N/A"])
    writer.writerow(["Financial Health Score", report_context["health_score"]["score"]])
    writer.writerow(["Financial Health Status", report_context["health_score"]["status"]])
    writer.writerow(["Budget Utilization %", report_context["budget_recommendations"]["budget_utilization_percent"]])
    writer.writerow(["Savings Prediction 3M", report_context["savings_prediction"]["prediction_3m"]])
    writer.writerow(["Savings Prediction 6M", report_context["savings_prediction"]["prediction_6m"]])
    writer.writerow(["Savings Prediction 12M", report_context["savings_prediction"]["prediction_12m"]])
    writer.writerow(["Achievements Earned", report_context["achievements"]["earned_count"]])
    writer.writerow(["Achievements Total", report_context["achievements"]["total_count"]])
    writer.writerow([])
    writer.writerow(["Top Recommendations"])
    writer.writerow(["Type", "Message", "Generated On"])
    for recommendation in report_context["recommendations"][:8]:
        writer.writerow([
            recommendation["recommendation_type"],
            recommendation["message"],
            recommendation["generated_on"],
        ])

    writer.writerow([])
    writer.writerow(["Recent Alerts"])
    writer.writerow(["Priority", "Title", "Message", "Created At"])
    for alert in report_context["recent_alerts"][:8]:
        writer.writerow([
            alert["priority"],
            alert["title"],
            alert["message"],
            alert["created_at"],
        ])

    writer.writerow([])

    writer.writerow([
        "Transaction ID",
        "Category",
        "Amount (INR)",
        "Description",
        "Expense Date"
    ])

    for row in rows:

        writer.writerow([
            row["id"],
            row["category"],
            f"{float(row['amount']):.2f}",
            row["description"] or "-",
            row["expense_date"]
        ])

    response = make_response(output.getvalue())

    response.headers[
        "Content-Disposition"
    ] = "attachment; filename=expense_report.csv"

    response.headers[
        "Content-Type"
    ] = "text/csv"

    return response


# ---------------- INVESTMENT ---------------- #

@app.route("/investment", methods=["GET", "POST"])
def investment():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    if request.method == "POST":

        if not validate_csrf_token():
            conn.close()
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/investment")

        asset = request.form["asset"]
        investment_name = request.form["investment_name"]
        invested_amount = request.form["invested_amount"]
        current_value = request.form["current_value"]
        purchase_date = request.form.get("purchase_date")

        cur.execute("""
            INSERT INTO investments(
                user_id,
                asset,
                investment_name,
                invested_amount,
                current_value,
                purchase_date
            )
            VALUES(?,?,?,?,?,?)
        """, (
            user_id,
            asset,
            investment_name,
            invested_amount,
            current_value,
            purchase_date
        ))

        log_activity(
            cur,
            user_id,
            "investment_added",
            "Investment Added",
            f"{investment_name} ({asset}) added with Rs. {safe_float(invested_amount):,.0f} invested.",
            "fa-solid fa-chart-line",
            "/investment",
        )

        create_notification_if_missing(
            cur,
            user_id,
            "investment_added",
            "Investment Added",
            f"{investment_name} was added to your portfolio.",
                f"investment_added:{ist_now().strftime('%Y-%m-%d')}:{investment_name}:{safe_float(invested_amount):.2f}",
            "success",
        )

        user = get_user_by_id(user_id)
        if user and int(user["notify_investment_alerts"] or 0):
            send_investment_added_email(
                user["name"],
                user["email"],
                investment_name,
                asset,
                safe_float(invested_amount),
                safe_float(current_value),
            )

        conn.commit()
        conn.close()
        flash("Investment Added Successfully")
        return redirect("/investment")

    keyword = request.args.get("keyword", "").strip()
    asset_filter = request.args.get("asset", "").strip()

    where_clause = ["user_id=?"]
    params = [user_id]

    if keyword:
        where_clause.append("(asset LIKE ? OR investment_name LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    if asset_filter:
        where_clause.append("asset=?")
        params.append(asset_filter)

    cur.execute(f"""
        SELECT *
        FROM investments
        WHERE {' AND '.join(where_clause)}
        ORDER BY created_at DESC, id DESC
    """, params)

    investments = cur.fetchall()

    investment_rows = []

    for row in investments:
        invested = float(row["invested_amount"])
        current = float(row["current_value"])
        profit = current - invested
        roi = round((profit / invested) * 100, 2) if invested else 0
        investment_rows.append({
            "id": row["id"],
            "asset": row["asset"],
            "investment_name": row["investment_name"],
            "invested_amount": invested,
            "current_value": current,
            "purchase_date": row["purchase_date"],
            "created_at": row["created_at"],
            "profit": round(profit, 2),
            "roi": roi
        })

    summary = get_investment_summary(cur, user_id)

    conn.close()

    return render_template(
        "investment.html",
        investments=investment_rows,
        summary=summary,
        keyword=keyword,
        asset_filter=asset_filter
    )


@app.route("/edit_investment/<int:id>", methods=["GET", "POST"])
def edit_investment(id):

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    if request.method == "POST":

        if not validate_csrf_token():
            conn.close()
            flash("Invalid security token. Please try again.", "danger")
            return redirect(f"/edit_investment/{id}")

        asset = request.form["asset"]
        investment_name = request.form["investment_name"]
        invested_amount = request.form["invested_amount"]
        current_value = request.form["current_value"]
        purchase_date = request.form.get("purchase_date")

        cur.execute("""
            UPDATE investments
            SET asset=?, investment_name=?, invested_amount=?, current_value=?, purchase_date=?
            WHERE id=? AND user_id=?
        """, (
            asset,
            investment_name,
            invested_amount,
            current_value,
            purchase_date,
            id,
            user_id
        ))

        conn.commit()
        conn.close()
        flash("Investment Updated Successfully")
        return redirect("/investment")

    cur.execute("""
        SELECT *
        FROM investments
        WHERE id=? AND user_id=?
    """, (id, user_id))

    investment = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM investments
        WHERE user_id=?
        ORDER BY created_at DESC, id DESC
    """, (user_id,))

    investments = []

    for row in cur.fetchall():
        invested = float(row["invested_amount"])
        current = float(row["current_value"])
        profit = current - invested
        roi = round((profit / invested) * 100, 2) if invested else 0
        investments.append({
            "id": row["id"],
            "asset": row["asset"],
            "investment_name": row["investment_name"],
            "invested_amount": invested,
            "current_value": current,
            "purchase_date": row["purchase_date"],
            "created_at": row["created_at"],
            "profit": round(profit, 2),
            "roi": roi
        })

    summary = get_investment_summary(cur, user_id)

    conn.close()

    if not investment:
        flash("Investment not found")
        return redirect("/investment")

    return render_template(
        "investment.html",
        edit_investment=investment,
        investments=investments,
        summary=summary,
        keyword="",
        asset_filter=""
    )


@app.route("/delete_investment/<int:id>", methods=["POST"])
def delete_investment(id):

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/investment")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM investments
        WHERE id=? AND user_id=?
    """, (id, session["user_id"]))

    conn.commit()
    conn.close()

    flash("Investment Deleted Successfully")
    return redirect("/investment")


@app.route("/asset-allocation")
def asset_allocation():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    summary = get_investment_summary(cur, user_id)

    cur.execute("""
        SELECT
            asset,
            COUNT(*) AS count,
            IFNULL(SUM(invested_amount), 0) AS invested_amount,
            IFNULL(SUM(current_value), 0) AS current_value
        FROM investments
        WHERE user_id=?
        GROUP BY asset
        ORDER BY invested_amount DESC
    """, (user_id,))

    allocation_rows = []
    chart_labels = []
    chart_values = []

    for row in cur.fetchall():
        invested_amount = float(row["invested_amount"])
        current_value = float(row["current_value"])
        profit = current_value - invested_amount
        roi = round((profit / invested_amount) * 100, 2) if invested_amount else 0
        allocation_rows.append({
            "asset": row["asset"],
            "count": row["count"],
            "invested_amount": invested_amount,
            "current_value": current_value,
            "profit": round(profit, 2),
            "roi": roi,
            "share": round((invested_amount / summary["total_investment"]) * 100, 2) if summary["total_investment"] else 0
        })
        chart_labels.append(row["asset"])
        chart_values.append(invested_amount)

    conn.close()

    return render_template(
        "asset_allocation.html",
        summary=summary,
        allocation_rows=allocation_rows,
        chart_labels=chart_labels,
        chart_values=chart_values
    )


@app.route("/financial-goals", methods=["GET", "POST"])
def financial_goals():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    if request.method == "POST":

        if not validate_csrf_token():
            conn.close()
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/financial-goals")

        goal_name = request.form["goal_name"]
        target_amount = request.form["target_amount"]
        saved_amount = request.form["saved_amount"]
        target_date = request.form.get("target_date")

        cur.execute("""
            INSERT INTO goals(
                user_id,
                goal_name,
                target_amount,
                saved_amount,
                target_date
            )
            VALUES(?,?,?,?,?)
        """, (
            user_id,
            goal_name,
            target_amount,
            saved_amount,
            target_date
        ))

        log_activity(
            cur,
            user_id,
            "goal_created",
            "Goal Created",
            f"Goal '{goal_name}' created with target Rs. {safe_float(target_amount):,.0f}.",
            "fa-solid fa-bullseye",
            "/financial-goals",
        )

        if safe_float(saved_amount) >= safe_float(target_amount) and safe_float(target_amount) > 0:
            create_notification_if_missing(
                cur,
                user_id,
                "goal_completed",
                "Goal Completed",
                f"Congratulations! '{goal_name}' has reached its target.",
                f"goal_completed:{goal_name}:{ist_now().strftime('%Y-%m-%d')}",
                "success",
            )

            log_activity(
                cur,
                user_id,
                "goal_completed",
                "Goal Completed",
                f"Goal '{goal_name}' reached 100% completion.",
                "fa-solid fa-circle-check",
                "/financial-goals",
            )

            user = get_user_by_id(user_id)
            if user and int(user["notify_goal_alerts"] or 0):
                send_goal_completed_email(
                    user["name"],
                    user["email"],
                    goal_name,
                    safe_float(target_amount),
                )

        conn.commit()
        flash("Goal Added Successfully")
        return redirect("/financial-goals")

    cur.execute("""
        SELECT *
        FROM goals
        WHERE user_id=?
        ORDER BY target_date ASC, id DESC
    """, (user_id,))

    goals = []

    for row in cur.fetchall():
        target_amount = float(row["target_amount"])
        saved_amount = float(row["saved_amount"])
        remaining_amount = max(target_amount - saved_amount, 0)
        completion_percentage = round((saved_amount / target_amount) * 100, 2) if target_amount else 0
        goals.append({
            "id": row["id"],
            "goal_name": row["goal_name"],
            "target_amount": target_amount,
            "saved_amount": saved_amount,
            "remaining_amount": round(remaining_amount, 2),
            "target_date": row["target_date"],
            "completion_percentage": completion_percentage,
            "progress_width": min(completion_percentage, 100)
        })

    goal_summary = get_goal_summary(cur, user_id)

    conn.close()

    return render_template(
        "financial_goals.html",
        goals=goals,
        goal_summary=goal_summary
    )


@app.route("/edit_goal/<int:id>", methods=["GET", "POST"])
def edit_goal(id):

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    if request.method == "POST":

        if not validate_csrf_token():
            conn.close()
            flash("Invalid security token. Please try again.", "danger")
            return redirect(f"/edit_goal/{id}")

        goal_name = request.form["goal_name"]
        target_amount = request.form["target_amount"]
        saved_amount = request.form["saved_amount"]
        target_date = request.form.get("target_date")

        cur.execute("SELECT goal_name, target_amount, saved_amount FROM goals WHERE id=? AND user_id=?", (id, user_id))
        previous_goal = cur.fetchone()

        cur.execute("""
            UPDATE goals
            SET goal_name=?, target_amount=?, saved_amount=?, target_date=?
            WHERE id=? AND user_id=?
        """, (
            goal_name,
            target_amount,
            saved_amount,
            target_date,
            id,
            user_id
        ))

        log_activity(
            cur,
            user_id,
            "goal_updated",
            "Goal Updated",
            f"Goal '{goal_name}' was updated.",
            "fa-solid fa-pen-to-square",
            "/financial-goals",
        )

        was_complete = False
        if previous_goal:
            previous_target = safe_float(previous_goal["target_amount"])
            previous_saved = safe_float(previous_goal["saved_amount"])
            was_complete = previous_target > 0 and previous_saved >= previous_target

        is_complete = safe_float(target_amount) > 0 and safe_float(saved_amount) >= safe_float(target_amount)
        if is_complete and not was_complete:
            create_notification_if_missing(
                cur,
                user_id,
                "goal_completed",
                "Goal Completed",
                f"Great job! '{goal_name}' is now completed.",
                f"goal_completed:{id}",
                "success",
            )
            log_activity(
                cur,
                user_id,
                "goal_completed",
                "Goal Completed",
                f"Goal '{goal_name}' reached 100% completion.",
                "fa-solid fa-circle-check",
                "/financial-goals",
            )

            user = get_user_by_id(user_id)
            if user and int(user["notify_goal_alerts"] or 0):
                send_goal_completed_email(
                    user["name"],
                    user["email"],
                    goal_name,
                    safe_float(target_amount),
                )

        conn.commit()
        conn.close()
        flash("Goal Updated Successfully")
        return redirect("/financial-goals")

    cur.execute("""
        SELECT *
        FROM goals
        WHERE id=? AND user_id=?
    """, (id, user_id))

    goal = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM goals
        WHERE user_id=?
        ORDER BY target_date ASC, id DESC
    """, (user_id,))

    goals = []

    for row in cur.fetchall():
        target_amount = float(row["target_amount"])
        saved_amount = float(row["saved_amount"])
        remaining_amount = max(target_amount - saved_amount, 0)
        completion_percentage = round((saved_amount / target_amount) * 100, 2) if target_amount else 0
        goals.append({
            "id": row["id"],
            "goal_name": row["goal_name"],
            "target_amount": target_amount,
            "saved_amount": saved_amount,
            "remaining_amount": round(remaining_amount, 2),
            "target_date": row["target_date"],
            "completion_percentage": completion_percentage,
            "progress_width": min(completion_percentage, 100)
        })

    goal_summary = get_goal_summary(cur, user_id)

    conn.close()

    if not goal:
        flash("Goal not found")
        return redirect("/financial-goals")

    return render_template(
        "financial_goals.html",
        edit_goal=goal,
        goals=goals,
        goal_summary=goal_summary
    )


@app.route("/delete_goal/<int:id>", methods=["POST"])
def delete_goal(id):

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/financial-goals")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM goals
        WHERE id=? AND user_id=?
    """, (id, session["user_id"]))

    conn.commit()
    conn.close()

    flash("Goal Deleted Successfully")
    return redirect("/financial-goals")


@app.route("/portfolio-dashboard")
def portfolio_dashboard():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    user_id = session["user_id"]

    summary = get_investment_summary(cur, user_id)
    goal_summary = get_goal_summary(cur, user_id)

    cur.execute("""
        SELECT *
        FROM investments
        WHERE user_id=?
        ORDER BY created_at ASC, id ASC
    """, (user_id,))

    investments = []
    timeline_labels = []
    portfolio_growth = []
    investment_trend = []
    running_invested = 0.0
    running_value = 0.0

    for row in cur.fetchall():
        invested = float(row["invested_amount"])
        current = float(row["current_value"])
        profit = current - invested
        roi = round((profit / invested) * 100, 2) if invested else 0
        running_invested += invested
        running_value += current
        timeline_labels.append(row["purchase_date"] or row["created_at"][:10])
        portfolio_growth.append(round(running_value, 2))
        investment_trend.append(round(running_invested, 2))
        investments.append({
            "investment_name": row["investment_name"],
            "asset": row["asset"],
            "invested_amount": invested,
            "current_value": current,
            "profit": round(profit, 2),
            "roi": roi
        })

    cur.execute("""
        SELECT
            asset,
            IFNULL(SUM(invested_amount), 0) AS invested_amount
        FROM investments
        WHERE user_id=?
        GROUP BY asset
        ORDER BY invested_amount DESC
    """, (user_id,))

    allocation_labels = []
    allocation_values = []

    for row in cur.fetchall():
        allocation_labels.append(row["asset"])
        allocation_values.append(float(row["invested_amount"]))

    cur.execute("""
        SELECT
            goal_name,
            target_amount,
            saved_amount,
            target_date
        FROM goals
        WHERE user_id=?
        ORDER BY target_date ASC, id DESC
    """, (user_id,))

    goals = []
    goal_labels = []
    goal_progress_values = []

    for row in cur.fetchall():
        target_amount = float(row["target_amount"])
        saved_amount = float(row["saved_amount"])
        completion_percentage = round((saved_amount / target_amount) * 100, 2) if target_amount else 0
        goals.append({
            "goal_name": row["goal_name"],
            "target_amount": target_amount,
            "saved_amount": saved_amount,
            "completion_percentage": completion_percentage
        })
        goal_labels.append(row["goal_name"])
        goal_progress_values.append(min(completion_percentage, 100))

    top_investments = sorted(investments, key=lambda item: item["profit"], reverse=True)[:5]
    worst_investments = sorted(investments, key=lambda item: item["profit"])[:5]

    conn.close()

    return render_template(
        "portfolio_dashboard.html",
        summary=summary,
        goal_summary=goal_summary,
        timeline_labels=timeline_labels,
        portfolio_growth=portfolio_growth,
        investment_trend=investment_trend,
        allocation_labels=allocation_labels,
        allocation_values=allocation_values,
        goal_labels=goal_labels,
        goal_progress_values=goal_progress_values,
        top_investments=top_investments,
        worst_investments=worst_investments,
        goals=goals
    )

# ---------------- DOWNLOAD PDF ---------------- #

@app.route("/download_pdf")
def download_pdf():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    try:
        cur = conn.cursor()

        user_id = session["user_id"]
        report_id = f"SFI-{ist_now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

        report_context = get_reports_context(cur, user_id)
        log_activity(
            cur,
            user_id,
            "pdf_generated",
            "PDF Generated",
            "Professional PDF financial report was generated.",
            "fa-solid fa-file-pdf",
            "/reports",
        )
        create_notification_if_missing(
            cur,
            user_id,
            "report_pdf_generated",
            "PDF Report Generated",
            "Your PDF financial report is ready to download.",
            f"report_pdf_generated:{ist_now().strftime('%Y-%m-%d')}",
            "success",
        )
        conn.commit()
    finally:
        conn.close()

    user = report_context["user"]
    expenses = report_context["expenses"]
    category_rows = report_context["category_rows"]

    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=46,
        bottomMargin=42,
        title="Finance Analytics Platform for Financial Reporting and Budget Tracking - Financial Report",
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    heading_style = styles["Heading2"]
    normal_style = styles["BodyText"]

    heading_style.fontSize = 13
    heading_style.leading = 16
    heading_style.spaceAfter = 8

    stats_label_style = ParagraphStyle(
        "StatsLabel",
        parent=normal_style,
        fontSize=10,
        textColor=colors.HexColor("#475569"),
        leading=13,
    )

    stats_value_style = ParagraphStyle(
        "StatsValue",
        parent=normal_style,
        fontSize=11,
        textColor=colors.HexColor("#0f172a"),
        leading=14,
    )

    elements = []
    elements.append(Spacer(1, 1.4 * inch))
    elements.append(Paragraph("Finance Analytics Platform for Financial Reporting and Budget Tracking", title_style))
    elements.append(Spacer(1, 0.12 * inch))
    elements.append(Paragraph("Enterprise Financial Performance Report", heading_style))
    elements.append(Spacer(1, 0.35 * inch))
    elements.append(Paragraph(f"Report ID: {report_id}", normal_style))
    elements.append(Paragraph(f"Prepared For: {user['name'] if user else 'N/A'}", normal_style))
    elements.append(Paragraph(f"Report Generated: {report_context['report_generated_at']}", normal_style))
    elements.append(Paragraph(f"Generation Date: {ist_now().strftime('%d %b %Y')}", normal_style))
    elements.append(Spacer(1, 0.4 * inch))
    elements.append(Paragraph("Confidential - For Authorized Use Only", normal_style))
    elements.append(PageBreak())

    elements.append(Paragraph("User Information", heading_style))
    user_information_data = [
        [Paragraph("Name", stats_label_style), Paragraph(user['name'] if user else 'N/A', stats_value_style)],
        [Paragraph("Email", stats_label_style), Paragraph(user['email'] if user and user['email'] else 'N/A', stats_value_style)],
        [Paragraph("Member Since", stats_label_style), Paragraph((user['created_at'] if user and user['created_at'] else 'N/A'), stats_value_style)],
    ]
    user_information_table = Table(user_information_data, colWidths=[200, 280])
    user_information_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbff")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(user_information_table)
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph("Official Financial Report", heading_style))
    elements.append(Paragraph(f"Generated: {report_context['report_generated_at']}", normal_style))
    elements.append(Spacer(1, 0.18 * inch))

    summary_data = [
        [Paragraph("Income Summary", stats_label_style), Paragraph(f"Rs. {report_context['income_summary']:,.2f}", stats_value_style)],
        [Paragraph("Expense Summary", stats_label_style), Paragraph(f"Rs. {report_context['expense_summary']:,.2f}", stats_value_style)],
        [Paragraph("Savings Summary", stats_label_style), Paragraph(f"Rs. {report_context['savings_summary']:,.2f}", stats_value_style)],
        [Paragraph("Budget Summary", stats_label_style), Paragraph(f"Rs. {report_context['budget_summary']:,.2f}", stats_value_style)],
        [Paragraph("Budget Usage", stats_label_style), Paragraph(f"{report_context['budget_usage_percent']:.2f}%", stats_value_style)],
        [Paragraph("Financial Health Score", stats_label_style), Paragraph(f"{report_context['health_score']['score']}/100 ({report_context['health_score']['status']})", stats_value_style)],
        [Paragraph("Transactions", stats_label_style), Paragraph(str(report_context['total_transactions']), stats_value_style)],
        [Paragraph("Investment Summary", stats_label_style), Paragraph(f"Rs. {report_context['investment_summary']['current_value']:,.2f}", stats_value_style)],
        [Paragraph("Investment ROI", stats_label_style), Paragraph(f"{report_context['investment_summary']['roi']:.2f}%", stats_value_style)],
        [Paragraph("Achievements", stats_label_style), Paragraph(f"{report_context['achievements']['earned_count']}/{report_context['achievements']['total_count']}", stats_value_style)],
    ]

    summary_table = Table(summary_data, colWidths=[200, 280])
    summary_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbff")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph("Monthly Savings Prediction", heading_style))
    prediction_data = [
        ["Current", f"Rs. {report_context['savings_prediction']['current_savings']:,.2f}"],
        ["3 Months", f"Rs. {report_context['savings_prediction']['prediction_3m']:,.2f}"],
        ["6 Months", f"Rs. {report_context['savings_prediction']['prediction_6m']:,.2f}"],
        ["12 Months", f"Rs. {report_context['savings_prediction']['prediction_12m']:,.2f}"],
        ["Expected Goal Date", report_context['savings_prediction']['expected_goal_achievement_date']],
    ]
    prediction_table = Table(prediction_data, colWidths=[180, 300])
    prediction_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbff")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(prediction_table)
    elements.append(Spacer(1, 0.18 * inch))

    top_categories = report_context["category_rows"][:5]
    if top_categories:
        elements.append(Paragraph("Top Expense Categories Chart", heading_style))
        drawing = Drawing(480, 200)
        chart = VerticalBarChart()
        chart.x = 40
        chart.y = 35
        chart.height = 130
        chart.width = 390
        chart.data = [[float(row["total_amount"]) for row in top_categories]]
        chart.categoryAxis.categoryNames = [row["category"][:12] for row in top_categories]
        chart.bars[0].fillColor = colors.HexColor("#2563eb")
        chart.valueAxis.valueMin = 0
        chart.valueAxis.valueMax = max(float(row["total_amount"]) for row in top_categories) * 1.2
        chart.valueAxis.valueStep = max(1000, int(chart.valueAxis.valueMax / 5))
        drawing.add(chart)
        elements.append(drawing)
        elements.append(Spacer(1, 0.15 * inch))

        total_top = sum(float(row["total_amount"]) for row in top_categories)
        if total_top > 0:
            pie_drawing = Drawing(480, 220)
            pie_chart = Pie()
            pie_chart.x = 135
            pie_chart.y = 20
            pie_chart.width = 190
            pie_chart.height = 190
            pie_chart.data = [float(row["total_amount"]) for row in top_categories]
            pie_chart.labels = [row["category"][:14] for row in top_categories]
            pie_chart.slices.strokeWidth = 0.4
            pie_drawing.add(pie_chart)
            elements.append(Paragraph("Expense Distribution Chart", heading_style))
            elements.append(pie_drawing)
            elements.append(Spacer(1, 0.15 * inch))

    elements.append(Paragraph("Top Recommendations", heading_style))
    recommendation_rows = [["Type", "Recommendation"]]
    for recommendation in report_context["recommendations"][:8]:
        recommendation_rows.append([
            recommendation["recommendation_type"].title(),
            recommendation["message"],
        ])
    recommendation_table = Table(recommendation_rows, colWidths=[110, 370])
    recommendation_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7c3aed")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbff")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(recommendation_table)
    elements.append(Spacer(1, 0.18 * inch))

    elements.append(Paragraph("Recent Alerts", heading_style))
    alert_rows = [["Priority", "Title", "Message"]]
    for alert in report_context["recent_alerts"][:8]:
        alert_rows.append([
            (alert["priority"] or "info").title(),
            alert["title"],
            alert["message"],
        ])
    alert_table = Table(alert_rows, colWidths=[80, 135, 265])
    alert_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dc2626")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fff7ed")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(alert_table)
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph("Expense Transactions", heading_style))

    expense_data = [["Category", "Amount (INR)", "Description", "Date"]]
    for row in expenses[:60]:
        expense_data.append([
            row["category"],
            f"{float(row['amount']):,.2f}",
            row["description"] or "-",
            row["expense_date"],
        ])

    expense_table = Table(expense_data, colWidths=[120, 95, 220, 85])
    expense_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbff")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(expense_table)
    elements.append(Spacer(1, 0.18 * inch))

    elements.append(Paragraph("Expense Category Statistics", heading_style))
    category_data = [["Category", "Total Amount (INR)"]]
    for row in category_rows:
        category_data.append([row["category"], f"{float(row['total_amount']):,.2f}"])

    category_table = Table(category_data, colWidths=[250, 230])
    category_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbff")]),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(category_table)

    def add_page_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 42)
        canvas.setFillColor(colors.Color(0.80, 0.84, 0.90, alpha=0.12))
        canvas.drawCentredString(A4[0] / 2, A4[1] / 2, "Finance Analytics Platform for Financial Reporting and Budget Tracking")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(doc.leftMargin, 20, f"Finance Analytics Platform for Financial Reporting and Budget Tracking | Report ID: {report_id}")
        canvas.drawRightString(A4[0] - doc.rightMargin, 20, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    pdf.build(elements, onFirstPage=add_page_footer, onLaterPages=add_page_footer)

    if user and int(user["notify_monthly_report"] or 0):
        send_report_generated_email(user["name"], user["email"], report_context["report_generated_at"], report_id)

    buffer.seek(0)
    filename = f"financial_report_{ist_now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


@app.route("/email_report", methods=["POST"])
def email_report():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/reports")

    conn = get_db()
    try:
        cur = conn.cursor()
        user_id = session["user_id"]
        report_context = get_reports_context(cur, user_id)
        user = report_context.get("user")

        if not user or not user["email"]:
            flash("Unable to email report: account email is missing.", "danger")
            return redirect("/reports")

        report_id = f"SFI-{ist_now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        generated_at = report_context["report_generated_at"]

        # Commit report/achievement snapshot writes before PDF generation + email send.
        conn.commit()

        # Build latest PDF snapshot for email attachment.
        pdf_buffer = BytesIO()
        pdf = SimpleDocTemplate(pdf_buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=46, bottomMargin=42)
        styles = getSampleStyleSheet()
        title_style = styles["Title"]
        heading_style = styles["Heading2"]
        normal_style = styles["BodyText"]

        elements = [
            Paragraph("Finance Analytics Platform for Financial Reporting and Budget Tracking", title_style),
            Spacer(1, 0.1 * inch),
            Paragraph("Financial Report Snapshot", heading_style),
            Paragraph(f"Report ID: {report_id}", normal_style),
            Paragraph(f"Generated: {generated_at}", normal_style),
            Spacer(1, 0.14 * inch),
        ]

        summary_table = Table([
            ["Income", f"Rs. {report_context['income_summary']:,.2f}"],
            ["Expenses", f"Rs. {report_context['expense_summary']:,.2f}"],
            ["Savings", f"Rs. {report_context['savings_summary']:,.2f}"],
            ["Budget Usage", f"{report_context['budget_usage_percent']:.2f}%"],
            ["Health Score", f"{report_context['health_score']['score']}/100 ({report_context['health_score']['status']})"],
            ["Transactions", str(report_context['total_transactions'])],
        ], colWidths=[180, 300])
        summary_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbff")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 0.18 * inch))

        expense_data = [["Category", "Amount", "Date"]]
        for row in report_context["expenses"][:20]:
            expense_data.append([row["category"], f"Rs. {float(row['amount']):,.2f}", row["expense_date"]])

        expense_table = Table(expense_data, colWidths=[170, 150, 160])
        expense_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbff")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(Paragraph("Recent Transactions", heading_style))
        elements.append(expense_table)

        pdf.build(elements)
        pdf_buffer.seek(0)
        pdf_bytes = pdf_buffer.read()

        html_body = render_email_template(
            "report_emailed.html",
            user_name=user["name"],
            report_generated_at=generated_at,
            report_id=report_id,
        )
        attached_name = f"financial_report_{ist_now().strftime('%Y%m%d_%H%M%S')}.pdf"
        sent = send_email_message(
            "Financial Report - Finance Analytics Platform",
            [user["email"]],
            html_body,
            attachments=[{"filename": attached_name, "mimetype": "application/pdf", "data": pdf_bytes}],
            return_error=True,
        )

        email_sent, email_error = sent
        if email_sent:
            log_activity(
                cur,
                user_id,
                "report_emailed",
                "Report Emailed",
                "Latest PDF report was generated and emailed successfully.",
                "fa-solid fa-paper-plane",
                "/reports",
            )
            create_notification_if_missing(
                cur,
                user_id,
                "report_emailed",
                "Report Emailed",
                "Latest financial report was sent to your registered email.",
                f"report_emailed:{ist_now().strftime('%Y-%m-%d')}:{attached_name}",
                "success",
            )
            conn.commit()
            flash("Financial report sent successfully to your registered email.", "success")
            return redirect("/reports")

        flash(f"Unable to send the report. {email_error or 'Please check the email configuration.'}", "danger")
        return redirect("/reports")
    except Exception:
        logger.exception("Email report request failed before delivery")
        flash("Unable to send the report. Please check the email configuration.", "danger")
        return redirect("/reports")
    finally:
        conn.close()
    
    
# ---------------- PROFILE ---------------- #

@app.route("/profile", methods=["GET", "POST"])
def profile():

    if not login_required():
        return redirect("/login")

    conn = get_db()

    cur = conn.cursor()

    cur.execute("""
    SELECT *
    FROM users
    WHERE id=?
    """,(session["user_id"],))

    user = cur.fetchone()
    template_user = prepare_user_for_template(user)

    member_since = user["created_at"] if user and user["created_at"] else None
    profile_completion = calculate_profile_completion(user)

    financial_snapshot = get_financial_snapshot(cur, session["user_id"])
    spending_analysis = get_spending_analysis(cur, session["user_id"])
    investment_summary = get_investment_summary(cur, session["user_id"])
    goal_summary = get_goal_summary(cur, session["user_id"])
    budget_recommendations = get_budget_recommendations(
        cur,
        session["user_id"],
        financial_snapshot["income"],
        spending_analysis,
        financial_snapshot,
        investment_summary,
        goal_summary,
    )
    health_score = get_financial_health_score(
        financial_snapshot["income"],
        spending_analysis,
        investment_summary,
        budget_recommendations,
        goal_summary,
    )
    achievements = get_user_achievements(
        cur,
        session["user_id"],
        financial_snapshot,
        spending_analysis,
        budget_recommendations,
        investment_summary,
        goal_summary,
    )
    create_achievement_notifications(cur, session["user_id"], achievements)

    account_age_days = 0
    if member_since:
        try:
            joined_on = datetime.fromisoformat(member_since)
            account_age_days = max((datetime.utcnow() - joined_on).days, 0)
        except ValueError:
            account_age_days = 0

    profile_analytics = {
        "account_age_days": account_age_days,
        "financial_health_score": health_score["score"],
        "financial_health_status": health_score["status"],
        "total_transactions": spending_analysis["transaction_count"],
        "monthly_income": round(financial_snapshot["income"], 2),
        "monthly_expenses": round(spending_analysis["total_monthly_expense"], 2),
        "monthly_savings": round(health_score["monthly_savings"], 2),
        "total_investment": round(investment_summary["total_investment"], 2),
        "investment_roi": round(investment_summary["roi"], 2),
        "active_goals": goal_summary["active_goals"],
        "completed_goals": goal_summary["completed_goals"],
        "goals_progress": round(goal_summary["overall_progress"], 2),
        "achievements_earned": achievements["earned_count"],
        "achievements_total": achievements["total_count"],
        "journey_stage": "Advanced" if health_score["score"] >= 85 else "Growing" if health_score["score"] >= 65 else "Foundation",
    }

    recent_activities = get_recent_activities(cur, session["user_id"], limit=8)

    cur.execute("""
        SELECT score_date, score
        FROM health_scores
        WHERE user_id=?
        ORDER BY score_date DESC
        LIMIT 6
    """, (session["user_id"],))
    score_rows = list(reversed(cur.fetchall()))
    if score_rows:
        mini_chart_labels = [row["score_date"] for row in score_rows]
        mini_chart_scores = [int(row["score"]) for row in score_rows]
    else:
        mini_chart_labels = ["Current"]
        mini_chart_scores = [int(health_score["score"])]

    conn.commit()

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            conn.close()
            return redirect("/profile")

        full_name = request.form["name"].strip()
        phone = request.form["phone"].strip()
        date_of_birth = request.form.get("date_of_birth", "").strip()
        location = request.form.get("location", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip()
        country = request.form.get("country", "").strip()
        bio = request.form.get("bio", "").strip()
        occupation = request.form.get("occupation", "").strip()
        organization = request.form.get("organization", "").strip()
        github_url = request.form.get("github_url", "").strip()
        linkedin_url = request.form.get("linkedin_url", "").strip()
        profile_image_action = request.form.get("profile_image_action", "").strip().lower()
        remove_profile_image = request.form.get("remove_profile_image") == "1"
        uploaded_image = request.files.get("profile_image")
        has_profile_image_file = "profile_image" in request.files
        logger.info(
            "Profile upload request user_id=%s content_type=%s request_files=%s contains_profile_image=%s action=%s",
            session["user_id"],
            request.content_type,
            list(request.files.keys()),
            has_profile_image_file,
            profile_image_action or "<none>",
        )
        logger.info(
            "Profile upload received filename user_id=%s filename=%s",
            session["user_id"],
            (uploaded_image.filename.strip() if uploaded_image and uploaded_image.filename else "<empty>")
        )

        if profile_image_action == "upload" and not has_profile_image_file:
            flash("No image file was received by the server. Please choose a JPG, JPEG, PNG, or WEBP image and try again.", "danger")
            conn.close()
            return redirect("/profile")

        profile_image_name = normalize_profile_image_db_value(user["profile_image"])

        if remove_profile_image and profile_image_name:
            existing_file = profile_image_storage_path(profile_image_name)
            if existing_file.exists():
                existing_file.unlink()
            profile_image_name = None

        if profile_image_action == "upload" and uploaded_image and not uploaded_image.filename:
            flash("No file was selected. Please choose a JPG, JPEG, PNG, or WEBP image and try again.", "danger")
            conn.close()
            return redirect("/profile")

        if uploaded_image and uploaded_image.filename:
            saved_name, upload_error = save_profile_upload(uploaded_image, session["user_id"])
            if upload_error:
                flash(upload_error, "danger")
                conn.close()
                return redirect("/profile")

            logger.info("Profile upload saved filename user_id=%s saved_filename=%s", session["user_id"], saved_name)

            if profile_image_name:
                old_file = profile_image_storage_path(profile_image_name)
                if old_file.exists():
                    old_file.unlink()

            profile_image_name = saved_name

        cur.execute("""
            UPDATE users
            SET name=?, phone=?, date_of_birth=?, location=?, city=?, state=?, country=?,
                bio=?, occupation=?, organization=?, github_url=?, linkedin_url=?, profile_image=?, updated_at=?
            WHERE id=?
        """, (
            full_name,
            phone,
            date_of_birth,
            location,
            city,
            state,
            country,
            bio,
            occupation,
            organization,
            github_url,
            linkedin_url,
            profile_image_name,
            datetime.utcnow().isoformat(),
            session["user_id"],
        ))

        log_activity(
            cur,
            session["user_id"],
            "profile_updated",
            "Profile Updated",
            "Profile details were updated successfully.",
            "fa-solid fa-user-pen",
            "/profile",
        )

        create_notification_if_missing(
            cur,
            session["user_id"],
            "profile_updated",
            "Profile Updated",
            "Your profile information was updated successfully.",
            f"profile_updated:{ist_now().strftime('%Y-%m-%d')}",
            "info",
        )

        conn.commit()
        refreshed_user = get_user_by_id(session["user_id"])
        final_profile_image = normalize_profile_image_db_value(refreshed_user["profile_image"]) if refreshed_user else None
        logger.info(
            "Profile upload database updated user_id=%s profile_image=%s",
            session["user_id"],
            final_profile_image,
        )
        logger.info(
            "Profile upload final image path user_id=%s final_image_path=%s",
            session["user_id"],
            final_profile_image,
        )
        session["user_name"] = full_name
        send_profile_updated_email(full_name, user["email"])
        flash("Profile updated successfully.", "success")
        conn.close()
        return redirect("/profile")

    conn.close()

    return render_template(

        "profile.html",

        user=template_user,
        profile_image_url=get_user_profile_image(template_user),
        profile_initial=get_user_initial(template_user),
        member_since=member_since,
        profile_completion=profile_completion,
        profile_analytics=profile_analytics,
        recent_activities=recent_activities,
        mini_chart_labels=mini_chart_labels,
        mini_chart_scores=mini_chart_scores

    )


@app.route("/account-settings", methods=["GET", "POST"])
def account_settings():

    if not login_required():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM users
        WHERE id=?
    """, (session["user_id"],))
    user = cur.fetchone()

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            conn.close()
            return redirect("/account-settings")

        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        bio = request.form.get("bio", "").strip()
        github_url = request.form.get("github_url", "").strip()
        linkedin_url = request.form.get("linkedin_url", "").strip()
        privacy_private_profile = "1" if "1" in request.form.getlist("privacy_private_profile") else "0"
        privacy_public_developer_profile = "1" if "1" in request.form.getlist("privacy_public_developer_profile") else "0"
        privacy_search_visibility = "1" if "1" in request.form.getlist("privacy_search_visibility") else "0"
        notify_email_notifications = "1" if "1" in request.form.getlist("notify_email_notifications") else "0"
        notify_budget_alerts = "1" if "1" in request.form.getlist("notify_budget_alerts") else "0"
        notify_monthly_report = "1" if "1" in request.form.getlist("notify_monthly_report") else "0"
        notify_goal_alerts = "1" if "1" in request.form.getlist("notify_goal_alerts") else "0"
        notify_investment_alerts = "1" if "1" in request.form.getlist("notify_investment_alerts") else "0"
        appearance_theme = request.form.get("appearance_theme", user["appearance_theme"] or "default").strip() or "default"
        theme_mode = request.form.get("theme_mode", user["theme_mode"] if "theme_mode" in user.keys() else "light").strip().lower()
        if theme_mode not in {"light", "dark"}:
            theme_mode = "light"
        export_format_preference = request.form.get("export_format_preference", user["export_format_preference"] or "csv").strip() or "csv"
        email_report_frequency = request.form.get("email_report_frequency", user["email_report_frequency"] or "monthly").strip() or "monthly"
        search_preference = request.form.get("search_preference", user["search_preference"] or "all").strip() or "all"
        layout_preference = request.form.get("layout_preference", user["layout_preference"] or "default").strip() or "default"
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        email_changed = email and email != user["email"]
        notification_email = email if email else user["email"]

        if email_changed and not verify_password(user["password"], current_password):
            flash("Current password is required to change your email.", "danger")
            conn.close()
            return redirect("/account-settings")

        if new_password or confirm_password or current_password:

            if not verify_password(user["password"], current_password):
                flash("Current password is incorrect.", "danger")
                conn.close()
                return redirect("/account-settings")

            if new_password != confirm_password:
                flash("New passwords do not match.", "danger")
                conn.close()
                return redirect("/account-settings")

            if not password_is_strong(new_password):
                flash("New password must be at least 8 characters and include uppercase, lowercase, number, and symbol.", "danger")
                conn.close()
                return redirect("/account-settings")

            cur.execute("""
                UPDATE users
                SET email=?, phone=?, bio=?, github_url=?, linkedin_url=?,
                    privacy_private_profile=?, privacy_public_developer_profile=?, privacy_search_visibility=?,
                    notify_email_notifications=?, notify_budget_alerts=?, notify_monthly_report=?,
                    notify_goal_alerts=?, notify_investment_alerts=?,
                    appearance_theme=?, theme_mode=?, export_format_preference=?, email_report_frequency=?,
                    search_preference=?, layout_preference=?, password=?
                WHERE id=?
            """, (
                email or user["email"],
                phone,
                bio,
                github_url,
                linkedin_url,
                privacy_private_profile,
                privacy_public_developer_profile,
                privacy_search_visibility,
                notify_email_notifications,
                notify_budget_alerts,
                notify_monthly_report,
                notify_goal_alerts,
                notify_investment_alerts,
                appearance_theme,
                theme_mode,
                export_format_preference,
                email_report_frequency,
                search_preference,
                layout_preference,
                hash_password(new_password),
                session["user_id"],
            ))

            log_activity(
                cur,
                session["user_id"],
                "security_updated",
                "Security Settings Updated",
                "Account credentials and security preferences were updated.",
                "fa-solid fa-shield-halved",
                "/account-settings",
            )

            create_notification_if_missing(
                cur,
                session["user_id"],
                "settings_updated",
                "Security Preferences Updated",
                "Your account security settings have been updated.",
                f"settings_updated:{ist_now().strftime('%Y-%m-%d')}",
                "success",
            )

            conn.commit()
            send_password_changed_email(user["name"], notification_email)
            send_profile_updated_email(user["name"], notification_email)
            if email_changed:
                flash("Email and password updated successfully.", "success")
            else:
                flash("Password changed successfully.", "success")
        else:
            cur.execute("""
                UPDATE users
                SET email=?, phone=?, bio=?, github_url=?, linkedin_url=?,
                    privacy_private_profile=?, privacy_public_developer_profile=?, privacy_search_visibility=?,
                    notify_email_notifications=?, notify_budget_alerts=?, notify_monthly_report=?,
                    notify_goal_alerts=?, notify_investment_alerts=?,
                    appearance_theme=?, theme_mode=?, export_format_preference=?, email_report_frequency=?,
                    search_preference=?, layout_preference=?
                WHERE id=?
            """, (
                email or user["email"],
                phone,
                bio,
                github_url,
                linkedin_url,
                privacy_private_profile,
                privacy_public_developer_profile,
                privacy_search_visibility,
                notify_email_notifications,
                notify_budget_alerts,
                notify_monthly_report,
                notify_goal_alerts,
                notify_investment_alerts,
                appearance_theme,
                theme_mode,
                export_format_preference,
                email_report_frequency,
                search_preference,
                layout_preference,
                session["user_id"],
            ))

            log_activity(
                cur,
                session["user_id"],
                "settings_updated",
                "Settings Updated",
                "Account and notification settings were updated.",
                "fa-solid fa-sliders",
                "/account-settings",
            )

            conn.commit()
            send_profile_updated_email(user["name"], notification_email)
            if email_changed:
                flash("Email updated successfully.", "success")
            else:
                flash("Account settings updated successfully.", "success")

        session["theme_mode"] = theme_mode

        conn.close()
        return redirect("/account-settings")

    device_text = user["current_device"] or "Unknown Device"
    operating_system = "Unknown"
    if "(" in device_text and ")" in device_text:
        operating_system = device_text.split("(")[-1].replace(")", "").strip()

    session_info = {
        "current_device": device_text,
        "current_browser": user["current_browser"] or "Unknown Browser",
        "operating_system": operating_system,
        "last_login": user["last_login_at"] or "Never",
        "account_created": user["created_at"] or "Unknown",
        "last_activity": ist_now().strftime("%Y-%m-%d %H:%M:%S IST"),
    }

    conn.close()
    return render_template("account_settings.html", user=user, session_info=session_info)


@app.route("/logout-all-devices", methods=["POST"])
def logout_all_devices():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/account-settings")

    conn = get_db()
    cur = conn.cursor()
    log_activity(
        cur,
        session["user_id"],
        "session_ended",
        "Session Manager",
        "Logout from all devices was requested.",
        "fa-solid fa-power-off",
        "/account-settings",
    )
    conn.commit()
    conn.close()

    session.clear()
    flash("All active sessions have been signed out. Please log in again.", "success")
    return redirect("/login")


@app.route("/reset-preferences", methods=["POST"])
def reset_preferences():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/account-settings")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET appearance_theme='default',
            theme_mode='light',
            export_format_preference='csv',
            email_report_frequency='monthly',
            search_preference='all',
            layout_preference='default',
            notify_email_notifications=1,
            notify_budget_alerts=1,
            notify_monthly_report=1,
            notify_goal_alerts=1,
            notify_investment_alerts=1
        WHERE id=?
    """, (session["user_id"],))

    cur.execute("DELETE FROM dashboard_preferences WHERE user_id=?", (session["user_id"],))

    log_activity(
        cur,
        session["user_id"],
        "preferences_reset",
        "Preferences Reset",
        "All dashboard, notification, export, search, and layout preferences were reset.",
        "fa-solid fa-rotate-left",
        "/account-settings",
    )

    create_notification_if_missing(
        cur,
        session["user_id"],
        "preferences_reset",
        "Preferences Reset",
        "All configurable preferences were reset to default values.",
        f"preferences_reset:{ist_now().strftime('%Y-%m-%d')}",
        "info",
    )

    conn.commit()
    conn.close()

    session["theme_mode"] = "light"

    flash("All preferences were reset successfully.", "success")
    return redirect("/account-settings")


@app.route("/delete-account", methods=["POST"])
def delete_account():

    if not login_required():
        return redirect("/login")

    if not validate_csrf_token():
        flash("Invalid security token. Please try again.", "danger")
        return redirect("/account-settings")

    confirm_checkbox = request.form.get("confirm_delete") == "1"
    confirm_phrase = (request.form.get("confirm_phrase") or "").strip().upper()

    if not confirm_checkbox or confirm_phrase != "DELETE":
        flash("Account deletion confirmation failed. Please review and try again.", "danger")
        return redirect("/account-settings")

    user_id = session["user_id"]
    conn = get_db()
    cur = conn.cursor()

    cleanup_tables = [
        "expenses",
        "budget",
        "investments",
        "goals",
        "password_resets",
        "notifications",
        "health_scores",
        "recommendations",
        "user_achievements",
        "activity_logs",
        "dashboard_preferences",
        "reminders",
    ]

    for table_name in cleanup_tables:
        cur.execute(f"DELETE FROM {table_name} WHERE user_id=?", (user_id,))

    cur.execute("DELETE FROM users WHERE id=?", (user_id,))

    conn.commit()
    conn.close()

    session.clear()
    flash("Your account and related records were deleted permanently.", "success")
    return redirect("/")


@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/forgot_password")

        email = request.form.get("email", "").strip().lower()
        logger.info("Forgot password request received for email=%s", mask_email(email))

        conn = None
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                SELECT *
                FROM users
                WHERE lower(email)=?
            """, (email,))
            user = cur.fetchone()

            if user:
                logger.info("User found for password reset: user_id=%s email=%s", user["id"], mask_email(email))
                otp_code = generate_otp_code()
                otp_hash = hash_password(otp_code)
                expires_at = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

                if app.config.get("MAIL_DEBUG_LOG_OTP"):
                    logger.info("Generated OTP for user_id=%s otp=%s expires_at=%s", user["id"], otp_code, expires_at)
                else:
                    logger.info("Generated OTP for user_id=%s expires_at=%s", user["id"], expires_at)

                cur.execute("""
                    UPDATE password_resets
                    SET is_used=1
                    WHERE email=? AND is_used=0
                """, (email,))

                cur.execute("""
                    INSERT INTO password_resets(user_id, email, otp_hash, attempts, is_verified, is_used, expires_at)
                    VALUES(?,?,?,?,?,?,?)
                """, (user["id"], email, otp_hash, 0, 0, 0, expires_at))

                reset_id = cur.lastrowid
                conn.commit()
                logger.info("Stored OTP reset row successfully: reset_id=%s", reset_id)

                email_sent = send_password_reset_otp_email(user["name"], email, otp_code)
                if not email_sent:
                    logger.error("OTP email send failed for reset_id=%s email=%s", reset_id, mask_email(email))
                    cur.execute("""
                        UPDATE password_resets
                        SET is_used=1
                        WHERE id=?
                    """, (reset_id,))
                    conn.commit()
                    flash("Unable to send OTP email right now. Please verify mail configuration and try again.", "danger")
                    return redirect("/forgot_password")

                session["password_reset_user_id"] = user["id"]
                session["password_reset_email"] = email
                session["password_reset_token_id"] = reset_id
                session.pop("password_reset_verified", None)
                logger.info("Stored reset session values for reset_id=%s", reset_id)

                flash("An OTP has been sent to your email address.", "success")
                return redirect("/verify_otp")

            logger.warning("Forgot password requested for non-existent email=%s", mask_email(email))
            flash("If the email exists in our system, an OTP will be sent.", "success")
            return redirect("/verify_otp")

        except Exception:
            logger.exception("Forgot password flow failed unexpectedly")
            flash("Password reset service is temporarily unavailable. Please try again.", "danger")
            return redirect("/forgot_password")
        finally:
            if conn is not None:
                conn.close()

    return render_template("forgot_password.html")


@app.route("/verify_otp", methods=["GET", "POST"])
def verify_otp():

    if not session.get("password_reset_token_id"):
        return redirect("/forgot_password")

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/verify_otp")

        otp_code = request.form.get("otp", "").strip()

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM password_resets
            WHERE id=? AND is_used=0
        """, (session["password_reset_token_id"],))
        reset_row = cur.fetchone()

        if not reset_row:
            conn.close()
            flash("OTP session is no longer valid. Please request a new one.", "danger")
            session.pop("password_reset_token_id", None)
            session.pop("password_reset_verified", None)
            return redirect("/forgot_password")

        if reset_row["attempts"] >= 3:
            cur.execute("""
                UPDATE password_resets
                SET is_used=1
                WHERE id=?
            """, (reset_row["id"],))
            conn.commit()
            conn.close()
            session.pop("password_reset_token_id", None)
            session.pop("password_reset_verified", None)
            flash("Maximum OTP attempts exceeded. Please request a new OTP.", "danger")
            return redirect("/forgot_password")

        if datetime.utcnow() > datetime.fromisoformat(reset_row["expires_at"]):
            cur.execute("""
                UPDATE password_resets
                SET is_used=1
                WHERE id=?
            """, (reset_row["id"],))
            conn.commit()
            conn.close()
            session.pop("password_reset_token_id", None)
            session.pop("password_reset_verified", None)
            flash("OTP expired. Please request a new one.", "danger")
            return redirect("/forgot_password")

        if check_password_hash(reset_row["otp_hash"], otp_code):
            cur.execute("""
                UPDATE password_resets
                SET is_verified=1
                WHERE id=?
            """, (reset_row["id"],))
            conn.commit()
            conn.close()
            session["password_reset_verified"] = True
            flash("OTP verified successfully.", "success")
            return redirect("/reset_password")

        cur.execute("""
            UPDATE password_resets
            SET attempts = attempts + 1
            WHERE id=?
        """, (reset_row["id"],))
        conn.commit()
        conn.close()
        flash("Invalid OTP. Please try again.", "danger")
        return redirect("/verify_otp")

    return render_template("verify_otp.html")


@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():

    if not session.get("password_reset_verified") or not session.get("password_reset_token_id"):
        return redirect("/forgot_password")

    if request.method == "POST":

        if not validate_csrf_token():
            flash("Invalid security token. Please try again.", "danger")
            return redirect("/reset_password")

        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if new_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect("/reset_password")

        if not password_is_strong(new_password):
            flash("Password must be at least 8 characters and include uppercase, lowercase, number, and symbol.", "danger")
            return redirect("/reset_password")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM password_resets
            WHERE id=? AND is_verified=1 AND is_used=0
        """, (session["password_reset_token_id"],))
        reset_row = cur.fetchone()

        if not reset_row:
            conn.close()
            flash("Your password reset session expired. Please start again.", "danger")
            session.pop("password_reset_token_id", None)
            session.pop("password_reset_verified", None)
            return redirect("/forgot_password")

        cur.execute("""
            UPDATE users
            SET password=?
            WHERE id=?
        """, (hash_password(new_password), reset_row["user_id"]))

        cur.execute("""
            UPDATE password_resets
            SET is_used=1
            WHERE id=?
        """, (reset_row["id"],))

        conn.commit()

        cur.execute("""
            SELECT name, email
            FROM users
            WHERE id=?
        """, (reset_row["user_id"],))
        user = cur.fetchone()
        conn.close()

        if user:
            send_password_changed_email(user["name"], user["email"])

        session.pop("password_reset_token_id", None)
        session.pop("password_reset_verified", None)
        session.pop("password_reset_email", None)
        session.pop("password_reset_user_id", None)

        return render_template(
            "auth_status.html",
            status="success",
            title="Password Reset Complete",
            message="Your password has been updated successfully. You can now log in with your new password.",
            button_text="Go to Login",
            button_url=url_for("login"),
        )

    return render_template("reset_password.html")


@app.route("/admin")
@app.route("/admin/dashboard")
@developer_required
def admin_dashboard():

    developer_user = g.developer_user

    conn = get_db()
    try:
        cur = conn.cursor()
        metrics = get_admin_dashboard_metrics(cur)
        charts = get_admin_chart_payload(cur)
        recent_activity = get_admin_activity_feed(cur, limit=16)
        notifications = get_admin_notifications(cur, limit=10)
        logs = get_admin_logs(cur, limit=12)
        feedback_summary = get_admin_feedback_summary(cur, recent_limit=8)
        settings_snapshot = build_admin_settings_snapshot(cur)
    finally:
        conn.close()

    return render_template(
        "admin_dashboard.html",
        admin_user=developer_user,
        admin_nav_route="admin_dashboard",
        metrics=metrics,
        charts=charts,
        recent_activity=recent_activity,
        notifications=notifications,
        logs=logs,
        feedback_summary=feedback_summary,
        settings_snapshot=settings_snapshot,
    )


@app.route("/admin/users", methods=["GET", "POST"])
@developer_required
def admin_users():

    developer_user = g.developer_user

    search_term = request.args.get("q", "").strip()

    conn = get_db()
    try:
        cur = conn.cursor()

        if request.method == "POST":
            if not validate_csrf_token():
                flash("Invalid security token. Please try again.", "danger")
                return redirect(url_for("admin_users", q=search_term))

            action = (request.form.get("admin_action") or "").strip().lower()
            target_user_id = int(request.form.get("target_user_id") or 0)
            target_user = get_admin_user_detail(cur, target_user_id) if target_user_id else None

            if not target_user:
                flash("Selected user could not be found.", "danger")
                return redirect(url_for("admin_users", q=search_term))

            if action == "verify":
                cur.execute("UPDATE users SET email_verified=1, updated_at=? WHERE id=?", (datetime.utcnow().isoformat(), target_user_id))
                log_activity(cur, developer_user["id"], "admin_user_verified", "Admin Verified User", f"Verified {target_user['email']} from developer panel.", "fa-solid fa-user-check", "/admin/users")
                flash(f"Verified {target_user['email']}.", "success")
            elif action == "suspend":
                if target_user_id == developer_user["id"]:
                    flash("You cannot suspend your own developer account.", "danger")
                    return redirect(url_for("admin_users", q=search_term))
                next_state = 0 if int(target_user.get("is_suspended") or 0) else 1
                cur.execute("UPDATE users SET is_suspended=?, updated_at=? WHERE id=?", (next_state, datetime.utcnow().isoformat(), target_user_id))
                log_activity(cur, developer_user["id"], "admin_user_suspended", "Admin Updated Suspension", f"Set suspension={next_state} for {target_user['email']}.", "fa-solid fa-user-lock", "/admin/users")
                flash(("Suspended" if next_state else "Reactivated") + f" {target_user['email']}.", "success")
            elif action == "delete":
                if target_user_id == developer_user["id"]:
                    flash("You cannot delete your own developer account from the admin panel.", "danger")
                    return redirect(url_for("admin_users", q=search_term))
                delete_user_related_records(cur, target_user_id)
                log_activity(cur, developer_user["id"], "admin_user_deleted", "Admin Deleted User", f"Deleted user {target_user['email']} and related records.", "fa-solid fa-user-xmark", "/admin/users")
                flash(f"Deleted {target_user['email']} and related data.", "success")
            elif action == "edit":
                full_name = request.form.get("name", target_user["name"] or "").strip()
                phone = request.form.get("phone", target_user["phone"] or "").strip()
                income = safe_float(request.form.get("income", target_user.get("income") or 0), target_user.get("income") or 0)
                cur.execute("""
                    UPDATE users
                    SET name=?, phone=?, income=?, updated_at=?
                    WHERE id=?
                """, (full_name, phone, income, datetime.utcnow().isoformat(), target_user_id))
                log_activity(cur, developer_user["id"], "admin_user_edited", "Admin Edited User", f"Updated profile fields for {target_user['email']}.", "fa-solid fa-user-pen", "/admin/users")
                flash(f"Updated {target_user['email']}.", "success")
            else:
                flash("Unsupported admin action.", "danger")
                return redirect(url_for("admin_users", q=search_term))

            conn.commit()
            return redirect(url_for("admin_users", q=search_term, view=target_user_id))

        selected_user_id = request.args.get("view", "").strip() or request.args.get("edit", "").strip()
        selected_user = get_admin_user_detail(cur, int(selected_user_id)) if selected_user_id.isdigit() else None
        users = get_admin_user_rows(cur, search_term)
    finally:
        conn.close()

    return render_template(
        "admin_users.html",
        admin_user=developer_user,
        admin_nav_route="admin_users",
        search_term=search_term,
        users=users,
        selected_user=selected_user,
        edit_user_id=(int(request.args.get("edit")) if request.args.get("edit", "").isdigit() else None),
    )


@app.route("/admin/analytics")
@developer_required
def admin_analytics():

    developer_user = g.developer_user

    conn = get_db()
    try:
        cur = conn.cursor()
        metrics = get_admin_dashboard_metrics(cur)
        charts = get_admin_chart_payload(cur)

        cur.execute("""
            SELECT COUNT(*) AS total_requests,
                   COUNT(DISTINCT user_id) AS unique_users,
                   COALESCE(MAX(created_at), '') AS last_request_at
            FROM jarvis_history
        """)
        ai_summary = dict(cur.fetchone())

        cur.execute("""
            SELECT u.name, u.email, COUNT(j.id) AS request_count
            FROM users u
            JOIN jarvis_history j ON j.user_id = u.id
            GROUP BY u.id, u.name, u.email
            ORDER BY request_count DESC, u.name ASC
            LIMIT 8
        """)
        top_ai_users = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    return render_template(
        "admin_analytics.html",
        admin_user=developer_user,
        admin_nav_route="admin_analytics",
        metrics=metrics,
        charts=charts,
        ai_summary=ai_summary,
        top_ai_users=top_ai_users,
    )


@app.route("/admin/reports")
@developer_required
def admin_reports():

    developer_user = g.developer_user

    conn = get_db()
    try:
        cur = conn.cursor()
        report_payload = build_admin_reports_payload(cur)
    finally:
        conn.close()

    export_format = (request.args.get("format") or "").strip().lower()
    if export_format == "csv":
        return build_admin_csv_response(report_payload)
    if export_format == "pdf":
        return build_admin_pdf_response(report_payload)

    return render_template(
        "admin_reports.html",
        admin_user=developer_user,
        admin_nav_route="admin_reports",
        report_payload=report_payload,
    )


@app.route("/admin/system")
@developer_required
def admin_system():

    developer_user = g.developer_user

    conn = get_db()
    try:
        cur = conn.cursor()
        system_health = build_admin_system_health(cur)
        settings_snapshot = build_admin_settings_snapshot(cur)
    finally:
        conn.close()

    return render_template(
        "admin_system.html",
        admin_user=developer_user,
        admin_nav_route="admin_system",
        system_health=system_health,
        settings_snapshot=settings_snapshot,
    )


@app.route("/admin/settings")
@developer_required
def admin_settings():

    return redirect(url_for("admin_dashboard", _anchor="settings"))

# ---------------- ERROR PAGE ---------------- #

@app.errorhandler(403)
def page_forbidden(error):

    return render_template("403.html"), 403

@app.errorhandler(404)
def page_not_found(error):

    return render_template("404.html"),404


def bootstrap_application():

    init_db()


# ---------------- MAIN ---------------- #

bootstrap_application()

if __name__=="__main__":

    flask_env = str(os.getenv("FLASK_ENV", "development")).strip().lower()
    debug_flag = str(os.getenv("FLASK_DEBUG", "")).strip().lower()
    debug_mode = debug_flag in {"1", "true", "yes", "on"} if debug_flag else flask_env == "development"
    render_port = int(os.getenv("PORT", "5000"))

    app.run(

        host="0.0.0.0",
        port=render_port,
        debug=debug_mode

    )