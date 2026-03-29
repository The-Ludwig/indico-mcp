"""
Indico MCP Server — Phase 1

Tools:
  search_category_events   — list events in a category, filtered by date/keyword
  get_event_details        — full event metadata + contributions
  get_event_contributions  — flat list of contributions for an event
  get_event_sessions       — sessions with nested contributions (full agenda)
  search_events_by_keyword — full-text search via Indico search API
  list_category_info       — metadata about a category

Configuration (see .env.example):
  INDICO_BASE_URL / INDICO_TOKEN            — single instance
  INDICO_INSTANCES / INDICO_DEFAULT / ...   — multi-instance
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from dotenv import load_dotenv
from fastmcp import Context, FastMCP
from pydantic import Field

from .client import IndicoClient, IndicoError
from .config import Config
from .models import (
    extract_results,
    normalize_contribution,
    normalize_event,
    normalize_session,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

_config: Config | None = None
_clients: dict[str, IndicoClient] = {}


@asynccontextmanager
async def lifespan(server: FastMCP):  # noqa: ARG001
    global _config, _clients
    _config = Config()
    _clients = {
        name: IndicoClient(_config.get(name)) for name in _config.instance_names
    }
    yield
    for c in _clients.values():
        await c.aclose()
    _clients.clear()


app = FastMCP(
    "indico",
    instructions=(
        "Tools for browsing Indico meeting agendas, searching events by category, "
        "and extracting contribution and session details. "
        "Use the `instance` parameter to select between configured Indico servers."
    ),
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(instance: str | None) -> IndicoClient:
    assert _config is not None, "Server not initialised"
    cfg = _config.get(instance)
    return _clients[cfg.name]


def _instance_field() -> Any:
    return Field(
        default=None,
        description=(
            "Named Indico instance to query (e.g. 'cern', 'su'). "
            "Defaults to the primary configured instance."
        ),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@app.tool()
async def search_category_events(
    category_id: Annotated[int, Field(description="Indico category ID. Use 0 for the root (all public events).")] = 0,
    from_date: Annotated[str | None, Field(description="Start date filter, YYYY-MM-DD.")] = None,
    to_date: Annotated[str | None, Field(description="End date filter, YYYY-MM-DD.")] = None,
    keyword: Annotated[str | None, Field(description="Filter event titles by this keyword (case-insensitive).")] = None,
    limit: Annotated[int, Field(description="Maximum number of events to return (default 50, max 1000).")] = 50,
    instance: Annotated[str | None, _instance_field()] = None,
    ctx: Context = None,
) -> list[dict]:
    """
    List events in an Indico category, optionally filtered by date range and keyword.

    Returns a list of events with id, title, type, start/end times, location, and URL.
    Use get_event_details or get_event_contributions to drill into a specific event.
    """
    client = _client(instance)
    params: dict[str, Any] = {
        "from": from_date,
        "to": to_date,
        "limit": min(limit, 1000),
        "order": "start",
    }
    try:
        data = await client.export(f"categ/{category_id}", **params)
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    events = [normalize_event(r) for r in results]

    if keyword:
        kw = keyword.lower()
        events = [e for e in events if kw in (e.get("title") or "").lower()]

    return events


@app.tool()
async def get_event_details(
    event_id: Annotated[int, Field(description="Indico event ID.")],
    instance: Annotated[str | None, _instance_field()] = None,
    ctx: Context = None,
) -> dict:
    """
    Get full metadata for an event, including its list of contributions.

    Returns event title, description, dates, location, and all contributions
    (with speakers, duration, abstract, session assignment).
    """
    client = _client(instance)
    try:
        data = await client.export(f"event/{event_id}", detail="contributions")
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    if not results:
        raise ValueError(f"Event {event_id} not found or not accessible.")

    return normalize_event(results[0], include_contributions=True)


@app.tool()
async def get_event_contributions(
    event_id: Annotated[int, Field(description="Indico event ID.")],
    instance: Annotated[str | None, _instance_field()] = None,
    ctx: Context = None,
) -> list[dict]:
    """
    List all contributions for an event.

    Each contribution includes: title, speakers, authors, start time, duration,
    session, track, abstract/description, and room.
    """
    client = _client(instance)
    try:
        data = await client.export(f"event/{event_id}", detail="contributions")
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    if not results:
        raise ValueError(f"Event {event_id} not found or not accessible.")

    raw_contribs = results[0].get("contributions", [])
    return [normalize_contribution(c) for c in raw_contribs]


@app.tool()
async def get_event_sessions(
    event_id: Annotated[int, Field(description="Indico event ID.")],
    instance: Annotated[str | None, _instance_field()] = None,
    ctx: Context = None,
) -> list[dict]:
    """
    Get the session structure for an event, with each session's contributions nested inside.

    Useful for understanding how an event agenda is organised into parallel tracks or blocks.
    Each session includes: title, conveners, start/end time, room, and contributions list.
    """
    client = _client(instance)
    try:
        data = await client.export(f"event/{event_id}", detail="sessions")
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    if not results:
        raise ValueError(f"Event {event_id} not found or not accessible.")

    raw_sessions = results[0].get("sessions", [])
    return [normalize_session(s) for s in raw_sessions]


@app.tool()
async def search_events_by_keyword(
    keyword: Annotated[str, Field(description="Search term.")],
    limit: Annotated[int, Field(description="Maximum results to return (default 20).")] = 20,
    instance: Annotated[str | None, _instance_field()] = None,
    ctx: Context = None,
) -> list[dict]:
    """
    Full-text search for events matching a keyword.

    Uses the Indico search API if available, otherwise falls back to the
    legacy title-search export endpoint. Returns matching events with id,
    title, category, dates, and URL.
    """
    client = _client(instance)

    # Try the modern search API first
    try:
        data = await client.api("search/", q=keyword, scope="events", limit=limit)
        # Modern search API returns {"results": {"events": {"results": [...], "total": n}}}
        events_block = (
            data.get("results", {}).get("events", {})
            if isinstance(data.get("results"), dict)
            else {}
        )
        raw_events = events_block.get("results", [])
        if raw_events:
            return [normalize_event(e) for e in raw_events[:limit]]
    except IndicoError:
        pass  # fall through to legacy

    # Legacy: search via the export title-search endpoint
    try:
        import urllib.parse
        encoded = urllib.parse.quote(keyword, safe="")
        data = await client.export(f"event/search/{encoded}", limit=limit)
        results = extract_results(data)
        return [normalize_event(r) for r in results[:limit]]
    except IndicoError as e:
        raise ValueError(str(e)) from e


@app.tool()
async def list_category_info(
    category_id: Annotated[int, Field(description="Indico category ID. Use 0 for the root.")] = 0,
    instance: Annotated[str | None, _instance_field()] = None,
    ctx: Context = None,
) -> dict:
    """
    Get metadata about an Indico category: name, description, and subcategory IDs.

    Useful for navigating the category hierarchy to find the right category_id
    before using search_category_events.
    """
    client = _client(instance)

    # Try the REST API first (returns richer category data)
    try:
        data = await client.api(f"categories/{category_id}/")
        return {
            "id": data.get("id"),
            "title": data.get("title"),
            "description": data.get("description") or None,
            "url": data.get("url"),
            "parent_id": data.get("parent_id"),
            "subcategory_ids": [c.get("id") for c in data.get("subcategories", [])],
        }
    except IndicoError:
        pass

    # Fallback: infer from a minimal export call
    try:
        data = await client.export(f"categ/{category_id}", limit=0)
    except IndicoError as e:
        raise ValueError(str(e)) from e

    return {
        "id": category_id,
        "count": data.get("count", 0),
        "note": (
            "Full category metadata not available on this instance. "
            "Use search_category_events to list events."
        ),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
