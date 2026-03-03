from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta


def get_current_utc_timestamp() -> datetime:
    return datetime.now(timezone.utc)


def convert_ms_to_readable_format(execution_time_ms: float) -> str:
    milliseconds = execution_time_ms % 1000
    seconds = int((execution_time_ms // 1000) % 60)
    minutes = int((execution_time_ms // (1000 * 60)) % 60)
    hours = int((execution_time_ms // (1000 * 60 * 60)) % 24)

    time_str = ""
    if hours > 0:
        time_str += f"{hours}h "
    if minutes > 0:
        time_str += f"{minutes}m "
    if seconds > 0:
        time_str += f"{seconds}s "
    if milliseconds > 0 or time_str == "":
        time_str += f"{milliseconds:.0f}ms"

    return time_str.strip()


def get_execution_time_in_readable_format(start_time: datetime) -> str:
    return convert_ms_to_readable_format(
        get_execution_time_in_seconds(start_time) * 1000
    )


def get_execution_time_in_seconds(start_time: datetime) -> float:
    if start_time.tzinfo is timezone.utc:
        return round((get_current_utc_timestamp() - start_time).total_seconds(), 2)
    else:
        return round((datetime.now() - start_time).total_seconds(), 2)


def get_next_run_date(
    date: datetime, interval: int, is_custom: bool = False
) -> datetime:
    if not is_custom:
        if interval == 7:
            return date + timedelta(weeks=1)
        if interval == 15:
            return date + timedelta(weeks=2)
        if interval == 30:
            return date + relativedelta(months=1)
        if interval == 60:
            return date + relativedelta(months=2)
        if interval == 90:
            return date + relativedelta(months=3)
        if interval == 180:
            return date + relativedelta(months=6)
        if interval == 365:
            return date + relativedelta(years=1)
    else:
        return date + timedelta(days=interval)


def get_previous_schedule_date(
    date: datetime, interval: int, is_custom: bool = False
) -> datetime:
    if not is_custom:
        if interval == 7:
            return date - timedelta(weeks=1)
        if interval == 15:
            return date - timedelta(weeks=2)
        if interval == 30:
            return date - relativedelta(months=1)
        if interval == 60:
            return date - relativedelta(months=2)
        if interval == 90:
            return date - relativedelta(months=3)
        if interval == 180:
            return date - relativedelta(months=6)
        if interval == 365:
            return date - relativedelta(years=1)
    else:
        return date - timedelta(days=interval)


def to_tz(dt: datetime, timezone_str: str) -> datetime:
    tz = ZoneInfo(timezone_str)
    # If dt is naive, assume it's UTC (common API convention)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)
