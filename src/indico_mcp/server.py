# Copyright (c) 2026 Christian Ohm. MIT License — see LICENSE file.
"""
Indico MCP Server

Tools:
  search_categories          — find categories by name (returns ID + breadcrumb path)
  find_events_by_title       — search event titles across the whole instance; reveals category IDs
  browse_category            — list direct subcategories of a category by ID
  search_category_events     — list events in a category, filtered by date/keyword
  get_category_contributions — all contributions across events in a category (single call)
  get_event_details          — full event metadata + contributions
  get_event_contributions    — flat list of contributions for an event
  get_event_sessions         — sessions with nested contributions (full agenda)
  search_events_by_keyword   — full-text search via Indico search API
  list_category_info         — metadata about a category, with subcategory names
  list_event_attachments     — list file attachments for an event or contribution
  download_attachment        — download an attachment file to disk

Configuration (see .env.example):
  INDICO_BASE_URL / INDICO_TOKEN            — single instance
  INDICO_INSTANCES / INDICO_DEFAULT / ...   — multi-instance
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import Field

from .client import IndicoClient, IndicoError
from .config import Config
from .models import (
    extract_results,
    normalize_attachment,
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
    instructions="""
Tools for browsing Indico meeting agendas, searching events, and extracting
contribution and session details across one or more configured Indico instances.
Use the `instance` parameter to select between them.

Important:
- Never assume an instance name like "cern" or "su" exists.
- Only use instance names explicitly configured for this server.
- If unsure, omit `instance` to use the configured default.

## Finding the right category_id

Most tools require a category_id. Use this decision tree when you don't know it:

1. **search_categories** — try first with a keyword from the category name
   (e.g. "colloquia", "HGTD", "Stockholm"). Returns up to 10 matches with full
   breadcrumb paths, so you can confirm you have the right one.

2. **find_events_by_title** — if search_categories fails or returns nothing useful,
   search by a known fragment of the *event* title instead
   (e.g. "AlbaNova ATLAS meeting", "HGTD Speakers Committee meeting").
   Every result includes `category_id` and `category` — this immediately tells you
   which category the events live in, even when the category name is something
   unexpected like "Stockholm" or "HGTD Miscellaneous".

3. **browse_category** — if you have a parent category ID but need to find the right
   subcategory, call this to see all subcategories with event counts. Drill down
   level by level. Start at 0 for the root if completely lost.

## Which instance to search

If a meeting is part of a large international experiment (ATLAS, CMS, LHCb, etc.),
it is almost always on the experiments instance 
(e.g. CERN for the LHC experiment ATLAS, CMS, LHCb and IceCube for IceCube)
even if the group is based elsewhere (Stockholm, Paris, Tokyo). 
Local seminars and colloquia are typically on the institute's own instance.

When selecting an instance, first verify the name is configured in this MCP
deployment.

## Retrieving data efficiently

- **Listing events in a known category:** search_category_events
- **Contributions across many events at once** (speaker counts, theme analysis,
  finding a recurring talk slot): get_category_contributions — one API call instead
  of one call per event. Use this whenever you need to aggregate over a meeting series.
- **Single event agenda:** get_event_details or get_event_sessions

## Downloading files

- **List attachments:** list_event_attachments — shows all files (slides, papers, minutes)
  attached to an event or contribution, with download URLs and metadata.
- **Download a file:** download_attachment — downloads a file given its download_url
  (from list_event_attachments) and saves it locally.
