"""
Indico MCP Server

Tools:
  search_categories        — find categories by name (returns ID + breadcrumb path)
  search_category_events   — list events in a category, filtered by date/keyword
  get_category_contributions — all contributions across events in a category (single call)
  get_event_details        — full event metadata + contributions
  get_event_contributions  — flat list of contributions for an event
  get_event_sessions       — sessions with nested contributions (full agenda)
  search_events_by_keyword — full-text search via Indico search API
  list_category_info       — metadata about a category, with subcategory names

Configuration (see .env.example):
  INDICO_BASE_URL / INDICO_TOKEN            — single instance
  INDICO_INSTANCES / INDICO_DEFAULT / ...   — multi-instance
"""

from __future__ import annotations

import os
import urllib.parse
from contextlib import asynccontextmanager
from typing import Annotated, Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import Field

from .client import IndicoClient, IndicoError
from .config import Config
from .models import (
    extract_results,
    normalize_contribution,
    normalize_event,
    normalize_session,
    normalize_event_header,
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
    if _config is None:
        raise RuntimeError("Indico MCP server is not initialised — lifespan did not run")
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
async def search_categories(
    query: Annotated[str, Field(description="Name or partial name of the category to search for.")],
    instance: Annotated[str | None, _instance_field()] = None,
) -> list[dict]:
    """
    Search for Indico categories by name.

    Returns matching categories with their ID, title, full breadcrumb path from the root,
    and event/subcategory counts. Use this to discover the category_id needed by other tools
    when you don't already know it (e.g. 'OKC Colloquia', 'HGTD Speakers Committee').

    Returns up to 10 results. Refine the query if too many or too few results appear.
    """
    client = _client(instance)
    try:
        data = await client.get("category/search", q=query)
    except IndicoError as e:
        raise ValueError(str(e)) from e

    return [
        {k: v for k, v in {
            "id": c.get("id"),
            "title": c.get("title"),
            "path": " > ".join(p["title"] for p in c.get("path", [])),
            "has_events": c.get("has_events"),
            "has_children": c.get("has_children"),
            "event_count": c.get("deep_event_count"),
            "is_protected": c.get("is_protected"),
        }.items() if v is not None}
        for c in data.get("categories", [])
    ]


@app.tool()
async def get_category_contributions(
    category_id: Annotated[int, Field(description="Indico category ID.")],
    from_date: Annotated[str | None, Field(description="Start date filter, YYYY-MM-DD.")] = None,
    to_date: Annotated[str | None, Field(description="End date filter, YYYY-MM-DD.")] = None,
    limit: Annotated[int, Field(description="Maximum contributions to return (default 200, max 500).")] = 200,
    offset: Annotated[int, Field(description="Pagination offset for retrieving further results.")] = 0,
    instance: Annotated[str | None, _instance_field()] = None,
) -> list[dict]:
    """
    Get contributions from ALL events in a category within an optional date range, in a single API call.

    Returns a flat list of contributions, each annotated with event_id, event_title, and event_start
    so you know which event each contribution belongs to.

    This is far more efficient than listing events and calling get_event_contributions for each one.
    Use it for: finding all talks by a speaker across a series of meetings, analysing themes across
    colloquia, counting talks matching a title pattern, or any aggregation over multiple events.

    The API caps results at 500 contributions per request. Use offset to paginate if needed.
    """
    client = _client(instance)
    params: dict[str, Any] = {
        "from": from_date,
        "to": to_date,
        "limit": min(limit, 500),
        "offset": offset,
        "order": "start",
    }
    try:
        data = await client.export(f"categ/{category_id}", detail="contributions", **params)
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    contributions = []
    for event in results:
        event_ctx = normalize_event_header(event)
        for raw_contrib in event.get("contributions", []):
            contrib = normalize_contribution(raw_contrib)
            contrib.update(event_ctx)
            contributions.append(contrib)

    return contributions


@app.tool()
async def search_category_events(
    category_id: Annotated[int, Field(description="Indico category ID. Use 0 for the root (all public events).")] = 0,
    from_date: Annotated[str | None, Field(description="Start date filter, YYYY-MM-DD.")] = None,
    to_date: Annotated[str | None, Field(description="End date filter, YYYY-MM-DD.")] = None,
    keyword: Annotated[str | None, Field(description="Filter event titles by this keyword (case-insensitive).")] = None,
    limit: Annotated[int, Field(description="Maximum number of events to return (default 50, max 1000).")] = 50,
    instance: Annotated[str | None, _instance_field()] = None,
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
    except IndicoError as e:
        if e.status_code != 404:
            raise ValueError(str(e)) from e
        # 404: modern search endpoint not available on this instance, fall through to legacy

    # Legacy: search via the export title-search endpoint
    try:
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
        return {k: v for k, v in {
            "id": data.get("id"),
            "title": data.get("title"),
            "description": data.get("description") or None,
            "url": data.get("url"),
            "parent_id": data.get("parent_id"),
            "subcategories": [
                {"id": c.get("id"), "title": c.get("title")}
                for c in data.get("subcategories", [])
            ] or None,
        }.items() if v is not None}
    except IndicoError as e:
        if e.status_code != 404:
            raise ValueError(str(e)) from e
        # 404: REST categories endpoint not available on this instance, fall through to export

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
