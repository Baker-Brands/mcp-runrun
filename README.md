# mcp-runrun

MCP server for [Runrun.it](https://runrun.it) — gives Claude access to tasks, projects, clients, users, boards and more via the Runrun.it REST API v1.0.

## Requirements

- Python 3.11+
- Runrun.it **App-Key** and **User-Token** (Settings → Integrations → API)

## Setup

```bash
# 1. Create & activate venv
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Mac/Linux

# 2. Install
pip install -e .
```

## Credentials

Copy `.env.example` to `.env` in the project root and fill in your keys:

```
RUNRUN_APP_KEY=your_app_key
RUNRUN_USER_TOKEN=your_user_token
```

`.env` is gitignored — credentials never leave your machine and never go
into Claude's config files.

## Configure in Claude

**Mac/Linux** — register the wrapper script (it loads `.env` and starts the server):

```bash
claude mcp add --scope user runrun -- /path/to/mcp-runrun/run.sh
```

For the Claude Desktop app, add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "runrun": { "command": "/path/to/mcp-runrun/run.sh" }
  }
}
```

**Windows** — register `run.bat` (same idea: loads `.env`, starts the server).
Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "runrun": { "command": "C:\\mcp-runrun\\run.bat" }
  }
}
```

## Security

- **Never commit `.env`** — it is gitignored on purpose. Don't paste real
  keys into README, issues, commits, or pull requests.
- The **User-Token acts as your Runrun.it user**: anyone with it can read
  and change tasks on your behalf. Treat it like a password.
- If a token leaks, **revoke and regenerate** it immediately at
  Runrun.it → Settings → Integrations → API.
- Each person must use **their own** User-Token — don't share tokens
  between teammates (actions are logged as the token's owner).

## Available Tools

### Performance & filtering notes

Runrun.it task objects are huge (125 fields, ~5.5 KB each). To stay fast and avoid
flooding the model's context, list endpoints (`list_tasks`, `list_projects`,
`list_users`, `list_clients`) return a **concise summary** by default plus
**pagination metadata** (`total`, `has_more`, `next_page`). Pass
`detail_level: "full"` when you genuinely need every field.

Two distinct people are attached to each task:
- **Creator** → `user_id` filter ("tasks I created")
- **Responsible / assignee** → `responsible_id` filter ("tasks assigned to me")

For "my cards / tasks I created", use **`list_my_tasks`** — it resolves the current
user automatically and filters in a single fast call.

### Extracting card content & building spreadsheets

The core workflow ("filter cards → read each card's content → standardized spreadsheet")
is served by two tools:

- **`get_task_content`** — reads ONE card and returns a clean flat record: status,
  board/stage/project/client/type, creator, responsible, all assignees, dates, time
  (raw seconds + `H:MM:SS`), tags, custom fields **with human labels resolved**
  (e.g. `"Área": "Growth"`), and the description converted from HTML to plain text
  (tables → tab-separated cells, lists → bullets, links preserved as `text (url)`).
- **`export_tasks`** — the workhorse. Same filters as `list_tasks`, then extracts a
  standardized record for every matching card (descriptions fetched in parallel,
  paging past the 100-per-request API ceiling up to `max_tasks`). Pass
  `flatten_custom_fields: true` to get one `cf: <label>` column per custom field plus
  a deterministic `columns` list — drop straight into a spreadsheet. Returns
  pagination metadata (`total`, `capped`, `capped_by`, `errors`).

Custom fields that share a label across the org are kept distinct (the field id is
appended). Rate-limited (429) requests are retried with backoff.

### Tools

| Tool | Description |
|------|-------------|
| `list_tasks` | List tasks with filters (creator `user_id`, `responsible_id`, project, board, status). Summary + pagination |
| `set_task_fields` | **Write** custom field values on a task. Keys accept field ids or labels; option labels are resolved to ids automatically; `null` clears |
| `list_field_options` | List the selectable options (id + label) of a custom field |
| `list_my_tasks` | List the authenticated user's own tasks (created or responsible) in one call |
| `get_task_content` | Read one card → standardized record (description as text, tags, resolved custom fields) |
| `export_tasks` | Filter + extract every matching card into uniform, spreadsheet-ready rows |
| `list_boards` / `get_board` | Discover boards / board IDs for filtering |
| `get_task` | Get a task by ID |
| `create_task` | Create a new task. Optional `board_id`/`board_stage_id` move it to the right board immediately (the API always creates on the default board) |
| `update_task` | Update task fields |
| `delete_task` | Delete a task |
| `play_task` | Start timer on a task |
| `pause_task` | Pause timer on a task |
| `close_task` | Close/complete a task |
| `reopen_task` | Reopen a closed task |
| `list_task_comments` | List comments on a task |
| `add_task_comment` | Add a comment to a task |
| `list_task_attachments` | List attachments on a task |
| `list_projects` | List projects |
| `get_project` | Get a project by ID |
| `create_project` | Create a project |
| `update_project` | Update a project |
| `list_clients` | List clients |
| `get_client` | Get a client by ID |
| `create_client` | Create a client |
| `list_users` | List organization users |
| `get_user` | Get a user by ID |
| `get_current_user` | Get the authenticated user |
| `list_activities` | List activity history |
| `list_boards` | List boards |
| `get_board_stages` | Get stages of a board |
| `list_task_types` | List task types |
| `list_teams` | List teams |

## Authentication

Get your credentials at **Runrun.it → Settings → API**:
- **App-Key**: the application key for your organization
- **User-Token**: the personal token for the acting user