""",
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
            "Named Indico instance to query. Use only configured names. "
            "If omitted, the server default instance is used."
        ),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@app.tool()
async def list_instances() -> dict:
    """
    List configured Indico instances and the default instance name.

    Use this first if you are unsure which `instance` values are valid.
    """
    if _config is None:
        raise RuntimeError("Indico MCP server is not initialised — lifespan did not run")
    return {
        "default": _config.default_name,
        "instances": _config.instance_names,
    }


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
async def find_events_by_title(
    title: Annotated[str, Field(description="Event title or partial title to search for.")],
    from_date: Annotated[str | None, Field(description="Start date filter, YYYY-MM-DD.")] = None,
    to_date: Annotated[str | None, Field(description="End date filter, YYYY-MM-DD.")] = None,
    limit: Annotated[int, Field(description="Maximum results to return (default 10).")] = 10,
    instance: Annotated[str | None, _instance_field()] = None,
) -> list[dict]:
    """
    Search for events by title across the entire Indico instance.

    Each result includes category_id and category name, making this the most direct
    way to discover which category a meeting series belongs to when you know part of
    the event title but not the category ID.

    Example: searching 'AlbaNova ATLAS meeting' returns events whose category field
    reads 'Stockholm' with category_id=1384, immediately revealing where those
    meetings live so you can pass that ID to other tools.
    """
    client = _client(instance)
    params: dict[str, Any] = {
        "q": title,
        "from": from_date,
        "to": to_date,
        "limit": min(limit, 100),
        "order": "start",
    }
    try:
        data = await client.export("categ/0", **params)
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    return [normalize_event(r) for r in results[:limit]]


@app.tool()
async def browse_category(
    category_id: Annotated[int, Field(description="Indico category ID. Use 0 for the root.")] = 0,
    instance: Annotated[str | None, _instance_field()] = None,
) -> list[dict]:
    """
    List the direct subcategories of an Indico category, with event counts.

    Works by fetching a batch of recent events from the category and collecting the
    distinct (category_id, category_title) pairs from their metadata. This is the
    reliable fallback when the REST API is not available on an instance.

    Use this to navigate the category hierarchy: start at the root (0), pick a
    subcategory, call browse_category again on that ID, and so on until you find
    the right leaf category to pass to search_category_events or get_category_contributions.

    Note: subcategories with no events in the sampled batch may not appear.
    """
    client = _client(instance)
    try:
        data = await client.export(f"categ/{category_id}", limit=500, order="start")
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)

    seen: dict[int, dict] = {}
    for event in results:
        cid = event.get("categoryId")
        ctitle = event.get("category")
        if cid is None:
            continue
        if cid not in seen:
            seen[cid] = {"id": cid, "title": ctitle, "sampled_event_count": 0}
        seen[cid]["sampled_event_count"] += 1

    # Sort by most events first so the busiest subcategories appear at the top
    return sorted(seen.values(), key=lambda x: -x["sampled_event_count"])


@app.tool()
async def get_category_contributions(
    category_id: Annotated[int, Field(description="Indico category ID.")],
    from_date: Annotated[str | None, Field(description="Start date filter, YYYY-MM-DD.")] = None,
    to_date: Annotated[str | None, Field(description="End date filter, YYYY-MM-DD.")] = None,
    limit: Annotated[int, Field(description="Maximum contributions to return (default 200, max 500).")] = 200,
    offset: Annotated[int, Field(description="Pagination offset for retrieving further results.")] = 0,
    include_attachments: Annotated[bool, Field(description="If true, include contribution attachment metadata in each result.")] = False,
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
    total_count = data.get("count", 0)

    contributions = []
    for event in results:
        event_ctx = normalize_event_header(event)
        for raw_contrib in event.get("contributions", []):
            contrib = normalize_contribution(
                raw_contrib, include_attachments=include_attachments
            )
            contrib.update(event_ctx)
            contributions.append(contrib)

    if not contributions and total_count > 0:
        raise ValueError(
            f"Category {category_id} has {total_count} events but no contributions were "
            "returned. The token most likely lacks the 'Classic API' (legacy_api) scope "
            "required for detail=contributions. Enable it under My Profile → Personal "
            "Tokens on the Indico instance."
        )

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
        "q": keyword,  # server-side title filter; applied before limit
        "limit": min(limit, 1000),
        "order": "start",
    }
    try:
        data = await client.export(f"categ/{category_id}", **params)
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    return [normalize_event(r) for r in results]


@app.tool()
async def get_event_details(
    event_id: Annotated[int, Field(description="Indico event ID.")],
    include_attachments: Annotated[bool, Field(description="If true, include contribution attachment metadata in the event output.")] = False,
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

    return normalize_event(
        results[0],
        include_contributions=True,
        include_contribution_attachments=include_attachments,
    )


@app.tool()
async def get_event_contributions(
    event_id: Annotated[int, Field(description="Indico event ID.")],
    include_attachments: Annotated[bool, Field(description="If true, include attachment metadata for each contribution.")] = False,
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
    return [
        normalize_contribution(c, include_attachments=include_attachments)
        for c in raw_contribs
    ]


@app.tool()
async def get_event_sessions(
    event_id: Annotated[int, Field(description="Indico event ID.")],
    include_attachments: Annotated[bool, Field(description="If true, include attachment metadata for contributions inside each session.")] = False,
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
    return [
        normalize_session(s, include_attachments=include_attachments)
        for s in raw_sessions
    ]


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


@app.tool()
async def list_event_attachments(
    event_id: Annotated[int, Field(description="Indico event ID.")],
    contribution_id: Annotated[int | None, Field(description="If set, only list attachments for this contribution.")] = None,
    instance: Annotated[str | None, _instance_field()] = None,
) -> list[dict]:
    """
    List all file attachments and links for an event (or a specific contribution).

    Returns a flat list of attachments, each with: id, title, filename, content_type,
    size, download_url, and the folder/contribution/event context it belongs to.

    Use this to discover what files (slides, papers, minutes, etc.) are attached to
    an event or contribution before downloading them with download_attachment.
    """
    client = _client(instance)
    try:
        data = await client.export(f"event/{event_id}", detail="contributions")
    except IndicoError as e:
        raise ValueError(str(e)) from e

    results = extract_results(data)
    if not results:
        raise ValueError(f"Event {event_id} not found or not accessible.")

    event = results[0]
    attachments: list[dict] = []

    def _collect(obj: dict, context: dict) -> None:
        for folder in obj.get("folders", []):
            folder_title = folder.get("title", "")
            for att in folder.get("attachments", []):
                entry = normalize_attachment(att)
                entry["folder"] = folder_title
                entry.update(context)
                attachments.append(entry)

    # Event-level attachments
    _collect(event, {"event_id": event_id})

    # Contribution-level attachments
    for contrib in event.get("contributions", []):
        cid = contrib.get("id")
        if contribution_id is not None and cid != contribution_id:
            continue
        ctx = {"event_id": event_id, "contribution_id": cid, "contribution_title": contrib.get("title")}
        _collect(contrib, ctx)

        # Subcontribution-level attachments
        for subcontrib in contrib.get("subContributions", []):
            sub_ctx = {**ctx, "subcontribution_id": subcontrib.get("id"), "subcontribution_title": subcontrib.get("title")}
            _collect(subcontrib, sub_ctx)

    if not attachments:
        return [{"note": "No attachments found for this event." + (
            f" (filtered to contribution {contribution_id})" if contribution_id else ""
        )}]

    return attachments


@app.tool()
async def download_attachment(
    download_url: Annotated[str, Field(description="The download_url from list_event_attachments output.")],
    save_to: Annotated[str | None, Field(
        description="Local file path to save the file to. If not provided, saves to a temp directory."
    )] = None,
    instance: Annotated[str | None, _instance_field()] = None,
) -> dict:
    """
    Download a file attachment from Indico and save it locally.

    Use list_event_attachments first to get the download_url for the file you want.
    Returns the local file path, filename, content type, and size.

    Files are saved to a temporary directory by default, or to a specified path.
    Maximum file size: 100 MB.
    """
    client = _client(instance)
    try:
        result = await client.download(download_url)
    except IndicoError as e:
        raise ValueError(str(e)) from e

    if save_to:
        dest = Path(save_to)
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        tmp_dir = Path(tempfile.gettempdir()) / "indico-mcp-downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        dest = tmp_dir / result.filename

    # Avoid overwriting: append a suffix if needed
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = dest.with_name(f"{stem}_{counter}{suffix}")
            counter += 1

    dest.write_bytes(result.content)

    return {
        "path": str(dest),
        "filename": result.filename,
        "content_type": result.content_type,
        "size": result.size,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
