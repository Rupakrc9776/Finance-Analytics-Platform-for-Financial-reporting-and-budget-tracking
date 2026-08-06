from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from google import genai
from google.genai.errors import APIError, ClientError, ServerError

from services.finance_context import get_finance_context

logger = logging.getLogger(__name__)

PROVIDER_NAME = "Google Gemini"
DEFAULT_MODEL_CANDIDATES = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
)


def _get_api_key() -> str | None:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    return api_key.strip() if api_key else None


def _get_model_candidates() -> tuple[str, ...]:
    configured_model = (os.getenv("GEMINI_MODEL") or "").strip()
    candidates: list[str] = []

    if configured_model:
        candidates.append(configured_model)

    for model_name in DEFAULT_MODEL_CANDIDATES:
        if model_name not in candidates:
            candidates.append(model_name)

    return tuple(candidates)


@lru_cache(maxsize=1)
def get_gemini_client() -> genai.Client | None:
    api_key = _get_api_key()
    if not api_key:
        logger.error("Gemini API key is not configured. Set GEMINI_API_KEY in the .env file.")
        return None

    try:
        return genai.Client(api_key=api_key)
    except (APIError, ClientError, ServerError):
        logger.exception("Failed to initialize Gemini client.")
    except Exception:
        logger.exception("Unexpected error while initializing Gemini client.")

    return None


def _build_error(message: str) -> dict[str, str]:
    return {"error": message}


