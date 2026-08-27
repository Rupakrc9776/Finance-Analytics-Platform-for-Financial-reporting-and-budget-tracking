from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_ist(value, output_format="%Y-%m-%d %H:%M:%S"):
    if not value:
        return value
    if isinstance(value, datetime):
        parsed = value
    else:
        raw_value = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return value

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IST).strftime(output_format)
