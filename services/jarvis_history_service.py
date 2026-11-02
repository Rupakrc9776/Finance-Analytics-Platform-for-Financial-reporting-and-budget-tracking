from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "finance.db")))


def _get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def save_conversation(user_id: int, question: str, answer: str) -> int | None:
    if user_id <= 0 or not question.strip() or not answer.strip():
        return None

    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute(
                """
                INSERT INTO jarvis_history(user_id, question, answer)
                VALUES(?,?,?)
                """,
                (user_id, question.strip(), answer.strip()),
            )
            connection.commit()
            return int(cur.lastrowid)
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to save JARVIS conversation for user_id=%s.", user_id)
    except Exception:
        logger.exception("Unexpected error while saving JARVIS conversation for user_id=%s.", user_id)

    return None


def _ensure_history_columns(connection: sqlite3.Connection) -> None:
    cur = connection.cursor()
    cur.execute("PRAGMA table_info(jarvis_history)")
    existing = {str(row[1]) for row in cur.fetchall()}

    if "conversation_title" not in existing:
        cur.execute("ALTER TABLE jarvis_history ADD COLUMN conversation_title TEXT")
    if "thread_id" not in existing:
        cur.execute("ALTER TABLE jarvis_history ADD COLUMN thread_id TEXT")


def save_conversation_v2(
    user_id: int,
    question: str,
    answer: str,
    thread_id: str | None = None,
    conversation_title: str | None = None,
) -> int | None:
    if user_id <= 0 or not question.strip() or not answer.strip():
        return None

    thread_value = (thread_id or "").strip() or None
    title_value = (conversation_title or "").strip() or question.strip()[:72]

    try:
        connection = _get_db_connection()
        try:
            _ensure_history_columns(connection)
            cur = connection.cursor()
            cur.execute(
                """
                INSERT INTO jarvis_history(user_id, question, answer, conversation_title, thread_id)
                VALUES(?,?,?,?,?)
                """,
                (user_id, question.strip(), answer.strip(), title_value, thread_value),
            )
            connection.commit()
            return int(cur.lastrowid)
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to save v2 JARVIS conversation for user_id=%s.", user_id)
    except Exception:
        logger.exception("Unexpected error while saving v2 JARVIS conversation for user_id=%s.", user_id)

    return None


def get_recent_conversations(user_id: int, limit: int = 10) -> list[dict[str, object]]:
    max_items = max(1, min(int(limit), 100))

    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute(
                """
                                SELECT h.id, h.question, h.answer, h.created_at, f.feedback,
                                       COALESCE(h.conversation_title, h.question) AS conversation_title,
                                       COALESCE(h.thread_id, 'legacy-' || h.id) AS thread_id
                                FROM jarvis_history h
                                LEFT JOIN jarvis_feedback f
                                    ON f.user_id = h.user_id AND f.conversation_id = h.id
                                WHERE h.user_id=?
                                ORDER BY h.id DESC
                LIMIT ?
                """,
                (user_id, max_items),
            )
            rows = cur.fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to read recent JARVIS conversations for user_id=%s.", user_id)
        return []
    except Exception:
        logger.exception("Unexpected error while reading recent JARVIS conversations for user_id=%s.", user_id)
        return []

    return [
        {
            "id": int(row["id"]),
            "question": row["question"],
            "answer": row["answer"],
            "created_at": row["created_at"],
            "feedback": row["feedback"],
            "conversation_title": row["conversation_title"],
            "thread_id": row["thread_id"],
        }
        for row in rows
    ]


def get_conversation_threads(user_id: int, limit: int = 80) -> list[dict[str, object]]:
    max_items = max(1, min(int(limit), 200))
    try:
        connection = _get_db_connection()
        try:
            _ensure_history_columns(connection)
            cur = connection.cursor()
            cur.execute(
                """
                SELECT
                    COALESCE(thread_id, 'legacy-' || id) AS thread_key,
                    MAX(id) AS latest_id,
                    MAX(created_at) AS last_message_at,
                    MIN(created_at) AS first_message_at,
                    COUNT(*) AS message_count,
                    COALESCE(MAX(conversation_title), MAX(question)) AS title
                FROM jarvis_history
                WHERE user_id=?
                GROUP BY thread_key
                ORDER BY latest_id DESC
                LIMIT ?
                """,
                (user_id, max_items),
            )
            rows = cur.fetchall()
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to get JARVIS threads for user_id=%s", user_id)
        return []

    return [
        {
            "thread_id": row["thread_key"],
            "title": (row["title"] or "Conversation")[:100],
            "latest_id": int(row["latest_id"] or 0),
            "message_count": int(row["message_count"] or 0),
            "first_message_at": row["first_message_at"],
            "last_message_at": row["last_message_at"],
        }
        for row in rows
    ]