def _build_chat_prompt(
    message: str,
    financial_context: dict[str, object],
    history_context: list[dict[str, object]],
    user_settings: dict[str, object] | None = None,
    attachments_context: list[dict[str, object]] | None = None,
) -> str:
    overview = financial_context.get("financial_overview", {})
    transactions = financial_context.get("last_10_transactions", [])
    categories = financial_context.get("expense_category_summary", [])
    investments = financial_context.get("investments", [])
    goals = financial_context.get("financial_goals", [])
    monthly_trends = financial_context.get("monthly_trends", [])
    health = financial_context.get("ai_health_score", {})
    analytics = financial_context.get("dashboard_analytics", {})
    actionable = financial_context.get("actionable_intelligence", {})

    highest_category = categories[0]["category"] if categories else "No Data"

    transaction_lines = [
        f"- {item.get('date') or 'Unknown date'} | {item.get('category') or 'Uncategorized'} | Rs. {float(item.get('amount') or 0):,.2f} | {item.get('description') or 'No description'}"
        for item in transactions
    ] or ["- No recent transactions available"]

    category_lines = [
        f"- {item.get('category') or 'Uncategorized'}: Rs. {float(item.get('amount') or 0):,.2f}"
        for item in categories
    ] or ["- No expense categories available"]

    investment_lines = [
        f"- {item.get('name') or 'Unnamed investment'} ({item.get('asset') or 'Unknown asset'}): invested Rs. {float(item.get('invested_amount') or 0):,.2f}, current value Rs. {float(item.get('current_value') or 0):,.2f}"
        for item in investments
    ] or ["- No investments available"]

    goal_lines = [
        f"- {item.get('goal_name') or 'Unnamed goal'}: saved Rs. {float(item.get('saved_amount') or 0):,.2f} of Rs. {float(item.get('target_amount') or 0):,.2f} ({float(item.get('progress_percent') or 0):,.2f}%)"
        for item in goals
    ] or ["- No financial goals available"]

    trend_lines = [
        f"- {item.get('month') or 'Unknown month'}: Rs. {float(item.get('expense') or 0):,.2f} expenses"
        for item in monthly_trends
    ] or ["- No monthly trend data available"]

    recommendation_lines = [
        f"- {item}"
        for item in (financial_context.get("budget_recommendations", []) or [])
        if isinstance(item, str) and item.strip()
    ] or ["- No recommendations available from current data"]

    memory_lines = []
    for entry in history_context[:10]:
        question = str(entry.get("question") or "").strip()
        answer = str(entry.get("answer") or "").strip()
        created_at = str(entry.get("created_at") or "")
        if question and answer:
            memory_lines.append(f"- {created_at} | Q: {question} | A: {answer}")

    if not memory_lines:
        memory_lines = ["- No previous conversations available"]

    transaction_block = "\n".join(transaction_lines)
    category_block = "\n".join(category_lines)
    investment_block = "\n".join(investment_lines)
    goal_block = "\n".join(goal_lines)
    trend_block = "\n".join(trend_lines)
    recommendation_block = "\n".join(recommendation_lines)
    memory_block = "\n".join(memory_lines)
    settings = user_settings or {}
    attachment_lines = []
    for item in attachments_context or []:
        name = str(item.get("name") or "Uploaded file")
        mime_type = str(item.get("mime_type") or "unknown")
        preview = str(item.get("preview_text") or "").strip()
        attachment_lines.append(f"- {name} ({mime_type})")
        if preview:
            attachment_lines.append(f"  Preview: {preview[:800]}")
    if not attachment_lines:
        attachment_lines = ["- No attachment context provided"]

    response_style = str(settings.get("response_style") or "balanced")
    language = str(settings.get("language") or "en")
    temperature = float(settings.get("temperature") or 0.3)
    settings_block = (
        f"- Response Style: {response_style}\n"
        f"- Language: {language}\n"
        f"- Temperature: {temperature:.2f}"
    )
    attachment_block = "\n".join(attachment_lines)

    return (
        "You are JARVIS, a financial assistant inside Smart Finance Insights. "
        "Answer only using the provided financial context. "
        "Use the provided financial context to answer accurately, clearly, and safely. "
        "Never invent financial numbers, assumptions, or events that are not present in the context. "
        "Never estimate a purchase price for bike, laptop, or any item when price is not provided by user. "
        "If a value is missing, unavailable, or zero due to no records, explicitly say it is unavailable. "
        "Do not mention database tables, raw SQL, or internal storage details. "
        "Provide professional formatting using headings and concise bullet points. "
        "Include markdown tables whenever they help compare numeric values. "
        "Give recommendations only when they are directly supported by provided values.\n\n"
        "Strict Response Rules:\n"
        "1) Use only numbers in context; do not infer hidden values.\n"
        "2) For 'How much did I spend?': include both spent_this_month and total_spent_all_time when available.\n"
        "3) For 'Which category costs the most?': use highest_spending_category and highest_spending_category_amount only.\n"
        "4) For 'Can I afford a bike/laptop?': if price missing, ask for exact price and show safe_purchase_limit and discretionary_purchase_capacity.\n"
        "5) For 'Predict next month expenses': use predicted_next_month_expense exactly.\n"
        "6) For 'Suggest better savings': use suggested_monthly_savings and additional_savings_needed.\n"
        "7) For 'Should I invest more?': use investment_roi and suggested_investment_topup.\n"
        "8) For 'How much emergency fund do I need?': use emergency_fund_required, emergency_fund_current, emergency_fund_gap.\n\n"
        "Current Financial Information:\n"
        f"Income: Rs. {float(overview.get('total_income') or 0):,.2f}\n"
        f"Expenses: Rs. {float(overview.get('total_expense') or 0):,.2f}\n"
        f"Savings: Rs. {float(overview.get('total_savings') or 0):,.2f}\n"
        f"Current Budget: Rs. {float(overview.get('current_budget') or 0):,.2f}\n"
        f"Remaining Budget: Rs. {float(overview.get('remaining_budget') or 0):,.2f}\n"
        f"Budget Utilization: {float(overview.get('budget_utilization_percent') or 0):,.2f}%\n"
        f"Top Category: {highest_category}\n"
        f"AI Financial Health Score: {int(health.get('score') or 0)}/100 ({health.get('status') or 'Unknown'})\n\n"
        "Dashboard Analytics:\n"
        f"- Transaction Count: {int(analytics.get('transaction_count') or 0)}\n"
        f"- Highest Spending Category: {analytics.get('highest_spending_category') or 'No Data'}\n"
        f"- Highest Spending Amount: Rs. {float(analytics.get('highest_spending_amount') or 0):,.2f}\n"
        f"- Monthly Change: Rs. {float(analytics.get('monthly_change_amount') or 0):,.2f} ({float(analytics.get('monthly_change_percent') or 0):,.2f}%)\n"
        f"- Budget Health Status: {analytics.get('budget_health_status') or 'Unknown'}\n"
        f"- Investment ROI: {float(analytics.get('investment_roi') or 0):,.2f}%\n"
        f"- Goal Progress: {float(analytics.get('goal_progress') or 0):,.2f}%\n\n"
        "Actionable Intelligence (Computed):\n"
        f"- Total Spent (All Time): Rs. {float(actionable.get('total_spent_all_time') or 0):,.2f}\n"
        f"- Spent This Month: Rs. {float(actionable.get('spent_this_month') or 0):,.2f}\n"
        f"- Spent Previous Month: Rs. {float(actionable.get('spent_previous_month') or 0):,.2f}\n"
        f"- Highest Spending Category: {actionable.get('highest_spending_category') or 'No Data'}\n"
        f"- Highest Spending Category Amount: Rs. {float(actionable.get('highest_spending_category_amount') or 0):,.2f}\n"
        f"- Predicted Next Month Expense: Rs. {float(actionable.get('predicted_next_month_expense') or 0):,.2f}\n"
        f"- Savings Per Month: Rs. {float(actionable.get('savings_per_month') or 0):,.2f}\n"
        f"- Suggested Monthly Savings: Rs. {float(actionable.get('suggested_monthly_savings') or 0):,.2f}\n"
        f"- Additional Savings Needed: Rs. {float(actionable.get('additional_savings_needed') or 0):,.2f}\n"
        f"- Investment ROI: {float(actionable.get('investment_roi') or 0):,.2f}%\n"
        f"- Suggested Investment Top-up: Rs. {float(actionable.get('suggested_investment_topup') or 0):,.2f}\n"
        f"- Goal Progress Percent: {float(actionable.get('goal_progress_percent') or 0):,.2f}%\n"
        f"- Budget Remaining: Rs. {float(actionable.get('budget_remaining') or 0):,.2f}\n"
        f"- Discretionary Purchase Capacity: Rs. {float(actionable.get('discretionary_purchase_capacity') or 0):,.2f}\n"
        f"- Safe Purchase Limit: Rs. {float(actionable.get('safe_purchase_limit') or 0):,.2f}\n"
        f"- Emergency Fund Required: Rs. {float(actionable.get('emergency_fund_required') or 0):,.2f}\n"
        f"- Emergency Fund Current: Rs. {float(actionable.get('emergency_fund_current') or 0):,.2f}\n"
        f"- Emergency Fund Gap: Rs. {float(actionable.get('emergency_fund_gap') or 0):,.2f}\n\n"
        "Recent Transactions:\n"
        f"{transaction_block}\n\n"
        "Expense Category Summary:\n"
        f"{category_block}\n\n"
        "Investment Portfolio:\n"
        f"{investment_block}\n\n"
        "Financial Goals:\n"
        f"{goal_block}\n\n"
        "Monthly Trends:\n"
        f"{trend_block}\n\n"
        "Previous Conversations (Last 10):\n"
        f"{memory_block}\n\n"
        "Context-Supported Recommendations:\n"
        f"{recommendation_block}\n\n"
        "User Assistant Preferences:\n"
        f"{settings_block}\n\n"
        "Attachment Context:\n"
        f"{attachment_block}\n\n"
        "User Question:\n"
        f"{message.strip()}"
    )


