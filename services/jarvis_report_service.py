from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import current_app
from flask_mail import Message
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from services.finance_context import get_finance_context

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "finance.db")))


def _get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_user_identity(user_id: int) -> dict[str, str] | None:
    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()
            cur.execute("SELECT name, email FROM users WHERE id=?", (user_id,))
            row = cur.fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to load user identity for user_id=%s.", user_id)
        return None

    if not row:
        return None

    return {
        "name": str(row["name"] or "User"),
        "email": str(row["email"] or "").strip(),
    }


def _format_currency(value: object) -> str:
    return f"Rs. {_safe_float(value):,.2f}"


def _build_report_payload(user_id: int) -> dict[str, object]:
    context = get_finance_context(user_id)
    user = get_user_identity(user_id)
    generated_at = datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC")
    report_id = f"JRV-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    overview = context.get("financial_overview", {}) or {}
    investment_summary = context.get("investment_summary", {}) or {}
    goal_summary = context.get("goal_summary", {}) or {}
    health = context.get("ai_health_score", {}) or {}
    dashboard = context.get("dashboard_analytics", {}) or {}

    recommendations = [
        str(item).strip()
        for item in (context.get("budget_recommendations", []) or [])
        if str(item).strip()
    ]
    if not recommendations:
        recommendations = ["No data-backed recommendations available yet."]

    health_tips = [
        str(item).strip()
        for item in (health.get("improvement_tips", []) or [])
        if str(item).strip()
    ]
    if health_tips:
        recommendations.extend(health_tips)

    return {
        "report_id": report_id,
        "generated_at": generated_at,
        "user": user or {"name": "User", "email": ""},
        "overview": overview,
        "category_summary": context.get("expense_category_summary", []) or [],
        "investment_summary": investment_summary,
        "goals": context.get("financial_goals", []) or [],
        "goal_summary": goal_summary,
        "health": health,
        "dashboard": dashboard,
        "recommendations": recommendations[:12],
    }


def build_markdown_report(user_id: int) -> dict[str, object]:
    payload = _build_report_payload(user_id)

    overview = payload["overview"]
    dashboard = payload["dashboard"]
    investment_summary = payload["investment_summary"]
    goal_summary = payload["goal_summary"]
    health = payload["health"]

    top_categories = payload["category_summary"][:5]
    category_lines = "\n".join(
        [f"- {item.get('category', 'Uncategorized')}: {_format_currency(item.get('amount', 0))}" for item in top_categories]
    ) or "- No category spending data available"

    goal_lines = "\n".join([
        f"- {goal.get('goal_name', 'Unnamed Goal')}: {_format_currency(goal.get('saved_amount', 0))} / {_format_currency(goal.get('target_amount', 0))} ({_safe_float(goal.get('progress_percent', 0)):.2f}%)"
        for goal in payload["goals"][:5]
    ]) or "- No financial goals found"

    recommendation_lines = "\n".join([f"- {item}" for item in payload["recommendations"]])

    markdown = (
        f"## Financial Report\n"
        f"Report ID: **{payload['report_id']}**  \n"
        f"Generated At: **{payload['generated_at']}**\n\n"
        f"### Income Summary\n"
        f"- Total Income: {_format_currency(overview.get('total_income', 0))}\n\n"
        f"### Expense Analysis\n"
        f"- Total Expense: {_format_currency(overview.get('total_expense', 0))}\n"
        f"- This Month Change: {_format_currency(dashboard.get('monthly_change_amount', 0))} ({_safe_float(dashboard.get('monthly_change_percent', 0)):.2f}%)\n"
        f"- Highest Spending Category: {dashboard.get('highest_spending_category', 'No Data')} ({_format_currency(dashboard.get('highest_spending_amount', 0))})\n"
        f"- Category Breakdown:\n{category_lines}\n\n"
        f"### Budget Health\n"
        f"- Current Budget: {_format_currency(overview.get('current_budget', 0))}\n"
        f"- Remaining Budget: {_format_currency(overview.get('remaining_budget', 0))}\n"
        f"- Budget Utilization: {_safe_float(overview.get('budget_utilization_percent', 0)):.2f}%\n"
        f"- Status: {dashboard.get('budget_health_status', 'Unknown')}\n\n"
        f"### Savings\n"
        f"- Net Savings: {_format_currency(overview.get('total_savings', 0))}\n"
        f"- Savings Ratio: {_safe_float(health.get('savings_ratio', 0)):.2f}%\n\n"
        f"### Investment Analysis\n"
        f"- Total Invested: {_format_currency(investment_summary.get('total_investment', 0))}\n"
        f"- Current Value: {_format_currency(investment_summary.get('current_value', 0))}\n"
        f"- Profit/Loss: {_format_currency(investment_summary.get('total_profit', 0))}\n"
        f"- ROI: {_safe_float(investment_summary.get('roi', 0)):.2f}%\n\n"
        f"### Goal Progress\n"
        f"- Active Goals: {int(goal_summary.get('active_goals', 0) or 0)}\n"
        f"- Completed Goals: {int(goal_summary.get('completed_goals', 0) or 0)}\n"
        f"- Overall Progress: {_safe_float(goal_summary.get('overall_progress', 0)):.2f}%\n"
        f"- Goal Details:\n{goal_lines}\n\n"
        f"### Financial Score\n"
        f"- Score: **{int(health.get('score', 0) or 0)}/100**\n"
        f"- Status: **{health.get('status', 'Needs Improvement')}**\n\n"
        f"### AI Recommendations\n"
        f"{recommendation_lines}"
    )

    return {
        "report_id": payload["report_id"],
        "generated_at": payload["generated_at"],
        "markdown": markdown,
    }