def get_thread_messages(user_id: int, thread_id: str, limit: int = 120) -> list[dict[str, object]]:
    max_items = max(1, min(int(limit), 400))
    thread_value = (thread_id or "").strip()
    if not thread_value:
        return []

    try:
        connection = _get_db_connection()
        try:
            _ensure_history_columns(connection)
            cur = connection.cursor()
            if thread_value.startswith("legacy-"):
                legacy_id = int(thread_value.replace("legacy-", "", 1) or "0")
                cur.execute(
                    """
                    SELECT h.id, h.question, h.answer, h.created_at, f.feedback,
                           COALESCE(h.conversation_title, h.question) AS conversation_title,
                           h.thread_id
                    FROM jarvis_history h
                    LEFT JOIN jarvis_feedback f
                        ON f.user_id = h.user_id AND f.conversation_id = h.id
                    WHERE h.user_id=? AND h.id=?
                    ORDER BY h.id ASC
                    LIMIT ?
                    """,
                    (user_id, legacy_id, max_items),
                )
            else:
                cur.execute(
                    """
                    SELECT h.id, h.question, h.answer, h.created_at, f.feedback,
                           COALESCE(h.conversation_title, h.question) AS conversation_title,
                           h.thread_id
                    FROM jarvis_history h
                    LEFT JOIN jarvis_feedback f
                        ON f.user_id = h.user_id AND f.conversation_id = h.id
                    WHERE h.user_id=? AND h.thread_id=?
                    ORDER BY h.id ASC
                    LIMIT ?
                    """,
                    (user_id, thread_value, max_items),
                )
            rows = cur.fetchall()
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to read thread messages user_id=%s thread_id=%s", user_id, thread_id)
        return []

    return [
        {
            "id": int(row["id"]),
            "question": row["question"],
            "answer": row["answer"],
            "created_at": row["created_at"],
            "feedback": row["feedback"],
            "conversation_title": row["conversation_title"],
            "thread_id": row["thread_id"],
        }
        for row in rows
    ]


def rename_thread(user_id: int, thread_id: str, title: str) -> bool:
    new_title = (title or "").strip()[:100]
    if not new_title:
        return False
    thread_value = (thread_id or "").strip()
    if not thread_value:
        return False

    try:
        connection = _get_db_connection()
        try:
            _ensure_history_columns(connection)
            cur = connection.cursor()
            if thread_value.startswith("legacy-"):
                legacy_id = int(thread_value.replace("legacy-", "", 1) or "0")
                cur.execute(
                    "UPDATE jarvis_history SET conversation_title=? WHERE user_id=? AND id=?",
                    (new_title, user_id, legacy_id),
                )
            else:
                cur.execute(
                    "UPDATE jarvis_history SET conversation_title=? WHERE user_id=? AND thread_id=?",
                    (new_title, user_id, thread_value),
                )
            updated = cur.rowcount > 0
            connection.commit()
            return updated
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to rename thread user_id=%s thread_id=%s", user_id, thread_id)
        return False


def delete_thread(user_id: int, thread_id: str) -> int:
    thread_value = (thread_id or "").strip()
    if not thread_value:
        return 0

    try:
        connection = _get_db_connection()
        try:
            _ensure_history_columns(connection)
            cur = connection.cursor()
            if thread_value.startswith("legacy-"):
                legacy_id = int(thread_value.replace("legacy-", "", 1) or "0")
                cur.execute("DELETE FROM jarvis_feedback WHERE user_id=? AND conversation_id=?", (user_id, legacy_id))
                cur.execute("DELETE FROM jarvis_history WHERE user_id=? AND id=?", (user_id, legacy_id))
                deleted = int(cur.rowcount)
            else:
                cur.execute(
                    "SELECT id FROM jarvis_history WHERE user_id=? AND thread_id=?",
                    (user_id, thread_value),
                )
                ids = [int(row[0]) for row in cur.fetchall()]
                if ids:
                    placeholders = ",".join(["?"] * len(ids))
                    cur.execute(
                        f"DELETE FROM jarvis_feedback WHERE user_id=? AND conversation_id IN ({placeholders})",
                        [user_id, *ids],
                    )
                cur.execute(
                    "DELETE FROM jarvis_history WHERE user_id=? AND thread_id=?",
                    (user_id, thread_value),
                )
                deleted = int(cur.rowcount)

            connection.commit()
            return deleted
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to delete thread user_id=%s thread_id=%s", user_id, thread_id)
        return 0


