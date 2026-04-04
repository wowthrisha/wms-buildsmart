from datetime import datetime, timezone


def utc_now():
    """Return current UTC time as a naive datetime for DB compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_utc(dt):
    """Normalize stored datetimes to aware UTC for safe comparisons/rendering."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