def _extract_reply_text(response: Any) -> str:
    direct_text = getattr(response, "text", None)
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        texts = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                texts.append(part_text.strip())
        if texts:
            return "\n".join(texts)

    return ""


def _is_retryable_model_error(exc: ClientError) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in (
        "model",
        "not found",
        "unsupported",
        "not supported",
        "invalid argument",
    ))


def test_connection() -> tuple[dict[str, str], int]:
    client = get_gemini_client()
    if client is None:
        return _build_error("Gemini service is not configured."), 503

    return {
        "status": "connected",
        "provider": PROVIDER_NAME,
    }, 200


def generate_chat_reply(
    message: str,
    user_id: int,
    history_context: list[dict[str, object]] | None = None,
    user_settings: dict[str, object] | None = None,
    attachments_context: list[dict[str, object]] | None = None,
) -> tuple[dict[str, str], int]:
    if not isinstance(message, str) or not message.strip():
        return _build_error("Message is required."), 400

    if not isinstance(user_id, int) or user_id <= 0:
        return _build_error("Authentication required."), 401

    client = get_gemini_client()
    if client is None:
        return _build_error("Gemini service is not configured."), 503

    financial_context = get_finance_context(user_id)
    prompt = _build_chat_prompt(
        message,
        financial_context,
        history_context or [],
        user_settings=user_settings,
        attachments_context=attachments_context,
    )
    logger.info("Generating JARVIS response for user_id=%s with %s recent transactions and %s goals.", user_id, len(financial_context.get("last_10_transactions", [])), len(financial_context.get("financial_goals", [])))

    last_client_error: ClientError | None = None
    for model_name in _get_model_candidates():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            reply = _extract_reply_text(response)
            if not reply:
                logger.error("Gemini returned an empty response for model %s.", model_name)
                return _build_error("Gemini returned an empty reply."), 502

            return {"reply": reply}, 200
        except ClientError as exc:
            last_client_error = exc
            if _is_retryable_model_error(exc):
                logger.warning("Gemini model %s unavailable, trying fallback. %s", model_name, exc)
                continue

            logger.exception("Gemini client error while generating content with model %s.", model_name)
            return _build_error("Gemini request was rejected."), 502
        except (APIError, ServerError):
            logger.exception("Gemini API error while generating content with model %s.", model_name)
            return _build_error("Gemini request failed."), 502
        except Exception:
            logger.exception("Unexpected Gemini error while generating content with model %s.", model_name)
            return _build_error("Gemini service encountered an unexpected error."), 500

    if last_client_error is not None:
        logger.exception("No compatible Gemini Flash model could be used.", exc_info=last_client_error)
        return _build_error("No compatible Gemini Flash model is available."), 503

    logger.error("Gemini model selection produced no attemptable models.")
    return _build_error("Gemini service is not configured."), 503