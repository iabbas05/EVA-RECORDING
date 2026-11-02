"""Conservative EVA session and booking recipient matching helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

# Bahrain uses UTC+03:00 year-round. A fixed offset avoids depending on the
# optional IANA tzdata package, which is not bundled with Python on Windows.
BAHRAIN = timezone(timedelta(hours=3), "Asia/Bahrain")
CANCELLED = {"cancelled", "canceled", "void", "refunded"}


def group_sessions(bookings: list[dict], session_date: str) -> list[dict]:
    """Daily picker groups actual play slots, without merging different games/IDs."""
    date.fromisoformat(session_date)
    groups = {}
    for booking in bookings:
        if (booking.get('session_date') != session_date or str(booking.get('branch', '')).casefold() != 'bahrain'
                or str(booking.get('arena_id')) != '81' or _is_cancelled(booking.get('status'))):
            continue
        booking_window(booking)
        key = (booking['start_time'], booking['end_time'], str(booking.get('game') or '').strip(), str(booking.get('official_session_id') or ''))
        if key not in groups:
            groups[key] = dict(session_date=session_date, start_time=key[0], end_time=key[1], game=key[2],
                               official_session_id=key[3] or None, branch='Bahrain', arena_id='81', booking_count=0)
        groups[key]['booking_count'] += 1
    return [groups[key] for key in sorted(groups)]


def _is_cancelled(status: object) -> bool:
    value = str(status or "").strip().casefold()
    return value in CANCELLED or any(term in value for term in ("cancel", "void", "refund"))


def _dt(day: str, clock: str) -> datetime:
    """Parse a Bahrain-local date/time. If end rolls past midnight, callers add a day."""
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(clock), BAHRAIN)


def booking_window(booking: dict) -> tuple[datetime, datetime]:
    start = _dt(booking["session_date"], booking["start_time"])
    end = _dt(booking["session_date"], booking["end_time"])
    if end <= start:
        end += timedelta(days=1)
    return start, end


def recording_window(recording: dict) -> tuple[datetime, datetime]:
    start = _dt(recording["session_date"], recording["start_time"])
    end = _dt(recording["session_date"], recording["end_time"])
    if end <= start:
        end += timedelta(days=1)
    return start, end


def match_bookings(recording: dict, bookings: list[dict], tolerance_seconds: int = 120) -> dict:
    """Return exact eligible bookings or an explicit ambiguity/miss result.

    Order IDs are deliberately ignored. Cancelled records are excluded. An official
    session ID wins only when area and time also agree, guarding against stale IDs.
    Without an official ID, both interval endpoints must agree within the tolerance.
    """
    try:
        r_start, r_end = recording_window(recording)
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid_recording_time", "bookings": []}

    r_area = str(recording.get("arena_id", ""))
    r_session_id = str(recording.get("official_session_id") or "").strip()
    r_game = str(recording.get('game') or '').strip().casefold()
    candidates = []
    for booking in bookings:
        if str(booking.get("arena_id", "")) != r_area:
            continue
        if str(booking.get("branch", "Bahrain")).casefold() != "bahrain":
            continue
        if _is_cancelled(booking.get("status")):
            continue
        try:
            b_start, b_end = booking_window(booking)
        except (KeyError, TypeError, ValueError):
            continue

        session_id = str(booking.get("official_session_id") or "").strip()
        game = str(booking.get('game') or '').strip().casefold()
        if r_game and game and game != r_game:
            continue
        # Prefer an exact official ID where both systems provide one. If the
        # booking has no session ID, use the guarded area/time match instead.
        if r_session_id and session_id and session_id != r_session_id:
            continue
        if abs((b_start - r_start).total_seconds()) <= tolerance_seconds and abs((b_end - r_end).total_seconds()) <= tolerance_seconds:
            candidates.append((booking, b_start, b_end, session_id))

    if not candidates:
        return {"status": "no_match", "bookings": []}
    if len(candidates) > 1:
        session_ids = {item[3] for item in candidates if item[3]}
        games = {str(item[0].get('game') or '').strip().casefold() for item in candidates if item[0].get('game')}
        if len(session_ids) > 1 or len(games) > 1:
            return {'status': 'ambiguous', 'bookings': [item[0] for item in candidates]}
        exact_session = bool(r_session_id) and all(item[3] == r_session_id for item in candidates)
        same_slot = all(item[1] == candidates[0][1] and item[2] == candidates[0][2] for item in candidates)
        if not exact_session and not same_slot:
            return {"status": "ambiguous", "bookings": [item[0] for item in candidates]}
    return {"status": "matched", "bookings": [item[0] for item in candidates]}


def recipients_for_bookings(bookings: list[dict], users: dict[str, dict]) -> dict:
    """Resolve only user IDs referenced by matched bookings/attendee lists."""
    by_email: dict[str, dict] = {}
    missing: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    for booking in bookings:
        booking_id = str(booking.get("booking_id", ""))
        attendee_ids = [str(x) for x in booking.get("attendee_user_ids", [])]
        owner_id = str(booking.get("owner_user_id") or "")
        links = [(uid, "Player") for uid in attendee_ids]
        if owner_id and owner_id not in attendee_ids:
            links.append((owner_id, "Booking owner"))

        for user_id, role in links:
            pair = (booking_id, user_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            user = users.get(user_id)
            if not user:
                missing.append({"booking_id": booking_id, "user_id": user_id, "reason": "linked account data missing", "role": role})
                continue
            email = str(user.get("email") or "").strip()
            if not email or "@" not in email:
                missing.append({"booking_id": booking_id, "user_id": user_id, "name": user.get("name", ""), "reason": "email missing", "role": role})
                continue
            key = email.casefold()
            if key not in by_email:
                by_email[key] = {"email": email, "name": user.get("name", ""), "user_id": user_id, "roles": [], "booking_ids": []}
            recipient = by_email[key]
            if role not in recipient["roles"]:
                recipient["roles"].append(role)
            if booking_id not in recipient["booking_ids"]:
                recipient["booking_ids"].append(booking_id)

    return {"recipients": list(by_email.values()), "missing": missing}


def delivery_key(video_sha256: str, email: str) -> tuple[str, str]:
    return video_sha256.lower().strip(), email.strip().casefold()
