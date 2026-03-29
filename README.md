# indico-mcp

An MCP (Model Context Protocol) server for the [Indico](https://getindico.io/) meeting agenda system, letting AI agents search event categories, browse agendas, and extract contribution and session details.

Works with any Indico instance — configure multiple instances simultaneously and switch between them per tool call.

## Tools

| Tool | Description |
|------|-------------|
| `search_category_events` | List events in a category, filtered by date range and keyword |
| `get_event_details` | Full event metadata including all contributions |
| `get_event_contributions` | Flat list of contributions: speakers, abstract, duration, track, session |
| `get_event_sessions` | Session structure with nested contributions (full agenda view) |
| `search_events_by_keyword` | Full-text search across events |
| `list_category_info` | Category name, description, and subcategory IDs |

All tools accept an optional `instance` parameter to select which Indico server to query.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone <repo>
cd indico-mcp
uv sync
```

## Configuration

Copy the example env file and fill in your details:

```bash
cp .env.example .env
```

### Single instance

```
INDICO_BASE_URL=https://indico.fysik.su.se
INDICO_TOKEN=indp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Multiple instances

```
INDICO_INSTANCES=cern,su
INDICO_DEFAULT=su

INDICO_CERN_URL=https://indico.cern.ch
INDICO_CERN_TOKEN=indp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

INDICO_SU_URL=https://indico.fysik.su.se
INDICO_SU_TOKEN=indp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`INDICO_DEFAULT` sets which instance is used when no `instance=` argument is passed to a tool. Tokens are optional for public-only access.

### Getting a token

1. Log in to your Indico instance
2. Go to **My Profile → HTTP API** (or `/user/preferences/api`)
3. Click **Create API key**
4. Copy the token (starts with `indp_`)
5. Required scope: `legacy_api`

## Connecting to Claude

Add to your Claude config (`~/.claude.json` or Claude Desktop settings):

```json
{
  "mcpServers": {
    "indico": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/indico-mcp", "indico-mcp"]
    }
  }
}
```

The `.env` file in the project directory is loaded automatically. Alternatively, pass configuration directly via the `env` block in the MCP config:

```json
{
  "mcpServers": {
    "indico": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/indico-mcp", "indico-mcp"],
      "env": {
        "INDICO_INSTANCES": "cern,su",
        "INDICO_DEFAULT": "su",
        "INDICO_CERN_URL": "https://indico.cern.ch",
        "INDICO_CERN_TOKEN": "indp_xxx",
        "INDICO_SU_URL": "https://indico.fysik.su.se",
        "INDICO_SU_TOKEN": "indp_yyy"
      }
    }
  }
}
```

## Usage examples

```python
# Events in the next month (default instance)
search_category_events(from_date="2025-04-01", to_date="2025-04-30")

# Events in a specific category on CERN Indico
search_category_events(category_id=72, instance="cern")

# All contributions for a meeting, with speakers and abstracts
get_event_contributions(event_id=1234567, instance="cern")

# Full session/agenda structure of a conference
get_event_sessions(event_id=9876543, instance="su")

# Full-text search
search_events_by_keyword("dark matter", instance="cern")

# Navigate the category hierarchy
list_category_info(category_id=0, instance="su")
```

## How it works

The server uses the [Indico HTTP Export API](https://docs.getindico.io/en/stable/http-api/) (`/export/`) with `detail=contributions` and `detail=sessions` query parameters to retrieve structured agenda data. Authentication uses a standard `Authorization: Bearer <token>` header. The newer REST search API (`/api/search/`) is used for keyword search where available, with automatic fallback to the legacy title-search endpoint.

## License

MIT. Indico itself is also [MIT licensed](https://github.com/indico/indico/blob/master/LICENSE).
