from __future__ import annotations

from io import BytesIO

from flask import Blueprint, jsonify, request, send_file, session

from services.gemini_service import generate_chat_reply, test_connection
from services.jarvis_attachment_service import (
    delete_attachment,
    list_attachments,
    load_attachment_context,
    save_attachment,
)
from services.jarvis_history_service import (
    clear_conversations,
    delete_conversation,
    delete_thread,
    export_conversations,
    get_conversation_threads,
    get_recent_conversations,
    get_thread_messages,
    rename_thread,
    get_timeline,
    save_conversation,
    save_conversation_v2,
    search_conversations,
    set_conversation_feedback,
)
from services.jarvis_settings_service import get_settings, upsert_settings
from services.jarvis_report_service import build_markdown_report, email_report, generate_pdf_bytes
from services.jarvis_coach_service import generate_login_coach_briefing

jarvis_bp = Blueprint("jarvis", __name__, url_prefix="/api/jarvis")


def _parse_positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


@jarvis_bp.get("/test")
def jarvis_test():
    payload, status_code = test_connection()
    return jsonify(payload), status_code


@jarvis_bp.post("/chat")
def jarvis_chat():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")
    thread_id = str(payload.get("thread_id") or "").strip() or None
    attachment_ids = payload.get("attachment_ids") or []
    if not isinstance(attachment_ids, list):
        attachment_ids = []

    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    user_id_int = int(user_id)
    user_settings = get_settings(user_id_int)
    attachment_context = load_attachment_context(user_id_int, attachment_ids)
    prior_conversations = get_recent_conversations(user_id_int, limit=10)
    response_body, status_code = generate_chat_reply(
        message,
        user_id_int,
        history_context=prior_conversations,
        user_settings=user_settings,
        attachments_context=attachment_context,
    )

    if status_code == 200 and "reply" in response_body:
        title = payload.get("conversation_title") or message
        history_id = save_conversation_v2(
            user_id_int,
            message,
            response_body["reply"],
            thread_id=thread_id,
            conversation_title=str(title),
        )
        if history_id is None:
            history_id = save_conversation(user_id_int, message, response_body["reply"])
        if history_id is not None:
            response_body["history_id"] = history_id
        if thread_id:
            response_body["thread_id"] = thread_id

    return jsonify(response_body), status_code


@jarvis_bp.get("/history")
def jarvis_history():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    query = (request.args.get("q") or "").strip()
    limit = _parse_positive_int(request.args.get("limit"), 50)
    user_id_int = int(user_id)

    if query:
        conversations = search_conversations(user_id_int, query, limit=limit)
    else:
        conversations = get_recent_conversations(user_id_int, limit=limit)

    return jsonify({"conversations": conversations}), 200


@jarvis_bp.delete("/history")
def jarvis_clear_history():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    deleted = clear_conversations(int(user_id))
    return jsonify({"status": "cleared", "deleted": deleted}), 200


@jarvis_bp.delete("/history/<int:conversation_id>")
def jarvis_delete_history_item(conversation_id: int):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    deleted = delete_conversation(int(user_id), int(conversation_id))
    if not deleted:
        return jsonify({"error": "Conversation not found."}), 404

    return jsonify({"status": "deleted", "id": conversation_id}), 200


@jarvis_bp.post("/history/<int:conversation_id>/feedback")
def jarvis_feedback(conversation_id: int):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    payload = request.get_json(silent=True) or {}
    feedback = payload.get("feedback", "")
    ok = set_conversation_feedback(int(user_id), int(conversation_id), str(feedback))
    if not ok:
        return jsonify({"error": "Unable to set feedback."}), 400

    return jsonify({"status": "ok", "feedback": str(feedback).strip().lower()}), 200


@jarvis_bp.get("/history/timeline")
def jarvis_history_timeline():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    days = _parse_positive_int(request.args.get("days"), 30)
    timeline = get_timeline(int(user_id), limit_days=days)
    return jsonify({"timeline": timeline}), 200


@jarvis_bp.get("/history/export")
def jarvis_history_export():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    payload = export_conversations(int(user_id))
    return jsonify(payload), 200


@jarvis_bp.get("/history/threads")
def jarvis_history_threads():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    limit = _parse_positive_int(request.args.get("limit"), 80)
    items = get_conversation_threads(int(user_id), limit=limit)
    return jsonify({"threads": items}), 200


@jarvis_bp.get("/history/threads/<thread_id>")
def jarvis_history_thread_messages(thread_id: str):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    limit = _parse_positive_int(request.args.get("limit"), 120)
    items = get_thread_messages(int(user_id), str(thread_id), limit=limit)
    return jsonify({"messages": items}), 200


@jarvis_bp.patch("/history/threads/<thread_id>")
def jarvis_history_thread_rename(thread_id: str):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required."}), 400

    ok = rename_thread(int(user_id), str(thread_id), title)
    if not ok:
        return jsonify({"error": "Thread not found."}), 404

    return jsonify({"status": "ok", "thread_id": thread_id, "title": title}), 200


@jarvis_bp.delete("/history/threads/<thread_id>")
def jarvis_history_thread_delete(thread_id: str):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    deleted = delete_thread(int(user_id), str(thread_id))
    if deleted <= 0:
        return jsonify({"error": "Thread not found."}), 404
    return jsonify({"status": "deleted", "thread_id": thread_id, "deleted": deleted}), 200


@jarvis_bp.get("/settings")
def jarvis_settings_get():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    payload = get_settings(int(user_id))
    return jsonify(payload), 200


@jarvis_bp.put("/settings")
def jarvis_settings_put():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    payload = request.get_json(silent=True) or {}
    saved = upsert_settings(int(user_id), payload)
    return jsonify(saved), 200


@jarvis_bp.get("/attachments")
def jarvis_attachments_list():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    limit = _parse_positive_int(request.args.get("limit"), 20)
    files = list_attachments(int(user_id), limit=limit)
    return jsonify({"attachments": files}), 200


@jarvis_bp.post("/attachments")
def jarvis_attachments_upload():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    uploaded = request.files.get("file")
    record, error = save_attachment(int(user_id), uploaded)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"attachment": record}), 200


@jarvis_bp.delete("/attachments/<int:attachment_id>")
def jarvis_attachments_delete(attachment_id: int):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    ok = delete_attachment(int(user_id), int(attachment_id))
    if not ok:
        return jsonify({"error": "Attachment not found."}), 404
    return jsonify({"status": "deleted", "id": int(attachment_id)}), 200


@jarvis_bp.get("/report/summary")
def jarvis_report_summary():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    payload = build_markdown_report(int(user_id))
    return jsonify(payload), 200


@jarvis_bp.get("/report/pdf")
def jarvis_report_pdf():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    pdf_bytes, filename, _report_id = generate_pdf_bytes(int(user_id))
    return send_file(
        BytesIO(pdf_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


@jarvis_bp.post("/report/email")
def jarvis_report_email():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    sent, message = email_report(int(user_id))
    if sent:
        return jsonify({"status": "sent", "message": message}), 200

    return jsonify({"error": message}), 502


@jarvis_bp.get("/coach/state")
def jarvis_coach_state():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    return jsonify({"coach_pending": bool(session.get("jarvis_coach_pending", False))}), 200


@jarvis_bp.post("/coach/briefing")
def jarvis_coach_briefing():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401

    payload = generate_login_coach_briefing(int(user_id))
    session["jarvis_coach_pending"] = False
    return jsonify(payload), 200