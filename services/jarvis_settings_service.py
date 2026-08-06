from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DATABASE_PATH = Path(__file__).resolve().parent.parent / "finance.db"

DEFAULT_SETTINGS: dict[str, object] = {
    "response_style": "balanced",
    "language": "en",
    "voice_enabled": True,
    "voice_gender": "auto",
    "speech_rate": 1.0,
    "pitch": 1.0,
    "temperature": 0.3,
    "theme_preference": "system",
}


ALLOWED_RESPONSE_STYLES = {"precise", "balanced", "creative"}
ALLOWED_LANGUAGES = {"en", "hi", "bn"}
ALLOWED_VOICES = {"auto", "female", "male"}
ALLOWED_THEMES = {"system", "light", "dark"}


def _get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_settings_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
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
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_jarvis_settings_user ON jarvis_settings(user_id)"
    )


def _normalize(payload: dict[str, object] | None) -> dict[str, object]:
    data = dict(DEFAULT_SETTINGS)
    if not payload:
        return data

    response_style = str(payload.get("response_style", data["response_style"]))
    language = str(payload.get("language", data["language"]))
    voice_gender = str(payload.get("voice_gender", data["voice_gender"]))
    theme_preference = str(payload.get("theme_preference", data["theme_preference"]))

    if response_style in ALLOWED_RESPONSE_STYLES:
        data["response_style"] = response_style
    if language in ALLOWED_LANGUAGES:
        data["language"] = language
    if voice_gender in ALLOWED_VOICES:
        data["voice_gender"] = voice_gender
    if theme_preference in ALLOWED_THEMES:
        data["theme_preference"] = theme_preference

    try:
        data["voice_enabled"] = bool(payload.get("voice_enabled", data["voice_enabled"]))
    except Exception:
        pass

    try:
        speech_rate = float(payload.get("speech_rate", data["speech_rate"]))
        data["speech_rate"] = max(0.7, min(speech_rate, 1.6))
    except (TypeError, ValueError):
        pass

    try:
        pitch = float(payload.get("pitch", data["pitch"]))
        data["pitch"] = max(0.7, min(pitch, 1.6))
    except (TypeError, ValueError):
        pass

    try:
        temperature = float(payload.get("temperature", data["temperature"]))
        data["temperature"] = max(0.0, min(temperature, 1.0))
    except (TypeError, ValueError):
        pass

    return data


def get_settings(user_id: int) -> dict[str, object]:
    try:
        connection = _get_db_connection()
        try:
            _ensure_settings_table(connection)
            cur = connection.cursor()
            cur.execute("SELECT * FROM jarvis_settings WHERE user_id=? LIMIT 1", (user_id,))
            row = cur.fetchone()
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to read jarvis settings for user_id=%s", user_id)
        return dict(DEFAULT_SETTINGS)

    if row is None:
        return dict(DEFAULT_SETTINGS)

    return {
        "response_style": row["response_style"],
        "language": row["language"],
        "voice_enabled": bool(row["voice_enabled"]),
        "voice_gender": row["voice_gender"],
        "speech_rate": float(row["speech_rate"]),
        "pitch": float(row["pitch"]),
        "temperature": float(row["temperature"]),
        "theme_preference": row["theme_preference"],
    }


def upsert_settings(user_id: int, payload: dict[str, object] | None) -> dict[str, object]:
    normalized = _normalize(payload)

    try:
        connection = _get_db_connection()
        try:
            _ensure_settings_table(connection)
            cur = connection.cursor()
            cur.execute(
                """
                INSERT INTO jarvis_settings(
                    user_id, response_style, language, voice_enabled, voice_gender,
                    speech_rate, pitch, temperature, theme_preference
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    response_style=excluded.response_style,
                    language=excluded.language,
                    voice_enabled=excluded.voice_enabled,
                    voice_gender=excluded.voice_gender,
                    speech_rate=excluded.speech_rate,
                    pitch=excluded.pitch,
                    temperature=excluded.temperature,
                    theme_preference=excluded.theme_preference,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    str(normalized["response_style"]),
                    str(normalized["language"]),
                    1 if bool(normalized["voice_enabled"]) else 0,
                    str(normalized["voice_gender"]),
                    float(normalized["speech_rate"]),
                    float(normalized["pitch"]),
                    float(normalized["temperature"]),
                    str(normalized["theme_preference"]),
                ),
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to save jarvis settings for user_id=%s", user_id)

    return get_settings(user_id)
