from datetime import datetime, timedelta, timezone

from mythos_sdk import MythosSession

SESSION_CACHE_TTL = timedelta(minutes=30)

_sessions: dict[str, tuple[MythosSession, datetime]] = {}


def remember_mythos_session(launch_token: str, session: MythosSession) -> None:
    _sessions[launch_token] = (session, datetime.now(timezone.utc) + SESSION_CACHE_TTL)


def get_mythos_session(launch_token: str) -> MythosSession | None:
    cached = _sessions.get(launch_token)
    if cached is None:
        return None
    session, expires_at = cached
    if expires_at <= datetime.now(timezone.utc):
        _sessions.pop(launch_token, None)
        return None
    return session
