from __future__ import annotations

from datetime import datetime

from services.finance_context import get_finance_context


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_currency(value: object) -> str:
    return f"Rs. {_safe_float(value):,.2f}"


def _day_key() -> int:
    return datetime.now().timetuple().tm_yday


def _month_name() -> str:
    return datetime.now().strftime("%B")


def generate_login_coach_briefing(user_id: int) -> dict[str, object]:
    context = get_finance_context(user_id)

    overview = context.get("financial_overview", {}) or {}
    dashboard = context.get("dashboard_analytics", {}) or {}
    goals = context.get("financial_goals", []) or []
    goal_summary = context.get("goal_summary", {}) or {}
    investment_summary = context.get("investment_summary", {}) or {}
    actionable = context.get("actionable_intelligence", {}) or {}

    spent_this_month = _safe_float(actionable.get("spent_this_month", 0))
    total_income = _safe_float(overview.get("total_income", 0))
    budget_remaining = _safe_float(actionable.get("budget_remaining", 0))
    budget_utilization = _safe_float(overview.get("budget_utilization_percent", 0))
    highest_category = str(actionable.get("highest_spending_category", "No Data"))
    highest_category_amount = _safe_float(actionable.get("highest_spending_category_amount", 0))
    monthly_savings = _safe_float(actionable.get("savings_per_month", 0))
    predicted_expense = _safe_float(actionable.get("predicted_next_month_expense", 0))
    emergency_gap = _safe_float(actionable.get("emergency_fund_gap", 0))
    emergency_required = _safe_float(actionable.get("emergency_fund_required", 0))
    emergency_current = _safe_float(actionable.get("emergency_fund_current", 0))
    suggested_investment_topup = _safe_float(actionable.get("suggested_investment_topup", 0))
    roi = _safe_float(investment_summary.get("roi", 0))

    today_tip = "Review your top expense category and trim 5% today."
    if highest_category != "No Data" and highest_category_amount > 0:
        today_tip = (
            f"Your top category is {highest_category} at {_fmt_currency(highest_category_amount)}. "
            f"Reducing it by 5% saves about {_fmt_currency(highest_category_amount * 0.05)}."
        )

    challenge_amount = max(_safe_float(total_income * 0.02), 100.0)
    weekly_challenge = (
        f"Save {_fmt_currency(challenge_amount)} this week by keeping non-essential spending below "
        f"{_fmt_currency(max(challenge_amount * 1.5, 150.0))}."
    )

    if roi < 0:
        investment_suggestion = "Your ROI is negative. Pause fresh high-risk buys and rebalance weak assets."
    elif suggested_investment_topup > 0:
        investment_suggestion = (
            f"You can consider investing up to {_fmt_currency(suggested_investment_topup)} this month from savings "
            f"while maintaining cash discipline."
        )
    else:
        investment_suggestion = "Build stable monthly savings first, then start a recurring investment contribution."

    monthly_reminder = (
        f"Plan { _month_name() } with predicted expense {_fmt_currency(predicted_expense)} and keep a review checkpoint every Sunday."
    )

    budget_warning = "Budget is under control."
    if budget_utilization >= 95:
        budget_warning = (
            f"Warning: budget utilization is {budget_utilization:.2f}%. Remaining budget is {_fmt_currency(budget_remaining)}."
        )
    elif budget_utilization >= 80:
        budget_warning = (
            f"Caution: budget utilization is {budget_utilization:.2f}%. Keep remaining spending within {_fmt_currency(budget_remaining)}."
        )

    next_goal = None
    for item in goals:
        progress = _safe_float(item.get("progress_percent", 0))
        if progress < 100:
            next_goal = item
            break

    if next_goal:
        goal_reminder = (
            f"Goal reminder: {next_goal.get('goal_name', 'Goal')} is at { _safe_float(next_goal.get('progress_percent', 0)):.2f}% "
            f"({_fmt_currency(next_goal.get('saved_amount', 0))} of {_fmt_currency(next_goal.get('target_amount', 0))})."
        )
    else:
        goal_reminder = (
            f"Great work. {int(goal_summary.get('completed_goals', 0) or 0)} goals completed. "
            "Set a new goal this month to keep momentum."
        )

    if emergency_required > 0 and emergency_gap > 0:
        quote = (
            f"'Small daily discipline builds big security.' Emergency fund gap is {_fmt_currency(emergency_gap)} "
            f"toward a target of {_fmt_currency(emergency_required)}."
        )
    else:
        quote = "'Consistency beats intensity in personal finance.' Keep your savings habit active."

    coach_text = (
        "## Finance Coach Briefing\n"
        f"Date: {datetime.now().strftime('%d %b %Y')}\n\n"
        "### Today's Financial Tip\n"
        f"- {today_tip}\n\n"
        "### Weekly Saving Challenge\n"
        f"- {weekly_challenge}\n\n"
        "### Investment Suggestion\n"
        f"- {investment_suggestion}\n\n"
        "### Monthly Reminder\n"
        f"- {monthly_reminder}\n\n"
        "### Budget Warning\n"
        f"- {budget_warning}\n\n"
        "### Goal Reminder\n"
        f"- {goal_reminder}\n\n"
        "### Motivational Quote\n"
        f"- {quote}"
    )

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "day_key": _day_key(),
        "briefing": {
            "today_financial_tip": today_tip,
            "weekly_saving_challenge": weekly_challenge,
            "investment_suggestion": investment_suggestion,
            "monthly_reminder": monthly_reminder,
            "budget_warning": budget_warning,
            "goal_reminder": goal_reminder,
            "motivational_quote": quote,
        },
        "message_markdown": coach_text,
        "source": {
            "income": total_income,
            "spent_this_month": spent_this_month,
            "predicted_next_month_expense": predicted_expense,
            "budget_utilization": budget_utilization,
            "budget_remaining": budget_remaining,
            "highest_category": highest_category,
            "highest_category_amount": highest_category_amount,
            "monthly_savings": monthly_savings,
            "investment_roi": roi,
            "emergency_fund_required": emergency_required,
            "emergency_fund_current": emergency_current,
            "emergency_fund_gap": emergency_gap,
        },
    }
