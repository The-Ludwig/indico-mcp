"""
Typed models and normalisation helpers for Indico API responses.

The Indico export API returns deeply nested, inconsistently-keyed JSON.
These helpers flatten it into clean dicts that are easy for agents to work with.
"""

from __future__ import annotations


def _date_str(d: dict | str | None) -> str | None:
    """Convert Indico {date, time, tz} dict or plain string to ISO-ish string."""
    if d is None:
        return None
    if isinstance(d, str):
        return d
    date = d.get("date", "")
    time = d.get("time", "")
    tz = d.get("tz", "")
    if date and time:
        return f"{date}T{time} ({tz})" if tz else f"{date}T{time}"
    return date or None


def _person_name(p: dict) -> str:
    """Extract a display name from a person dict."""
    if "fullName" in p:
        return p["fullName"]
    first = p.get("first_name") or p.get("firstName", "")
    last = p.get("last_name") or p.get("lastName", "")
    name = f"{first} {last}".strip()
    return name or p.get("name", "")


def normalize_event(raw: dict, include_contributions: bool = False) -> dict:
    """Flatten a raw event dict from the export API."""
    event: dict = {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "type": raw.get("type"),
        "url": raw.get("url"),
        "start": _date_str(raw.get("startDate")),
        "end": _date_str(raw.get("endDate")),
        "timezone": raw.get("timezone"),
        "location": raw.get("location"),
        "room": raw.get("roomFullname") or raw.get("room"),
        "category": raw.get("category"),
        "category_id": raw.get("categoryId"),
        "description": raw.get("description") or None,
    }
    if include_contributions and "contributions" in raw:
        event["contributions"] = [
            normalize_contribution(c) for c in raw["contributions"]
        ]
    return {k: v for k, v in event.items() if v is not None}


def normalize_contribution(raw: dict) -> dict:
    """Flatten a contribution from the export API."""
    speakers = [_person_name(p) for p in raw.get("speakers", [])]
    authors = [_person_name(p) for p in raw.get("primaryauthors", [])]

    contrib: dict = {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "start": _date_str(raw.get("startDate")),
        "duration": raw.get("duration"),
        "location": raw.get("location"),
        "room": raw.get("roomFullname") or raw.get("room"),
        "session_id": raw.get("session", {}).get("id") if isinstance(raw.get("session"), dict) else raw.get("session"),
        "session_title": raw.get("session", {}).get("title") if isinstance(raw.get("session"), dict) else None,
        "track": raw.get("track"),
        "speakers": speakers or None,
        "authors": authors or None,
        "abstract": raw.get("description") or None,
        "keywords": raw.get("keywords") or None,
    }
    return {k: v for k, v in contrib.items() if v is not None}


def normalize_session(raw: dict) -> dict:
    """Flatten a session from the export API."""
    conveners = [_person_name(p) for p in raw.get("conveners", [])]
    contributions = [normalize_contribution(c) for c in raw.get("contributions", [])]

    session: dict = {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "start": _date_str(raw.get("startDate")),
        "end": _date_str(raw.get("endDate")),
        "location": raw.get("location"),
        "room": raw.get("roomFullname") or raw.get("room"),
        "conveners": conveners or None,
        "contributions": contributions or None,
    }
    return {k: v for k, v in session.items() if v is not None}


def normalize_event_header(raw: dict) -> dict:
    """Minimal event context (id, title, start) used to annotate category-level contributions."""
    return {k: v for k, v in {
        "event_id": raw.get("id"),
        "event_title": raw.get("title"),
        "event_start": _date_str(raw.get("startDate")),
    }.items() if v is not None}


def normalize_room(raw: dict) -> dict:
    """Flatten a room dict from /export/roomName/."""
    room: dict = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "full_name": raw.get("fullName"),
        "building": raw.get("building"),
        "floor": raw.get("floor"),
        "location": raw.get("location"),
        "url": raw.get("url"),
    }
    return {k: v for k, v in room.items() if v is not None}


def normalize_reservation(raw: dict) -> dict:
    """Flatten a reservation dict from /export/reservation/."""
    room = raw.get("room", {})
    reservation: dict = {
        "id": raw.get("id"),
        "room_id": room.get("id") if isinstance(room, dict) else None,
        "room_name": room.get("fullName") if isinstance(room, dict) else None,
        "start": _date_str(raw.get("startDT")),
        "end": _date_str(raw.get("endDT")),
        "booked_for": raw.get("bookedForName"),
        "reason": raw.get("reason"),
        "is_confirmed": raw.get("isConfirmed"),
    }
    return {k: v for k, v in reservation.items() if v is not None}


def extract_results(response: dict) -> list[dict]:
    """Pull the results list out of the standard export API envelope."""
    # Some Indico instances use "result" (singular) instead of "results"
    results = response.get("results") or response.get("result", [])
    # Some endpoints wrap in another list
    if results and isinstance(results[0], list):
        results = results[0]
    return results