def generate_pdf_bytes(user_id: int) -> tuple[bytes, str, str]:
    payload = _build_report_payload(user_id)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title="Smart Finance Insights - Jarvis Financial Report",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "JarvisTitle",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=6,
    )
    section_style = ParagraphStyle(
        "JarvisSection",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1D4ED8"),
        spaceBefore=8,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "JarvisBody",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1F2937"),
    )

    overview = payload["overview"]
    dashboard = payload["dashboard"]
    investment_summary = payload["investment_summary"]
    goal_summary = payload["goal_summary"]
    health = payload["health"]

    elements = [
        Paragraph("Smart Finance Insights", styles["Title"]),
        Paragraph("Professional Financial Report", title_style),
        Paragraph(f"Report ID: {payload['report_id']}", body_style),
        Paragraph(f"Generated At: {payload['generated_at']}", body_style),
        Paragraph(f"Prepared For: {payload['user'].get('name', 'User')}", body_style),
        Spacer(1, 12),
    ]

    summary_table = Table([
        ["Income Summary", _format_currency(overview.get("total_income", 0))],
        ["Expense Analysis", _format_currency(overview.get("total_expense", 0))],
        ["Budget Health", f"{_safe_float(overview.get('budget_utilization_percent', 0)):.2f}% ({dashboard.get('budget_health_status', 'Unknown')})"],
        ["Savings", _format_currency(overview.get("total_savings", 0))],
        ["Investment Analysis", f"ROI {_safe_float(investment_summary.get('roi', 0)):.2f}% | Value {_format_currency(investment_summary.get('current_value', 0))}"],
        ["Goal Progress", f"{_safe_float(goal_summary.get('overall_progress', 0)):.2f}% ({int(goal_summary.get('completed_goals', 0) or 0)} completed)"],
        ["Financial Score", f"{int(health.get('score', 0) or 0)}/100 ({health.get('status', 'Needs Improvement')})"],
    ], colWidths=[2.2 * inch, 4.4 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#DBEAFE")),
        ("TEXTCOLOR", (0, 0), (1, -1), colors.HexColor("#0F172A")),
        ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("GRID", (0, 0), (1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (1, -1), 8),
        ("RIGHTPADDING", (0, 0), (1, -1), 8),
        ("TOPPADDING", (0, 0), (1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (1, -1), 6),
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("Expense Analysis", section_style))
    category_rows = payload["category_summary"][:8]
    if category_rows:
        category_table_data = [["Category", "Amount"]]
        for item in category_rows:
            category_table_data.append([
                str(item.get("category", "Uncategorized")),
                _format_currency(item.get("amount", 0)),
            ])
        category_table = Table(category_table_data, colWidths=[3.3 * inch, 3.3 * inch])
        category_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("LEFTPADDING", (0, 0), (1, -1), 7),
            ("RIGHTPADDING", (0, 0), (1, -1), 7),
            ("TOPPADDING", (0, 0), (1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (1, -1), 5),
        ]))
        elements.append(category_table)
    else:
        elements.append(Paragraph("No expense category data available.", body_style))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("AI Recommendations", section_style))
    for recommendation in payload["recommendations"]:
        elements.append(Paragraph(f"- {recommendation}", body_style))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Goal Progress", section_style))
    goals = payload["goals"][:8]
    if goals:
        goal_table_data = [["Goal", "Saved", "Target", "Progress"]]
        for goal in goals:
            goal_table_data.append([
                str(goal.get("goal_name", "Unnamed Goal")),
                _format_currency(goal.get("saved_amount", 0)),
                _format_currency(goal.get("target_amount", 0)),
                f"{_safe_float(goal.get('progress_percent', 0)):.2f}%",
            ])
        goal_table = Table(goal_table_data, colWidths=[2.8 * inch, 1.2 * inch, 1.2 * inch, 1.4 * inch])
        goal_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (3, 0), colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (3, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (3, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("LEFTPADDING", (0, 0), (3, -1), 6),
            ("RIGHTPADDING", (0, 0), (3, -1), 6),
            ("TOPPADDING", (0, 0), (3, -1), 5),
            ("BOTTOMPADDING", (0, 0), (3, -1), 5),
        ]))
        elements.append(goal_table)
    else:
        elements.append(Paragraph("No goals data available.", body_style))

    document.build(elements)
    buffer.seek(0)
    filename = f"jarvis_financial_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return buffer.getvalue(), filename, payload["report_id"]


def email_report(user_id: int) -> tuple[bool, str]:
    user = get_user_identity(user_id)
    if not user or not user.get("email"):
        return False, "Account email is missing."

    pdf_data, filename, report_id = generate_pdf_bytes(user_id)
    generated_at = datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC")

    html_body = (
        f"<p>Hello {user.get('name', 'User')},</p>"
        f"<p>Your Smart Finance Insights professional financial report is attached as a PDF.</p>"
        f"<p><strong>Report ID:</strong> {report_id}<br>"
        f"<strong>Generated At:</strong> {generated_at}</p>"
    )

    message = Message(
        subject="Your Jarvis Financial Report",
        recipients=[user["email"]],
        html=html_body,
        sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
    )
    message.attach(filename, "application/pdf", pdf_data)

    try:
        mail_ext = current_app.extensions.get("mail")
        if mail_ext is None:
            return False, "Mail extension is not configured."
        mail_ext.send(message)
        return True, f"Report emailed to {user['email']}."
    except Exception:
        logger.exception("Failed to send Jarvis report email for user_id=%s.", user_id)
        return False, "Unable to send report email right now."
