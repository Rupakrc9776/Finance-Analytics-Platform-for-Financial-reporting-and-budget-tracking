from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DATABASE_PATH = Path(__file__).resolve().parent.parent / "finance.db"


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


def _clamp_value(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _month_key(date_obj: datetime) -> str:
    return f"{date_obj.year:04d}-{date_obj.month:02d}"


def _month_label(date_obj: datetime) -> str:
    return date_obj.strftime("%b %Y")


def _shift_to_previous_month(date_obj: datetime) -> datetime:
    first_day = date_obj.replace(day=1)
    return first_day - timedelta(days=1)


def _get_financial_snapshot(cur: sqlite3.Cursor, user_id: int) -> dict[str, float]:
    cur.execute(
        """
        SELECT IFNULL(income, 0) AS income
        FROM users
        WHERE id=?
        """,
        (user_id,),
    )
    user_row = cur.fetchone()
    income = _safe_float(user_row["income"] if user_row else 0)

    cur.execute(
        """
        SELECT IFNULL(SUM(amount), 0) AS total_expense
        FROM expenses
        WHERE user_id=?
        """,
        (user_id,),
    )
    total_expense = _safe_float(cur.fetchone()["total_expense"])

    return {
        "total_income": round(income, 2),
        "total_expense": round(total_expense, 2),
        "total_savings": round(income - total_expense, 2),
    }


def _get_current_budget(cur: sqlite3.Cursor, user_id: int) -> float:
    cur.execute(
        """
        SELECT monthly_budget
        FROM budget
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return round(_safe_float(row["monthly_budget"] if row else 0), 2)


def _get_spending_analysis(cur: sqlite3.Cursor, user_id: int) -> dict[str, object]:
    current_month_date = datetime.now().replace(day=1)
    previous_month_date = _shift_to_previous_month(current_month_date)
    current_month = _month_key(current_month_date)
    previous_month = _month_key(previous_month_date)

    cur.execute(
        """
        SELECT IFNULL(SUM(amount), 0) AS monthly_expense
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7)=?
        """,
        (user_id, current_month),
    )
    total_monthly_expense = _safe_float(cur.fetchone()["monthly_expense"])

    cur.execute(
        """
        SELECT IFNULL(SUM(amount), 0) AS monthly_expense
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7)=?
        """,
        (user_id, previous_month),
    )
    previous_month_expense = _safe_float(cur.fetchone()["monthly_expense"])

    requested_month_keys: list[str] = []
    month_cursor = current_month_date
    for _ in range(6):
        requested_month_keys.append(_month_key(month_cursor))
        month_cursor = _shift_to_previous_month(month_cursor)
    requested_month_keys.reverse()

    month_totals: dict[str, float] = {}
    if requested_month_keys:
        placeholders = ",".join("?" for _ in requested_month_keys)
        cur.execute(
            f"""
            SELECT substr(expense_date, 1, 7) AS month_key, IFNULL(SUM(amount), 0) AS total
            FROM expenses
            WHERE user_id=? AND substr(expense_date, 1, 7) IN ({placeholders})
            GROUP BY month_key
            ORDER BY month_key
            """,
            [user_id, *requested_month_keys],
        )
        month_totals = {row["month_key"]: _safe_float(row["total"]) for row in cur.fetchall()}

    monthly_trends = []
    for month_value in requested_month_keys:
        year_part, month_part = month_value.split("-")
        date_obj = datetime(int(year_part), int(month_part), 1)
        monthly_trends.append({
            "month": _month_label(date_obj),
            "expense": round(month_totals.get(month_value, 0.0), 2),
        })

    cur.execute(
        """
        SELECT category, IFNULL(SUM(amount), 0) AS total
        FROM expenses
        WHERE user_id=? AND substr(expense_date, 1, 7)=?
        GROUP BY category
        ORDER BY total DESC
        """,
        (user_id, current_month),
    )
    category_rows = cur.fetchall()

    if not category_rows:
        cur.execute(
            """
            SELECT category, IFNULL(SUM(amount), 0) AS total
            FROM expenses
            WHERE user_id=?
            GROUP BY category
            ORDER BY total DESC
            """,
            (user_id,),
        )
        category_rows = cur.fetchall()

    category_breakdown = [
        {
            "category": row["category"],
            "amount": round(_safe_float(row["total"]), 2),
        }
        for row in category_rows
    ]

    cur.execute(
        """
        SELECT COUNT(*) AS txn_count
        FROM expenses
        WHERE user_id=?
        """,
        (user_id,),
    )
    transaction_count = int(cur.fetchone()["txn_count"] or 0)

    monthly_change_amount = total_monthly_expense - previous_month_expense
    monthly_change_percent = round((monthly_change_amount / previous_month_expense) * 100, 2) if previous_month_expense > 0 else 0.0

    highest_category = category_breakdown[0] if category_breakdown else {"category": "No Data", "amount": 0.0}

    return {
        "current_month_expense": round(total_monthly_expense, 2),
        "previous_month_expense": round(previous_month_expense, 2),
        "monthly_change_amount": round(monthly_change_amount, 2),
        "monthly_change_percent": round(monthly_change_percent, 2),
        "transaction_count": transaction_count,
        "expense_category_summary": category_breakdown,
        "highest_category": highest_category,
        "monthly_trends": monthly_trends,
    }


def _get_recent_transactions(cur: sqlite3.Cursor, user_id: int) -> list[dict[str, object]]:
    cur.execute(
        """
        SELECT category, amount, IFNULL(description, '') AS description, expense_date
        FROM expenses
        WHERE user_id=?
        ORDER BY expense_date DESC, id DESC
        LIMIT 10
        """,
        (user_id,),
    )
    return [
        {
            "category": row["category"],
            "amount": round(_safe_float(row["amount"]), 2),
            "description": row["description"],
            "date": row["expense_date"],
        }
        for row in cur.fetchall()
    ]


def _get_investments(cur: sqlite3.Cursor, user_id: int) -> tuple[list[dict[str, object]], dict[str, float]]:
    cur.execute(
        """
        SELECT asset, investment_name, invested_amount, current_value, purchase_date
        FROM investments
        WHERE user_id=?
        ORDER BY COALESCE(purchase_date, created_at) DESC, id DESC
        """,
        (user_id,),
    )
    investments = [
        {
            "asset": row["asset"],
            "name": row["investment_name"],
            "invested_amount": round(_safe_float(row["invested_amount"]), 2),
            "current_value": round(_safe_float(row["current_value"]), 2),
            "purchase_date": row["purchase_date"],
        }
        for row in cur.fetchall()
    ]

    total_investment = round(sum(item["invested_amount"] for item in investments), 2)
    current_value = round(sum(item["current_value"] for item in investments), 2)
    total_profit = round(current_value - total_investment, 2)
    roi = round((total_profit / total_investment) * 100, 2) if total_investment > 0 else 0.0

    return investments, {
        "total_investment": total_investment,
        "current_value": current_value,
        "total_profit": total_profit,
        "roi": roi,
    }


def _get_financial_goals(cur: sqlite3.Cursor, user_id: int) -> tuple[list[dict[str, object]], dict[str, float]]:
    cur.execute(
        """
        SELECT goal_name, target_amount, saved_amount, target_date
        FROM goals
        WHERE user_id=?
        ORDER BY target_date ASC, id DESC
        """,
        (user_id,),
    )
    goals = []
    total_target = 0.0
    total_saved = 0.0
    completed_goals = 0

    for row in cur.fetchall():
        target_amount = round(_safe_float(row["target_amount"]), 2)
        saved_amount = round(_safe_float(row["saved_amount"]), 2)
        progress = round((saved_amount / target_amount) * 100, 2) if target_amount > 0 else 0.0
        is_completed = saved_amount >= target_amount if target_amount > 0 else False
        if is_completed:
            completed_goals += 1
        total_target += target_amount
        total_saved += saved_amount
        goals.append({
            "goal_name": row["goal_name"],
            "target_amount": target_amount,
            "saved_amount": saved_amount,
            "target_date": row["target_date"],
            "progress_percent": progress,
            "status": "Completed" if is_completed else "In Progress",
        })

    total_goals = len(goals)
    active_goals = total_goals - completed_goals
    overall_progress = round((total_saved / total_target) * 100, 2) if total_target > 0 else 0.0

    return goals, {
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "active_goals": active_goals,
        "overall_progress": overall_progress,
    }


def _get_budget_context(current_budget: float, income: float, spending_analysis: dict[str, object], goal_summary: dict[str, float], investment_summary: dict[str, float], total_savings: float) -> dict[str, object]:
    working_budget = current_budget
    if working_budget <= 0:
        current_month_expense = _safe_float(spending_analysis["current_month_expense"])
        working_budget = income * 0.7 if income > 0 else current_month_expense

    current_month_expense = _safe_float(spending_analysis["current_month_expense"])
    budget_utilization = round((current_month_expense / working_budget) * 100, 2) if working_budget > 0 else 0.0
    remaining_budget = round(max(working_budget - current_month_expense, 0.0), 2)

    overspending_categories = []
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
    reference_budget = income if income > 0 else working_budget
    for category_data in spending_analysis["expense_category_summary"]:
        category_name = str(category_data["category"])
        spent_amount = _safe_float(category_data["amount"])
        category_limit = category_limits.get(category_name, 0.10)
        threshold = reference_budget * category_limit if reference_budget > 0 else 0.0
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

    expected_savings_target = income * 0.2 if income > 0 else 0.0
    suggested_budget = income - expected_savings_target if income > 0 else max(current_month_expense * 0.95, current_month_expense - 1000)
    suggested_budget = max(suggested_budget, 0.0)

    recommendations: list[str] = []
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
    monthly_savings = income - current_month_expense
    if income > 0 and monthly_savings < income * 0.2:
        recommendations.append("Increase monthly savings by reducing discretionary categories until you reach at least a 20% savings rate.")
    emergency_fund_target = current_month_expense * 3
    emergency_fund_current = max(total_savings, 0.0)
    if emergency_fund_target > 0 and emergency_fund_current < emergency_fund_target:
        recommendations.append(
            f"Emergency fund should be increased by roughly Rs. {emergency_fund_target - emergency_fund_current:,.0f} to cover at least 3 months of expenses."
        )
    if investment_summary["total_investment"] <= 0 and income > 0:
        recommendations.append("Start a recurring monthly investment to improve long-term financial growth.")
    elif investment_summary["roi"] < 0:
        recommendations.append("Investment portfolio is underperforming; review loss-making assets and rebalance risk exposure.")
    elif investment_summary["roi"] >= 10:
        recommendations.append("Investment portfolio is performing well; maintain diversification and continue disciplined contributions.")
    if goal_summary["active_goals"] > 0 and goal_summary["overall_progress"] < 60:
        recommendations.append("Goal progress is below target pace; allocate a fixed savings transfer toward active goals each month.")

    return {
        "current_budget": round(working_budget, 2),
        "remaining_budget": remaining_budget,
        "budget_utilization_percent": budget_utilization,
        "budget_health_status": budget_health_status,
        "suggested_budget": round(suggested_budget, 2),
        "overspending_categories": overspending_categories,
        "recommendations": recommendations,
    }


def _get_health_score(income: float, spending_analysis: dict[str, object], investment_summary: dict[str, float], budget_context: dict[str, object], goal_summary: dict[str, float]) -> dict[str, object]:
    current_month_expense = _safe_float(spending_analysis["current_month_expense"])
    monthly_savings = income - current_month_expense
    savings_ratio = (monthly_savings / income) * 100 if income > 0 else 0.0
    expense_ratio = (current_month_expense / income) * 100 if income > 0 else 0.0
    investment_ratio = (investment_summary["total_investment"] / income) * 100 if income > 0 else 0.0
    budget_utilization = _safe_float(budget_context["budget_utilization_percent"])
    transaction_count = int(spending_analysis["transaction_count"])
    goal_progress = _safe_float(goal_summary["overall_progress"])
    investment_roi = _safe_float(investment_summary["roi"])

    savings_score = _clamp_value(round(savings_ratio * 1.2), 0, 25)
    expense_score = _clamp_value(round((100 - expense_ratio) * 0.15), 0, 15)
    budget_score = _clamp_value(round((110 - budget_utilization) * 0.25), 0, 15)
    transaction_score = _clamp_value(round(min(transaction_count, 50) * 0.2), 0, 10)
    investment_score = _clamp_value(round((investment_roi + 10) * 0.6), 0, 15)
    goal_score = _clamp_value(round(goal_progress * 0.15), 0, 15)
    stability_score = 5 if monthly_savings >= 0 else 0

    score = int(_clamp_value(
        savings_score + expense_score + budget_score + transaction_score + investment_score + goal_score + stability_score,
        0,
        100,
    ))

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
        "monthly_savings": round(monthly_savings, 2),
        "savings_ratio": round(savings_ratio, 2),
        "expense_ratio": round(expense_ratio, 2),
        "investment_ratio": round(investment_ratio, 2),
        "budget_utilization": round(budget_utilization, 2),
        "transaction_count": transaction_count,
        "investment_roi": round(investment_roi, 2),
        "goal_progress": round(goal_progress, 2),
        "explanation": explanation,
        "improvement_tips": improvement_tips,
    }


def _get_actionable_intelligence(financial_snapshot: dict[str, float], spending_analysis: dict[str, object], budget_context: dict[str, object], investment_summary: dict[str, float], goal_summary: dict[str, float], health_score: dict[str, object]) -> dict[str, object]:
    total_expense = _safe_float(financial_snapshot.get("total_expense", 0))
    current_month_expense = _safe_float(spending_analysis.get("current_month_expense", 0))
    previous_month_expense = _safe_float(spending_analysis.get("previous_month_expense", 0))
    total_income = _safe_float(financial_snapshot.get("total_income", 0))
    total_savings = _safe_float(financial_snapshot.get("total_savings", 0))
    remaining_budget = _safe_float(budget_context.get("remaining_budget", 0))
    monthly_savings = _safe_float(health_score.get("monthly_savings", 0))

    trend_values = [
        _safe_float(item.get("expense", 0))
        for item in (spending_analysis.get("monthly_trends", []) or [])
        if _safe_float(item.get("expense", 0)) > 0
    ]
    recent_3 = trend_values[-3:] if len(trend_values) >= 3 else trend_values
    predicted_next_month_expense = round((sum(recent_3) / len(recent_3)), 2) if recent_3 else round(current_month_expense, 2)

    highest_category = spending_analysis.get("highest_category", {}) or {}
    highest_category_name = str(highest_category.get("category", "No Data"))
    highest_category_amount = round(_safe_float(highest_category.get("amount", 0)), 2)

    emergency_fund_required = round(current_month_expense * 6, 2)
    emergency_fund_current = round(max(total_savings, 0.0), 2)
    emergency_fund_gap = round(max(emergency_fund_required - emergency_fund_current, 0.0), 2)

    discretionary_purchase_capacity = round(max(remaining_budget, 0.0) + max(monthly_savings, 0.0), 2)
    safe_purchase_limit = round(max(discretionary_purchase_capacity - emergency_fund_gap * 0.1, 0.0), 2)

    suggested_monthly_savings = round(max(total_income * 0.2, monthly_savings, 0.0), 2)
    additional_savings_needed = round(max(suggested_monthly_savings - max(monthly_savings, 0.0), 0.0), 2)

    suggested_investment_topup = 0.0
    if monthly_savings > 0:
        suggested_investment_topup = round(monthly_savings * 0.3, 2)

    return {
        "total_spent_all_time": round(total_expense, 2),
        "spent_this_month": round(current_month_expense, 2),
        "spent_previous_month": round(previous_month_expense, 2),
        "highest_spending_category": highest_category_name,
        "highest_spending_category_amount": highest_category_amount,
        "predicted_next_month_expense": predicted_next_month_expense,
        "savings_per_month": round(monthly_savings, 2),
        "suggested_monthly_savings": suggested_monthly_savings,
        "additional_savings_needed": additional_savings_needed,
        "investment_roi": round(_safe_float(investment_summary.get("roi", 0)), 2),
        "suggested_investment_topup": suggested_investment_topup,
        "goal_progress_percent": round(_safe_float(goal_summary.get("overall_progress", 0)), 2),
        "budget_remaining": round(remaining_budget, 2),
        "discretionary_purchase_capacity": discretionary_purchase_capacity,
        "safe_purchase_limit": safe_purchase_limit,
        "emergency_fund_required": emergency_fund_required,
        "emergency_fund_current": emergency_fund_current,
        "emergency_fund_gap": emergency_fund_gap,
    }


def get_finance_context(user_id: int) -> dict[str, object]:
    context = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "financial_overview": {
            "total_income": 0.0,
            "total_expense": 0.0,
            "total_savings": 0.0,
            "current_budget": 0.0,
            "remaining_budget": 0.0,
            "budget_utilization_percent": 0.0,
        },
        "last_10_transactions": [],
        "expense_category_summary": [],
        "investments": [],
        "investment_summary": {
            "total_investment": 0.0,
            "current_value": 0.0,
            "total_profit": 0.0,
            "roi": 0.0,
        },
        "financial_goals": [],
        "goal_summary": {
            "total_goals": 0,
            "completed_goals": 0,
            "active_goals": 0,
            "overall_progress": 0.0,
        },
        "monthly_trends": [],
        "ai_health_score": {
            "score": 0,
            "status": "Needs Improvement",
            "monthly_savings": 0.0,
            "savings_ratio": 0.0,
            "expense_ratio": 0.0,
            "investment_ratio": 0.0,
            "budget_utilization": 0.0,
            "transaction_count": 0,
            "investment_roi": 0.0,
            "goal_progress": 0.0,
            "explanation": [],
            "improvement_tips": [],
        },
        "dashboard_analytics": {
            "transaction_count": 0,
            "highest_spending_category": "No Data",
            "highest_spending_amount": 0.0,
            "monthly_change_percent": 0.0,
            "monthly_change_amount": 0.0,
            "budget_health_status": "Unknown",
            "investment_roi": 0.0,
            "goal_progress": 0.0,
        },
        "budget_recommendations": [],
        "actionable_intelligence": {
            "total_spent_all_time": 0.0,
            "spent_this_month": 0.0,
            "spent_previous_month": 0.0,
            "highest_spending_category": "No Data",
            "highest_spending_category_amount": 0.0,
            "predicted_next_month_expense": 0.0,
            "savings_per_month": 0.0,
            "suggested_monthly_savings": 0.0,
            "additional_savings_needed": 0.0,
            "investment_roi": 0.0,
            "suggested_investment_topup": 0.0,
            "goal_progress_percent": 0.0,
            "budget_remaining": 0.0,
            "discretionary_purchase_capacity": 0.0,
            "safe_purchase_limit": 0.0,
            "emergency_fund_required": 0.0,
            "emergency_fund_current": 0.0,
            "emergency_fund_gap": 0.0,
        },
    }

    try:
        connection = _get_db_connection()
        try:
            cur = connection.cursor()

            financial_snapshot = _get_financial_snapshot(cur, user_id)
            current_budget = _get_current_budget(cur, user_id)
            spending_analysis = _get_spending_analysis(cur, user_id)
            recent_transactions = _get_recent_transactions(cur, user_id)
            investments, investment_summary = _get_investments(cur, user_id)
            goals, goal_summary = _get_financial_goals(cur, user_id)
            budget_context = _get_budget_context(
                current_budget,
                financial_snapshot["total_income"],
                spending_analysis,
                goal_summary,
                investment_summary,
                financial_snapshot["total_savings"],
            )
            health_score = _get_health_score(
                financial_snapshot["total_income"],
                spending_analysis,
                investment_summary,
                budget_context,
                goal_summary,
            )

            context["financial_overview"] = {
                "total_income": financial_snapshot["total_income"],
                "total_expense": financial_snapshot["total_expense"],
                "total_savings": financial_snapshot["total_savings"],
                "current_budget": budget_context["current_budget"],
                "remaining_budget": budget_context["remaining_budget"],
                "budget_utilization_percent": budget_context["budget_utilization_percent"],
            }
            context["last_10_transactions"] = recent_transactions
            context["expense_category_summary"] = spending_analysis["expense_category_summary"]
            context["investments"] = investments
            context["investment_summary"] = investment_summary
            context["financial_goals"] = goals
            context["goal_summary"] = goal_summary
            context["monthly_trends"] = spending_analysis["monthly_trends"]
            context["ai_health_score"] = health_score
            context["dashboard_analytics"] = {
                "transaction_count": int(spending_analysis.get("transaction_count", 0) or 0),
                "highest_spending_category": spending_analysis["highest_category"].get("category", "No Data"),
                "highest_spending_amount": round(_safe_float(spending_analysis["highest_category"].get("amount", 0)), 2),
                "monthly_change_percent": round(_safe_float(spending_analysis.get("monthly_change_percent", 0)), 2),
                "monthly_change_amount": round(_safe_float(spending_analysis.get("monthly_change_amount", 0)), 2),
                "budget_health_status": str(budget_context.get("budget_health_status", "Unknown")),
                "investment_roi": round(_safe_float(investment_summary.get("roi", 0)), 2),
                "goal_progress": round(_safe_float(goal_summary.get("overall_progress", 0)), 2),
            }
            context["budget_recommendations"] = budget_context["recommendations"]
            context["actionable_intelligence"] = _get_actionable_intelligence(
                financial_snapshot,
                spending_analysis,
                budget_context,
                investment_summary,
                goal_summary,
                health_score,
            )
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Failed to build finance context for user_id=%s.", user_id)
    except Exception:
        logger.exception("Unexpected error while building finance context for user_id=%s.", user_id)

    return context


def get_financial_context(user_id: int) -> dict[str, object]:
    return get_finance_context(user_id)