def search_conversations(user_id: int, query: str, limit: int = 50) -> list[dict[str, object]]:
    text = (query or "").strip()
    if not text:
        return get_recent_conversations(user_id, limit=limit)

    max_items = max(1, min(int(limit), 200))
    like_query = f"%{text}%"

    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute(
                """
                                SELECT h.id, h.question, h.answer, h.created_at, f.feedback,
                                       COALESCE(h.conversation_title, h.question) AS conversation_title,
                                       COALESCE(h.thread_id, 'legacy-' || h.id) AS thread_id
                                FROM jarvis_history h
                                LEFT JOIN jarvis_feedback f
                                    ON f.user_id = h.user_id AND f.conversation_id = h.id
                                WHERE h.user_id=?
                                    AND (h.question LIKE ? OR h.answer LIKE ?)
                                ORDER BY h.id DESC
                LIMIT ?
                """,
                (user_id, like_query, like_query, max_items),
            )
            rows = cur.fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to search JARVIS conversations for user_id=%s.", user_id)
        return []
    except Exception:
        logger.exception("Unexpected error while searching JARVIS conversations for user_id=%s.", user_id)
        return []

    return [
        {
            "id": int(row["id"]),
            "question": row["question"],
            "answer": row["answer"],
            "created_at": row["created_at"],
            "feedback": row["feedback"],
            "conversation_title": row["conversation_title"],
            "thread_id": row["thread_id"],
        }
        for row in rows
    ]


def delete_conversation(user_id: int, conversation_id: int) -> bool:
    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute(
                "DELETE FROM jarvis_feedback WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            )
            cur.execute(
                "DELETE FROM jarvis_history WHERE user_id=? AND id=?",
                (user_id, conversation_id),
            )
            connection.commit()
            return cur.rowcount > 0
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to delete JARVIS conversation id=%s for user_id=%s.", conversation_id, user_id)
    except Exception:
        logger.exception("Unexpected error while deleting JARVIS conversation id=%s for user_id=%s.", conversation_id, user_id)

    return False


def clear_conversations(user_id: int) -> int:
    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute("DELETE FROM jarvis_feedback WHERE user_id=?", (user_id,))
            cur.execute("DELETE FROM jarvis_history WHERE user_id=?", (user_id,))
            deleted = int(cur.rowcount)
            connection.commit()
            return deleted
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to clear JARVIS conversations for user_id=%s.", user_id)
    except Exception:
        logger.exception("Unexpected error while clearing JARVIS conversations for user_id=%s.", user_id)

    return 0


def get_timeline(user_id: int, limit_days: int = 30) -> list[dict[str, object]]:
    days = max(1, min(int(limit_days), 365))

    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS conversation_count,
                       MIN(created_at) AS first_message_at,
                       MAX(created_at) AS last_message_at
                FROM jarvis_history
                WHERE user_id=?
                  AND date(created_at) >= date('now', ?)
                GROUP BY day
                ORDER BY day DESC
                """,
                (user_id, f"-{days} day"),
            )
            rows = cur.fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to build JARVIS timeline for user_id=%s.", user_id)
        return []
    except Exception:
        logger.exception("Unexpected error while building JARVIS timeline for user_id=%s.", user_id)
        return []

    return [
        {
            "day": row["day"],
            "conversation_count": int(row["conversation_count"] or 0),
            "first_message_at": row["first_message_at"],
            "last_message_at": row["last_message_at"],
        }
        for row in rows
    ]


def export_conversations(user_id: int) -> dict[str, object]:
    conversations = get_recent_conversations(user_id, limit=1000)
    ordered = list(reversed(conversations))
    return {
        "exported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "conversation_count": len(ordered),
        "conversations": ordered,
    }


def set_conversation_feedback(user_id: int, conversation_id: int, feedback: str | None) -> bool:
    normalized = (feedback or "").strip().lower()
    if normalized not in {"like", "dislike", ""}:
        return False

    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute(
                "SELECT 1 FROM jarvis_history WHERE user_id=? AND id=? LIMIT 1",
                (user_id, conversation_id),
            )
            if cur.fetchone() is None:
                return False

            if normalized == "":
                cur.execute(
                    "DELETE FROM jarvis_feedback WHERE user_id=? AND conversation_id=?",
                    (user_id, conversation_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO jarvis_feedback(user_id, conversation_id, feedback)
                    VALUES(?,?,?)
                    ON CONFLICT(user_id, conversation_id)
                    DO UPDATE SET feedback=excluded.feedback, created_at=CURRENT_TIMESTAMP
                    """,
                    (user_id, conversation_id, normalized),
                )

            connection.commit()
            return True
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to set JARVIS feedback for user_id=%s conversation_id=%s.", user_id, conversation_id)
    except Exception:
        logger.exception("Unexpected error setting JARVIS feedback for user_id=%s conversation_id=%s.", user_id, conversation_id)

    return False
