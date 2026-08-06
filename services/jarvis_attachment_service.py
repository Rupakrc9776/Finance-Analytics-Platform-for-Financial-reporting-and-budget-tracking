from __future__ import annotations

import csv
import io
import logging
import mimetypes
import os
import sqlite3
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "finance.db")))
ATTACHMENT_DIR = BASE_DIR / "static" / "uploads" / "jarvis"
ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "csv", "xlsx", "xls", "png", "jpg", "jpeg", "webp"}
MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024


def _get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
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
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_jarvis_attachments_user_date ON jarvis_attachments(user_id, created_at)"
    )


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def _read_csv_text(content: bytes) -> str:
    stream = io.StringIO(content.decode("utf-8", errors="ignore"))
    reader = csv.reader(stream)
    rows: list[str] = []
    for idx, row in enumerate(reader):
        if idx >= 60:
            break
        rows.append(" | ".join(cell.strip() for cell in row[:16]))
    return "\n".join(rows)


def _read_excel_text(content: bytes) -> str:
    try:
        from openpyxl import load_workbook
    except Exception:
        return "Excel preview unavailable (openpyxl missing)."

    try:
        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        lines: list[str] = []
        for sheet in wb.worksheets[:3]:
            lines.append(f"[Sheet] {sheet.title}")
            for ridx, row in enumerate(sheet.iter_rows(values_only=True)):
                if ridx >= 25:
                    break
                values = [str(v).strip() for v in row[:10] if v is not None and str(v).strip()]
                if values:
                    lines.append(" | ".join(values))
        return "\n".join(lines)
    except Exception:
        return "Unable to extract Excel preview."


def _read_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return "PDF preview unavailable (pypdf missing)."

    try:
        reader = PdfReader(io.BytesIO(content))
        chunks: list[str] = []
        for page in reader.pages[:5]:
            txt = page.extract_text() or ""
            if txt.strip():
                chunks.append(txt.strip())
        joined = "\n".join(chunks)
        return joined[:8000]
    except Exception:
        return "Unable to extract PDF preview."


def _extract_preview(filename: str, content: bytes) -> str:
    ext = _extension(filename)
    if ext == "csv":
        return _read_csv_text(content)
    if ext in {"xlsx", "xls"}:
        return _read_excel_text(content)
    if ext == "pdf":
        return _read_pdf_text(content)
    if ext in {"png", "jpg", "jpeg", "webp"}:
        return f"Image uploaded: {filename}. Visual analysis can be requested by the user."
    return ""


def save_attachment(user_id: int, uploaded: FileStorage | None) -> tuple[dict[str, object] | None, str | None]:
    if uploaded is None or not uploaded.filename:
        return None, "No file selected."

    filename = secure_filename(uploaded.filename)
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        return None, "Unsupported file type."

    content = uploaded.read() or b""
    if len(content) > MAX_ATTACHMENT_SIZE:
        return None, "File exceeds 5MB limit."

    stored_name = f"{uuid.uuid4().hex}_{filename}"
    target = ATTACHMENT_DIR / stored_name
    target.write_bytes(content)

    mime_type = uploaded.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    preview = _extract_preview(filename, content)

    try:
        connection = _get_db_connection()
        try:
            _ensure_table(connection)
            cur = connection.cursor()
            cur.execute(
                """
                INSERT INTO jarvis_attachments(user_id, original_name, stored_name, mime_type, file_size, extracted_text)
                VALUES(?,?,?,?,?,?)
                """,
                (user_id, filename, stored_name, mime_type, len(content), preview[:12000]),
            )
            attachment_id = int(cur.lastrowid)
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to save attachment metadata for user_id=%s", user_id)
        return None, "Could not save attachment."

    return {
        "id": attachment_id,
        "name": filename,
        "mime_type": mime_type,
        "size": len(content),
        "preview": preview[:1200],
    }, None


def list_attachments(user_id: int, limit: int = 20) -> list[dict[str, object]]:
    try:
        connection = _get_db_connection()
        try:
            _ensure_table(connection)
            cur = connection.cursor()
            cur.execute(
                """
                SELECT id, original_name, mime_type, file_size, extracted_text, created_at
                FROM jarvis_attachments
                WHERE user_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, max(1, min(limit, 100))),
            )
            rows = cur.fetchall()
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to list attachments for user_id=%s", user_id)
        return []

    return [
        {
            "id": int(r["id"]),
            "name": r["original_name"],
            "mime_type": r["mime_type"],
            "size": int(r["file_size"] or 0),
            "preview": (r["extracted_text"] or "")[:220],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def delete_attachment(user_id: int, attachment_id: int) -> bool:
    try:
        connection = _get_db_connection()
        try:
            _ensure_table(connection)
            cur = connection.cursor()
            cur.execute(
                "SELECT stored_name FROM jarvis_attachments WHERE user_id=? AND id=? LIMIT 1",
                (user_id, attachment_id),
            )
            row = cur.fetchone()
            if row is None:
                return False

            stored_name = str(row["stored_name"])
            cur.execute(
                "DELETE FROM jarvis_attachments WHERE user_id=? AND id=?",
                (user_id, attachment_id),
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to delete attachment id=%s for user_id=%s", attachment_id, user_id)
        return False

    target = ATTACHMENT_DIR / stored_name
    if target.exists():
        try:
            target.unlink()
        except OSError:
            logger.warning("Could not delete attachment file %s", target)
    return True


def load_attachment_context(user_id: int, attachment_ids: list[int] | None) -> list[dict[str, object]]:
    ids = [int(x) for x in (attachment_ids or []) if isinstance(x, int) or str(x).isdigit()]
    if not ids:
        return []

    placeholders = ",".join(["?"] * len(ids))

    try:
        connection = _get_db_connection()
        try:
            _ensure_table(connection)
            cur = connection.cursor()
            cur.execute(
                f"""
                SELECT id, original_name, mime_type, extracted_text, created_at
                FROM jarvis_attachments
                WHERE user_id=? AND id IN ({placeholders})
                ORDER BY id DESC
                """,
                [user_id, *ids],
            )
            rows = cur.fetchall()
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to load attachment context for user_id=%s", user_id)
        return []

    return [
        {
            "id": int(r["id"]),
            "name": r["original_name"],
            "mime_type": r["mime_type"],
            "created_at": r["created_at"],
            "preview_text": (r["extracted_text"] or "")[:2500],
        }
        for r in rows
    ]
