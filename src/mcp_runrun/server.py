"""MCP server for Runrun.it — 100% endpoint coverage."""

import asyncio
import json
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, ToolAnnotations

from .client import RunrunClient

# ── Helpers ───────────────────────────────────────────────────────────────────

_READ_PREFIXES = ("list_", "get_")
_DESTRUCTIVE_PREFIXES = ("delete_", "destroy_", "remove_", "unshare_", "unmark_")


def _annotations(name: str) -> ToolAnnotations:
    """Derive behavioral hints from the tool's name prefix."""
    read_only = name.startswith(_READ_PREFIXES)
    destructive = name.startswith(_DESTRUCTIVE_PREFIXES)
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=read_only or name.startswith(("update_", "delete_")),
        openWorldHint=True,
    )


def _tool(name: str, description: str, props: dict[str, Any], required: list[str] | None = None) -> Tool:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema, annotations=_annotations(name))


def _int(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}

def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}

def _bool(desc: str) -> dict[str, Any]:
    return {"type": "boolean", "description": desc}

def _arr(desc: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


# ── Response summarizers ────────────────────────────────────────────────────────
# The Runrun.it API returns very large objects (a single task has 125 fields,
# ~5.5 KB). Returning the full payload for list endpoints is slow and floods the
# model's context. By default we project each item down to the fields that matter
# for triage/decision-making; pass detail_level="full" to get everything.

WEB_TASK_URL = "https://runrun.it/tasks/{id}"

_TASK_SUMMARY_FIELDS = (
    "id", "title", "is_closed", "state", "is_urgent", "priority",
    "board_name", "board_stage_name", "project_name", "client_name", "type_name",
    "user_id", "user_name", "responsible_id", "responsible_name",
    "desired_date", "start_date", "close_date", "created_at",
    "time_worked", "time_total", "tags",
)

_PROJECT_SUMMARY_FIELDS = (
    "id", "name", "client_id", "client_name", "is_closed",
    "project_group_name", "project_sub_group_name", "time_worked", "created_at",
)

_USER_SUMMARY_FIELDS = (
    "id", "name", "email", "is_master", "is_manager", "position",
    "on_vacation", "team_ids",
)

_CLIENT_SUMMARY_FIELDS = ("id", "name", "is_active", "created_at")


def _project(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    out = {k: item[k] for k in fields if k in item}
    if "id" in item and fields is _TASK_SUMMARY_FIELDS:
        out["url"] = WEB_TASK_URL.format(id=item["id"])
    return out


def _summarize_page(page: dict[str, Any], fields: tuple[str, ...], detail_level: str) -> dict[str, Any]:
    """Apply field projection to a paginated result unless detail_level == 'full'."""
    items = page.get("items", [])
    if detail_level != "full":
        items = [_project(it, fields) if isinstance(it, dict) else it for it in items]
    return {
        "pagination": {
            "total": page.get("total"),
            "returned": page.get("returned"),
            "page": page.get("page"),
            "limit": page.get("limit"),
            "has_more": page.get("has_more"),
            "next_page": page.get("next_page"),
        },
        "items": items,
    }

# ── Tool registry ─────────────────────────────────────────────────────────────

TOOLS: list[Tool] = [

    # ── Activities ─────────────────────────────────────────────────────────────
    _tool("list_activities", "List activity history with optional filters.",
          {"task_id": _int("Filter by task ID"), "user_id": _str("Filter by user ID"),
           "limit": _int("Max results"), "page": _int("Page number")}),

    # ── Boards ─────────────────────────────────────────────────────────────────
    _tool("list_boards", "List all boards (workflows). Use this to discover board_id values "
          "for filtering tasks.", {"limit": _int("Results per page"), "page": _int("Page")}),
    _tool("get_board", "Get a specific board by ID.", {"id": _int("Board ID")}, ["id"]),

    # ── Board Stages ───────────────────────────────────────────────────────────
    _tool("list_board_stages", "List all stages of a board.",
          {"board_id": _int("Board ID"), "limit": _int("Max results"), "page": _int("Page")}, ["board_id"]),
    _tool("get_board_stage", "Get a specific stage of a board.",
          {"board_id": _int("Board ID"), "stage_id": _int("Stage ID")}, ["board_id", "stage_id"]),
    _tool("create_board_stage", "Create a new stage on a board.",
          {"board_id": _int("Board ID"), "name": _str("Stage name"),
           "is_closed": _bool("Whether this is a closed/done stage")}, ["board_id", "name"]),
    _tool("update_board_stage", "Update a board stage.",
          {"board_id": _int("Board ID"), "stage_id": _int("Stage ID"), "name": _str("New name"),
           "is_closed": _bool("Closed status")}, ["board_id", "stage_id"]),
    _tool("delete_board_stage", "Delete a board stage.",
          {"board_id": _int("Board ID"), "stage_id": _int("Stage ID")}, ["board_id", "stage_id"]),
    _tool("move_board_stage", "Move a board stage to another position.",
          {"board_id": _int("Board ID"), "stage_id": _int("Stage ID"),
           "position": _int("Target position")}, ["board_id", "stage_id"]),

    # ── Checklist Items ────────────────────────────────────────────────────────
    _tool("list_checklist_items", "List all items in a checklist.",
          {"checklist_id": _int("Checklist ID")}, ["checklist_id"]),
    _tool("get_checklist_item", "Get a specific checklist item.",
          {"checklist_id": _int("Checklist ID"), "item_id": _int("Item ID")}, ["checklist_id", "item_id"]),
    _tool("create_checklist_item", "Create a new item in a checklist.",
          {"checklist_id": _int("Checklist ID"), "name": _str("Item name"),
           "is_done": _bool("Mark as done")}, ["checklist_id", "name"]),
    _tool("update_checklist_item", "Update a checklist item.",
          {"checklist_id": _int("Checklist ID"), "item_id": _int("Item ID"),
           "name": _str("New name"), "is_done": _bool("Done status")}, ["checklist_id", "item_id"]),
    _tool("delete_checklist_item", "Delete a checklist item.",
          {"checklist_id": _int("Checklist ID"), "item_id": _int("Item ID")}, ["checklist_id", "item_id"]),

    # ── Checklists ─────────────────────────────────────────────────────────────
    _tool("get_task_checklist", "Get the checklist of a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("create_task_checklist", "Create a checklist for a task.",
          {"task_id": _int("Task ID"), "name": _str("Checklist name")}, ["task_id", "name"]),
    _tool("update_task_checklist", "Update the checklist of a task.",
          {"task_id": _int("Task ID"), "name": _str("New name")}, ["task_id"]),
    _tool("delete_task_checklist", "Delete the checklist of a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("get_template_checklist", "Get the checklist of a task template.",
          {"template_id": _int("Task template ID")}, ["template_id"]),
    _tool("create_template_checklist", "Create a checklist for a task template.",
          {"template_id": _int("Task template ID"), "name": _str("Checklist name")}, ["template_id", "name"]),
    _tool("update_template_checklist", "Update the checklist of a task template.",
          {"template_id": _int("Task template ID"), "name": _str("New name")}, ["template_id"]),
    _tool("delete_template_checklist", "Delete the checklist of a task template.",
          {"template_id": _int("Task template ID")}, ["template_id"]),

    # ── Clients ────────────────────────────────────────────────────────────────
    _tool("list_clients", "List all clients (concise summary + pagination metadata).",
          {"limit": _int("Results per page (default 50)"), "page": _int("Page"),
           "detail_level": {"type": "string", "enum": ["summary", "full"],
                            "description": "summary (default) or full"}}),
    _tool("get_client", "Get a specific client by ID.",
          {"id": _int("Client ID")}, ["id"]),
    _tool("create_client", "Create a new client.",
          {"name": _str("Client name")}, ["name"]),
    _tool("update_client", "Update an existing client.",
          {"id": _int("Client ID"), "name": _str("New name")}, ["id"]),
    _tool("list_client_budgets", "List monthly budgets for a client.",
          {"client_id": _int("Client ID")}, ["client_id"]),
    _tool("update_client_budget", "Create or update a monthly budget for a client.",
          {"client_id": _int("Client ID"), "month": _str("Month (YYYY-MM)"),
           "amount": {"type": "number", "description": "Budget amount"}}, ["client_id"]),

    # ── Comments ───────────────────────────────────────────────────────────────
    _tool("list_task_comments", "List all comments on a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("get_comment", "Get a specific comment by ID.",
          {"id": _int("Comment ID")}, ["id"]),
    _tool("create_comment", "Create a new comment (on a task, project, etc.).",
          {"text": _str("Comment text"), "commentable_type": _str("Type: Task, Project, Team, Enterprise"),
           "commentable_id": _int("ID of the item being commented on")}, ["text", "commentable_type", "commentable_id"]),
    _tool("update_comment", "Edit an existing comment.",
          {"id": _int("Comment ID"), "text": _str("New text")}, ["id", "text"]),
    _tool("delete_comment", "Delete a comment.",
          {"id": _int("Comment ID")}, ["id"]),
    _tool("react_to_comment", "Add a reaction (emoji) to a comment.",
          {"id": _int("Comment ID"), "reaction": _str("Reaction emoji or code")}, ["id", "reaction"]),

    # ── Demanders ──────────────────────────────────────────────────────────────
    _tool("list_demanders", "List all demanders (requesters) of a user.",
          {"user_id": _str("User ID (slug)")}, ["user_id"]),
    _tool("add_demander", "Add a demander to a user.",
          {"user_id": _str("User ID"), "demander_id": _str("Demander user ID")}, ["user_id", "demander_id"]),
    _tool("replace_demanders", "Replace a user's full demanders list.",
          {"user_id": _str("User ID"), "demander_ids": _arr("List of demander user IDs")}, ["user_id", "demander_ids"]),
    _tool("delete_demander", "Remove a demander from a user.",
          {"user_id": _str("User ID"), "demander_id": _str("Demander user ID")}, ["user_id", "demander_id"]),

    # ── Descendants ────────────────────────────────────────────────────────────
    _tool("list_descendants", "List all descendant tasks of a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("add_descendant", "Add a descendant (subtask link) to a task.",
          {"task_id": _int("Parent task ID"), "descendant_id": _int("Descendant task ID")},
          ["task_id", "descendant_id"]),
    _tool("delete_descendant", "Remove a descendant link from a task.",
          {"task_id": _int("Parent task ID"), "descendant_id": _int("Descendant task ID")},
          ["task_id", "descendant_id"]),

    # ── Description ────────────────────────────────────────────────────────────
    _tool("get_descriptions", "Query multiple descriptions (tasks/projects).",
          {"ids": _arr("List of IDs"), "type": _str("Type: task or project")}),
    _tool("get_task_description", "Get the description of a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("update_task_description", "Update the description of a task (supports HTML).",
          {"task_id": _int("Task ID"), "description": _str("HTML description content")},
          ["task_id", "description"]),

    # ── Documents ──────────────────────────────────────────────────────────────
    _tool("list_task_documents", "List all documents attached to a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("get_document", "Get a specific document by ID.",
          {"id": _int("Document ID")}, ["id"]),
    _tool("delete_document", "Delete a document.",
          {"id": _int("Document ID")}, ["id"]),

    # ── Enterprises ────────────────────────────────────────────────────────────
    _tool("get_enterprise", "Get details for the authenticated user's enterprise/organization.",
          {}),
    _tool("update_enterprise", "Update enterprise/organization settings.",
          {"name": _str("Enterprise name")}),

    # ── Estimates ──────────────────────────────────────────────────────────────
    _tool("list_task_estimates", "List all estimates for a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("create_task_estimate", "Create an estimate for a task.",
          {"task_id": _int("Task ID"), "user_id": _str("User ID"),
           "seconds": _int("Estimated seconds to complete")}, ["task_id"]),

    # ── Event Notifications ────────────────────────────────────────────────────
    _tool("list_event_notifications", "List event notifications for a user.",
          {"user_id": _str("User ID"), "limit": _int("Max results"), "page": _int("Page")}, ["user_id"]),
    _tool("mark_notification_read", "Mark a notification as read.",
          {"user_id": _str("User ID"), "notification_id": _int("Notification ID")},
          ["user_id", "notification_id"]),
    _tool("mark_all_notifications_read", "Mark all notifications as read for a user.",
          {"user_id": _str("User ID")}, ["user_id"]),
    _tool("delete_notification", "Delete a specific notification.",
          {"user_id": _str("User ID"), "notification_id": _int("Notification ID")},
          ["user_id", "notification_id"]),
    _tool("delete_all_notifications", "Delete all notifications for a user.",
          {"user_id": _str("User ID")}, ["user_id"]),

    # ── Filters ────────────────────────────────────────────────────────────────
    _tool("list_project_filters", "List all saved project filters.", {}),
    _tool("get_project_filter", "Get a saved project filter by ID.",
          {"id": _int("Filter ID")}, ["id"]),
    _tool("delete_project_filter", "Delete a saved project filter.",
          {"id": _int("Filter ID")}, ["id"]),
    _tool("list_task_filters", "List all saved task filters.", {}),
    _tool("get_task_filter", "Get a saved task filter by ID.",
          {"id": _int("Filter ID")}, ["id"]),
    _tool("delete_task_filter", "Delete a saved task filter.",
          {"id": _int("Filter ID")}, ["id"]),

    # ── Justifications ─────────────────────────────────────────────────────────
    _tool("create_justification", "Create a justification (for a late/early task).",
          {"text": _str("Justification text"), "task_id": _int("Optional task ID")}),
    _tool("update_justification", "Update the text of a justification.",
          {"justification_id": _int("Justification ID"), "text": _str("New text")},
          ["justification_id", "text"]),

    # ── Manual Work Periods ────────────────────────────────────────────────────
    _tool("list_manual_work_periods", "List all manual time entries.",
          {"task_id": _int("Filter by task"), "user_id": _str("Filter by user"),
           "limit": _int("Max results"), "page": _int("Page")}),
    _tool("get_manual_work_period", "Get a specific manual time entry.",
          {"id": _int("Manual work period ID")}, ["id"]),
    _tool("create_manual_work_period", "Add a manual time entry to a task.",
          {"task_id": _int("Task ID"), "user_id": _str("User ID"),
           "start_time": _str("Start datetime (ISO 8601)"), "end_time": _str("End datetime (ISO 8601)")},
          ["task_id", "user_id", "start_time", "end_time"]),
    _tool("update_manual_work_period", "Update a manual time entry.",
          {"id": _int("Manual work period ID"), "start_time": _str("Start datetime"),
           "end_time": _str("End datetime")}, ["id"]),
    _tool("delete_manual_work_period", "Delete a manual time entry.",
          {"id": _int("Manual work period ID")}, ["id"]),

    # ── Off Days ───────────────────────────────────────────────────────────────
    _tool("list_off_days", "List all off days (holidays/non-working days).",
          {"limit": _int("Max results"), "page": _int("Page")}),
    _tool("get_off_day", "Get a specific off day.",
          {"id": _int("Off day ID")}, ["id"]),
    _tool("create_off_day", "Create a new off day.",
          {"date": _str("Date (YYYY-MM-DD)"), "name": _str("Off day name/reason")}, ["date"]),
    _tool("update_off_day", "Update an off day.",
          {"id": _int("Off day ID"), "date": _str("Date"), "name": _str("Name")}, ["id"]),
    _tool("delete_off_day", "Delete an off day.",
          {"id": _int("Off day ID")}, ["id"]),

    # ── Partners ───────────────────────────────────────────────────────────────
    _tool("list_partners", "List all partners of a user.",
          {"user_id": _str("User ID")}, ["user_id"]),
    _tool("add_partner", "Add a partner to a user.",
          {"user_id": _str("User ID"), "partner_id": _str("Partner user ID")}, ["user_id", "partner_id"]),
    _tool("replace_partners", "Replace a user's full partners list.",
          {"user_id": _str("User ID"), "partner_ids": _arr("Partner user IDs")}, ["user_id", "partner_ids"]),
    _tool("delete_partner", "Remove a partner from a user.",
          {"user_id": _str("User ID"), "partner_id": _str("Partner user ID")}, ["user_id", "partner_id"]),

    # ── Prerequisites ──────────────────────────────────────────────────────────
    _tool("list_prerequisites", "List all prerequisite tasks of a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("add_prerequisite", "Add a prerequisite (dependency) to a task.",
          {"task_id": _int("Task ID"), "prerequisite_id": _int("Prerequisite task ID")},
          ["task_id", "prerequisite_id"]),
    _tool("delete_prerequisite", "Remove a prerequisite from a task.",
          {"task_id": _int("Task ID"), "prerequisite_id": _int("Prerequisite task ID")},
          ["task_id", "prerequisite_id"]),

    # ── Project Groups ─────────────────────────────────────────────────────────
    _tool("list_project_groups", "List all project groups.", {}),
    _tool("create_project_group", "Create a new project group.",
          {"name": _str("Group name")}, ["name"]),
    _tool("update_project_group", "Update a project group.",
          {"id": _int("Group ID"), "name": _str("New name")}, ["id"]),
    _tool("delete_project_group", "Delete a project group.",
          {"id": _int("Group ID")}, ["id"]),

    # ── Project Sub Groups ─────────────────────────────────────────────────────
    _tool("list_project_sub_groups", "List sub-groups of a project group.",
          {"group_id": _int("Project group ID")}, ["group_id"]),
    _tool("create_project_sub_group", "Create a sub-group inside a project group.",
          {"group_id": _int("Project group ID"), "name": _str("Sub-group name")}, ["group_id", "name"]),
    _tool("update_project_sub_group", "Update a project sub-group.",
          {"group_id": _int("Project group ID"), "sub_group_id": _int("Sub-group ID"),
           "name": _str("New name")}, ["group_id", "sub_group_id"]),
    _tool("delete_project_sub_group", "Delete a project sub-group.",
          {"group_id": _int("Project group ID"), "sub_group_id": _int("Sub-group ID")},
          ["group_id", "sub_group_id"]),
    _tool("move_project_sub_group", "Move a sub-group to another position.",
          {"sub_group_id": _int("Sub-group ID"), "position": _int("Target position")}, ["sub_group_id"]),

    # ── Project Templates ──────────────────────────────────────────────────────
    _tool("list_project_templates", "List all project templates.", {}),
    _tool("get_project_template", "Get a specific project template.",
          {"id": _int("Template ID")}, ["id"]),
    _tool("create_project_template", "Create a new project template.",
          {"name": _str("Template name")}, ["name"]),
    _tool("update_project_template", "Update a project template.",
          {"id": _int("Template ID"), "name": _str("New name")}, ["id"]),
    _tool("delete_project_template", "Delete a project template.",
          {"id": _int("Template ID")}, ["id"]),

    # ── Projects ───────────────────────────────────────────────────────────────
    _tool("list_projects", "List all projects (concise summary + pagination metadata).",
          {"client_id": _int("Filter by client"), "is_active": _bool("Filter by active status"),
           "limit": _int("Results per page (default 50)"), "page": _int("Page"),
           "detail_level": {"type": "string", "enum": ["summary", "full"],
                            "description": "summary (default) or full"}}),
    _tool("get_project", "Get a specific project by ID.",
          {"id": _int("Project ID")}, ["id"]),
    _tool("create_project", "Create a new project.",
          {"name": _str("Project name"), "client_id": _int("Client ID"),
           "project_group_id": _int("Project group ID"),
           "project_sub_group_id": _int("Project sub-group ID")}, ["name"]),
    _tool("update_project", "Update an existing project.",
          {"id": _int("Project ID"), "name": _str("New name"), "client_id": _int("Client ID"),
           "is_active": _bool("Active status")}, ["id"]),
    _tool("get_project_related_users", "Get all users related to a project.",
          {"id": _int("Project ID")}, ["id"]),
    _tool("move_project", "Move a project to a different group/sub-group.",
          {"id": _int("Project ID"), "project_group_id": _int("Target group ID"),
           "project_sub_group_id": _int("Target sub-group ID")}, ["id"]),
    _tool("clone_project", "Clone a project.",
          {"id": _int("Project ID"), "name": _str("New project name")}, ["id"]),
    _tool("change_project_board_stage", "Move a project to a different board stage.",
          {"id": _int("Project ID"), "board_stage_id": _int("Target board stage ID")},
          ["id", "board_stage_id"]),

    # ── Project Extra Costs ────────────────────────────────────────────────────
    _tool("list_project_extra_costs", "List extra costs of a project.",
          {"project_id": _int("Project ID")}, ["project_id"]),
    _tool("get_project_extra_cost", "Get a specific extra cost entry.",
          {"project_id": _int("Project ID"), "cost_id": _int("Extra cost ID")}, ["project_id", "cost_id"]),
    _tool("create_project_extra_cost", "Add an extra cost to a project.",
          {"project_id": _int("Project ID"), "description": _str("Cost description"),
           "amount": {"type": "number", "description": "Cost amount"}}, ["project_id"]),

    # ── Reports ────────────────────────────────────────────────────────────────
    _tool("get_time_worked_report", "Get a time worked report with filters.",
          {"user_id": _str("Filter by user"), "task_id": _int("Filter by task"),
           "project_id": _int("Filter by project"), "start_date": _str("Start date (YYYY-MM-DD)"),
           "end_date": _str("End date (YYYY-MM-DD)"), "limit": _int("Max results"), "page": _int("Page")}),

    # ── Tags ───────────────────────────────────────────────────────────────────
    _tool("list_tags", "List all available tags.", {}),

    # ── Task Assignments ───────────────────────────────────────────────────────
    _tool("delete_task_assignment", "Remove an assignee from a task.",
          {"task_id": _int("Task ID"), "assignment_id": _int("Assignment ID")},
          ["task_id", "assignment_id"]),
    _tool("play_task_assignment", "Start timer for a specific task assignment.",
          {"task_id": _int("Task ID"), "assignment_id": _int("Assignment ID")},
          ["task_id", "assignment_id"]),
    _tool("pause_task_assignment", "Pause timer for a specific task assignment.",
          {"task_id": _int("Task ID"), "assignment_id": _int("Assignment ID")},
          ["task_id", "assignment_id"]),
    _tool("deliver_task_assignment", "Deliver a specific task assignment.",
          {"task_id": _int("Task ID"), "assignment_id": _int("Assignment ID")},
          ["task_id", "assignment_id"]),
    _tool("reopen_task_assignment", "Reopen a delivered task assignment.",
          {"task_id": _int("Task ID"), "assignment_id": _int("Assignment ID")},
          ["task_id", "assignment_id"]),

    # ── Task Evaluations ───────────────────────────────────────────────────────
    _tool("list_task_evaluations", "List task evaluations.",
          {"task_id": _int("Filter by task"), "limit": _int("Max results"), "page": _int("Page")}),
    _tool("create_task_evaluation", "Create a task evaluation.",
          {"task_id": _int("Task ID"), "evaluator_id": _str("Evaluator user ID")}, ["task_id"]),

    # ── Task Types ─────────────────────────────────────────────────────────────
    _tool("list_task_types", "List all task types.", {}),
    _tool("get_task_type", "Get a specific task type.",
          {"id": _int("Task type ID")}, ["id"]),
    _tool("create_task_type", "Create a new task type.",
          {"name": _str("Task type name")}, ["name"]),
    _tool("update_task_type", "Update a task type.",
          {"id": _int("Task type ID"), "name": _str("New name")}, ["id"]),

    # ── Tasks ──────────────────────────────────────────────────────────────────
    _tool("list_tasks",
          "List tasks with optional filters. Returns a concise summary of each task plus "
          "pagination metadata (total, has_more, next_page). To find tasks a person CREATED, "
          "filter by user_id; to find tasks a person is RESPONSIBLE for, filter by responsible_id. "
          "User IDs are slugs like 'tales-germano' (use get_current_user or list_users to resolve). "
          "Pass detail_level='full' only when you need every field of each task.",
          {"user_id": _str("Filter by CREATOR (the user who created the task), e.g. 'tales-germano'"),
           "responsible_id": _str("Filter by the RESPONSIBLE/assigned user (slug)"),
           "project_id": _int("Filter by project"),
           "board_id": _int("Filter by board"),
           "team_id": _int("Filter by team"),
           "type_id": _int("Filter by task type"),
           "is_closed": _bool("true = only closed/delivered tasks, false = only open tasks"),
           "limit": _int("Results per page (default 50, max 100)"),
           "page": _int("Page number (1-based)"),
           "detail_level": {"type": "string", "enum": ["summary", "full"],
                            "description": "summary (default, ~15 key fields) or full (all 125 fields)"}}),
    _tool("list_my_tasks",
          "Convenience tool: list the authenticated user's own tasks in a single call. "
          "Resolves the current user automatically, then filters by role. Use this for requests "
          "like 'my cards', 'tasks I created', 'what am I working on'.",
          {"role": {"type": "string", "enum": ["created", "responsible"],
                    "description": "created = tasks I created (default); responsible = tasks assigned to me"},
           "is_closed": _bool("true = closed only, false = open only, omit = both"),
           "limit": _int("Results per page (default 50)"),
           "page": _int("Page number"),
           "detail_level": {"type": "string", "enum": ["summary", "full"],
                            "description": "summary (default) or full"}}),
    _tool("get_task", "Get a specific task by ID (full raw object, 125 fields).",
          {"id": _int("Task ID")}, ["id"]),
    _tool("get_task_content",
          "Read ONE card and return its content in a clean, standardized, flat shape ready for "
          "tabulation: title, status, board/stage/project/client/type, creator, responsible, dates, "
          "TAGS, CUSTOM FIELDS (with human labels resolved, e.g. 'Área': 'Growth'), and the "
          "DESCRIPTION converted from HTML to plain text. Use this instead of get_task when you "
          "want the card's actual content rather than raw metadata.",
          {"task_id": _int("Task ID"),
           "include_description": _bool("Include the description body (default true)"),
           "include_comments": _bool("Include human comments, excluding system messages (default false)"),
           "html_format": {"type": "string", "enum": ["text", "html"],
                           "description": "How to return the description: text (default) or raw html"}},
          ["task_id"]),
    _tool("export_tasks",
          "THE workhorse for 'filter cards, read each one, and tabulate them'. Filters tasks (same "
          "filters as list_tasks), then for EVERY matching task extracts a standardized content "
          "record (title, status, board/stage/project/client/type, creator, responsible, dates, tags, "
          "custom fields with resolved labels, and the description as plain text). Returns an array of "
          "uniform rows plus the custom-field label map — drop straight into a spreadsheet. "
          "Descriptions are fetched in parallel. Respects max_tasks (default 50) and reports if the "
          "result was capped.",
          {"user_id": _str("Filter by CREATOR (slug, e.g. 'tales-germano')"),
           "responsible_id": _str("Filter by RESPONSIBLE user (slug)"),
           "project_id": _int("Filter by project"),
           "board_id": _int("Filter by board"),
           "team_id": _int("Filter by team"),
           "type_id": _int("Filter by task type"),
           "is_closed": _bool("true = closed only, false = open only, omit = both"),
           "include_description": _bool("Include description body as text (default true)"),
           "include_comments": _bool("Include human comments (default false)"),
           "html_format": {"type": "string", "enum": ["text", "html"],
                           "description": "Description format: text (default) or html"},
           "flatten_custom_fields": _bool("If true, promote each custom field to its own 'cf: <label>' "
                                          "column and join list cells into strings — ideal for a flat "
                                          "spreadsheet. Also returns an ordered 'columns' list. Default false."),
           "max_tasks": _int("Maximum tasks to export (default 50; pages through results to exceed 100). "
                             "Each task adds ~1 API call (~2 with comments); rate limit is 100/min.")}),
    _tool("get_task_subtasks", "Get all subtasks of a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("create_task", "Create a new task. The Runrun.it API always creates the task on the default "
          "board; pass board_id (and optionally board_stage_id) to have it moved to the right board "
          "immediately after creation, in a single call.",
          {"title": _str("Task title"), "project_id": _int("Project ID"),
           "responsible_id": _str("Responsible user ID"), "type_id": _int("Task type ID"),
           "description": _str("Task description"), "desired_date": _str("Desired date (YYYY-MM-DD)"),
           "desired_start_date": _str("Desired start date (YYYY-MM-DD)"),
           "estimated_delivery_date": _str("Estimated delivery date"),
           "board_id": _int("Target board ID — task is moved there right after creation"),
           "board_stage_id": _int("Target stage ID on the target board"),
           "priority": {"type": "string", "enum": ["none", "low", "medium", "high"],
                        "description": "Priority level"}},
          ["title", "project_id"]),
    _tool("update_task", "Update an existing task.",
          {"id": _int("Task ID"), "title": _str("New title"), "description": _str("New description"),
           "responsible_id": _str("New responsible user ID"), "desired_date": _str("Desired date (YYYY-MM-DD)"),
           "desired_start_date": _str("Desired start date"), "priority": _str("Priority level"),
           "estimated_delivery_date": _str("Estimated delivery date")}, ["id"]),
    _tool("delete_task", "Delete a task.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("play_task", "Start the timer on a task.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("pause_task", "Pause the timer on a task.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("deliver_task", "Deliver/complete a task (moves to closed stage).",
          {"id": _int("Task ID")}, ["id"]),
    _tool("reopen_task", "Reopen a delivered/closed task.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("change_task_board", "Move a task to a different board.",
          {"id": _int("Task ID"), "board_id": _int("Target board ID"),
           "board_stage_id": _int("Target stage ID")}, ["id", "board_id"]),
    _tool("change_task_project", "Move a task to a different project.",
          {"id": _int("Task ID"), "project_id": _int("Target project ID")}, ["id", "project_id"]),
    _tool("change_task_type", "Change the type of a task.",
          {"id": _int("Task ID"), "type_id": _int("New task type ID")}, ["id", "type_id"]),
    _tool("mark_task_urgent", "Mark a task as urgent.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("unmark_task_urgent", "Remove the urgent flag from a task.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("create_task_assignments", "Add assignees to a task.",
          {"id": _int("Task ID"),
           "assignments": {"type": "array", "items": {"type": "object"},
                           "description": "Array of assignment objects with user_id"}}, ["id"]),
    _tool("move_task_to_top", "Move a task to the top of its queue.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("move_task_to_next_stage", "Move a task to the next stage on its board.",
          {"id": _int("Task ID")}, ["id"]),
    _tool("clone_task", "Clone/duplicate a task.",
          {"task_id": _int("Source task ID"), "title": _str("New task title")}, ["task_id"]),
    _tool("get_task_form_answers", "Get form field answers for a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("get_task_fields", "Get custom field definitions and values for a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),
    _tool("list_field_options", "List the selectable options (id + label) of a custom field. "
          "Use get_task_fields to discover field ids (custom_XX) and labels.",
          {"field_id": _str("Custom field ID, e.g. 'custom_30'")}, ["field_id"]),
    _tool("set_task_fields", "Set custom field values on a task (write counterpart of get_task_fields). "
          "Keys may be field ids ('custom_30') or field labels ('Área'); values may be option labels or "
          "option ids — labels are resolved automatically. Use an array for multiple_options fields and "
          "null to clear a field.",
          {"task_id": _int("Task ID"),
           "values": {"type": "object",
                      "description": "Mapping of field id/label -> value(s), e.g. "
                                     "{\"Área\": \"Growth\", \"País\": [\"Brasil\", \"Reino Unido\"], "
                                     "\"custom_100\": null}"}},
          ["task_id", "values"]),
    _tool("list_task_attachments", "List all file attachments on a task.",
          {"task_id": _int("Task ID")}, ["task_id"]),

    # ── Teams ──────────────────────────────────────────────────────────────────
    _tool("list_teams", "List all teams.", {}),
    _tool("get_team", "Get a specific team by ID.",
          {"id": _int("Team ID")}, ["id"]),
    _tool("create_team", "Create a new team.",
          {"name": _str("Team name")}, ["name"]),
    _tool("update_team", "Update an existing team.",
          {"id": _int("Team ID"), "name": _str("New name")}, ["id"]),
    _tool("delete_team", "Delete a team.",
          {"id": _int("Team ID")}, ["id"]),
    _tool("add_team_member", "Add a member to a team.",
          {"team_id": _int("Team ID"), "user_id": _str("User ID")}, ["team_id", "user_id"]),
    _tool("remove_team_member", "Remove a member from a team.",
          {"team_id": _int("Team ID"), "user_id": _str("User ID")}, ["team_id", "user_id"]),

    # ── Users ──────────────────────────────────────────────────────────────────
    _tool("list_users", "List all users in the organization (concise summary + pagination metadata).",
          {"limit": _int("Results per page (default 50)"), "page": _int("Page"),
           "detail_level": {"type": "string", "enum": ["summary", "full"],
                            "description": "summary (default) or full"}}),
    _tool("get_user", "Get a specific user by ID.",
          {"id": _str("User ID (slug or numeric)")}, ["id"]),
    _tool("get_current_user", "Get the currently authenticated user.", {}),
    _tool("create_user", "Create a new user.",
          {"name": _str("Full name"), "email": _str("Email address"),
           "password": _str("Initial password")}, ["name", "email"]),
    _tool("update_user", "Update a user.",
          {"id": _str("User ID"), "name": _str("New name"), "email": _str("New email")}, ["id"]),

    # ── User Vacations ─────────────────────────────────────────────────────────
    _tool("list_vacations", "List user vacation periods.",
          {"limit": _int("Max results"), "page": _int("Page")}),
    _tool("get_vacation", "Get a specific vacation period.",
          {"id": _int("Vacation ID")}, ["id"]),
    _tool("create_vacation", "Create a vacation period for a user.",
          {"user_id": _str("User ID"), "start_date": _str("Start date (YYYY-MM-DD)"),
           "end_date": _str("End date (YYYY-MM-DD)")}, ["user_id", "start_date", "end_date"]),
    _tool("update_vacation", "Update a vacation period.",
          {"id": _int("Vacation ID"), "start_date": _str("Start date"),
           "end_date": _str("End date")}, ["id"]),
    _tool("delete_vacation", "Delete a vacation period.",
          {"id": _int("Vacation ID")}, ["id"]),

    # ── Workflows ──────────────────────────────────────────────────────────────
    _tool("list_workflow_elements", "List all elements in a workflow.",
          {"workflow_id": _int("Workflow ID")}, ["workflow_id"]),
    _tool("get_workflow_element", "Get a specific workflow element.",
          {"workflow_id": _int("Workflow ID"), "element_id": _int("Element ID")},
          ["workflow_id", "element_id"]),
    _tool("create_workflow_element", "Create a new element in a workflow.",
          {"workflow_id": _int("Workflow ID"), "name": _str("Element name"),
           "type": _str("Element type")}, ["workflow_id"]),
    _tool("reorder_workflow_element", "Reorder a workflow element.",
          {"workflow_id": _int("Workflow ID"), "element_id": _int("Element ID"),
           "position": _int("New position")}, ["workflow_id", "element_id"]),
    _tool("delete_workflow_element", "Delete a workflow element.",
          {"workflow_id": _int("Workflow ID"), "element_id": _int("Element ID")},
          ["workflow_id", "element_id"]),
]


# ── Tool handler ──────────────────────────────────────────────────────────────

async def call_tool(client: RunrunClient, name: str, args: dict[str, Any]) -> Any:  # noqa: C901
    a = args
    match name:
        # Activities
        case "list_activities": return await client.list_activities(**a)
        # Boards
        case "list_boards": return await client.list_boards(**a)
        case "get_board": return await client.get_board(a["id"])
        # Board Stages
        case "list_board_stages": return await client.list_board_stages(a["board_id"], **{k: v for k, v in a.items() if k != "board_id"})
        case "get_board_stage": return await client.get_board_stage(a["board_id"], a["stage_id"])
        case "create_board_stage":
            bid = a.pop("board_id"); return await client.create_board_stage(bid, **a)
        case "update_board_stage":
            bid, sid = a.pop("board_id"), a.pop("stage_id"); return await client.update_board_stage(bid, sid, **a)
        case "delete_board_stage": return await client.delete_board_stage(a["board_id"], a["stage_id"])
        case "move_board_stage":
            return await client.move_board_stage(a["board_id"], a["stage_id"], **{k: v for k, v in a.items() if k not in ("board_id", "stage_id")})
        # Checklist Items
        case "list_checklist_items": return await client.list_checklist_items(a["checklist_id"])
        case "get_checklist_item": return await client.get_checklist_item(a["checklist_id"], a["item_id"])
        case "create_checklist_item":
            cid = a.pop("checklist_id"); return await client.create_checklist_item(cid, **a)
        case "update_checklist_item":
            cid, iid = a.pop("checklist_id"), a.pop("item_id"); return await client.update_checklist_item(cid, iid, **a)
        case "delete_checklist_item": return await client.delete_checklist_item(a["checklist_id"], a["item_id"])
        # Checklists
        case "get_task_checklist": return await client.get_task_checklist(a["task_id"])
        case "create_task_checklist":
            tid = a.pop("task_id"); return await client.create_task_checklist(tid, **a)
        case "update_task_checklist":
            tid = a.pop("task_id"); return await client.update_task_checklist(tid, **a)
        case "delete_task_checklist": return await client.delete_task_checklist(a["task_id"])
        case "get_template_checklist": return await client.get_template_checklist(a["template_id"])
        case "create_template_checklist":
            tid = a.pop("template_id"); return await client.create_template_checklist(tid, **a)
        case "update_template_checklist":
            tid = a.pop("template_id"); return await client.update_template_checklist(tid, **a)
        case "delete_template_checklist": return await client.delete_template_checklist(a["template_id"])
        # Clients
        case "list_clients":
            detail = a.pop("detail_level", "summary")
            a.setdefault("limit", 50)
            return _summarize_page(await client.list_clients(**a), _CLIENT_SUMMARY_FIELDS, detail)
        case "get_client": return await client.get_client(a["id"])
        case "create_client":
            a2 = dict(a); a2.pop("id", None); return await client.create_client(**a2)
        case "update_client":
            cid = a.pop("id"); return await client.update_client(cid, **a)
        case "list_client_budgets": return await client.list_client_budgets(a["client_id"])
        case "update_client_budget":
            cid = a.pop("client_id"); return await client.update_client_budget(cid, **a)
        # Comments
        case "list_task_comments": return await client.list_task_comments(a["task_id"])
        case "get_comment": return await client.get_comment(a["id"])
        case "create_comment":
            a2 = dict(a); return await client.create_comment(**a2)
        case "update_comment":
            cid = a.pop("id"); return await client.update_comment(cid, **a)
        case "delete_comment": return await client.delete_comment(a["id"])
        case "react_to_comment": return await client.react_to_comment(a["id"], a["reaction"])
        # Demanders
        case "list_demanders": return await client.list_demanders(a["user_id"])
        case "add_demander": return await client.add_demander(a["user_id"], a["demander_id"])
        case "replace_demanders": return await client.replace_demanders(a["user_id"], a["demander_ids"])
        case "delete_demander": return await client.delete_demander(a["user_id"], a["demander_id"])
        # Descendants
        case "list_descendants": return await client.list_descendants(a["task_id"])
        case "add_descendant": return await client.add_descendant(a["task_id"], a["descendant_id"])
        case "delete_descendant": return await client.delete_descendant(a["task_id"], a["descendant_id"])
        # Description
        case "get_descriptions": return await client.get_descriptions(**a)
        case "get_task_description": return await client.get_task_description(a["task_id"])
        case "update_task_description": return await client.update_task_description(a["task_id"], a["description"])
        # Documents
        case "list_task_documents": return await client.list_task_documents(a["task_id"])
        case "get_document": return await client.get_document(a["id"])
        case "delete_document": return await client.delete_document(a["id"])
        # Enterprises
        case "get_enterprise": return await client.get_enterprise()
        case "update_enterprise":
            return await client.update_enterprise(**a)
        # Estimates
        case "list_task_estimates": return await client.list_task_estimates(a["task_id"])
        case "create_task_estimate":
            tid = a.pop("task_id"); return await client.create_task_estimate(tid, **a)
        # Event Notifications
        case "list_event_notifications":
            uid = a.pop("user_id"); return await client.list_event_notifications(uid, **a)
        case "mark_notification_read": return await client.mark_notification_read(a["user_id"], a["notification_id"])
        case "mark_all_notifications_read": return await client.mark_all_notifications_read(a["user_id"])
        case "delete_notification": return await client.delete_notification(a["user_id"], a["notification_id"])
        case "delete_all_notifications": return await client.delete_all_notifications(a["user_id"])
        # Filters
        case "list_project_filters": return await client.list_project_filters()
        case "get_project_filter": return await client.get_project_filter(a["id"])
        case "delete_project_filter": return await client.delete_project_filter(a["id"])
        case "list_task_filters": return await client.list_task_filters()
        case "get_task_filter": return await client.get_task_filter(a["id"])
        case "delete_task_filter": return await client.delete_task_filter(a["id"])
        # Justifications
        case "create_justification": return await client.create_justification(**a)
        case "update_justification": return await client.update_justification(a["justification_id"], a["text"])
        # Manual Work Periods
        case "list_manual_work_periods": return await client.list_manual_work_periods(**a)
        case "get_manual_work_period": return await client.get_manual_work_period(a["id"])
        case "create_manual_work_period": return await client.create_manual_work_period(**a)
        case "update_manual_work_period":
            pid = a.pop("id"); return await client.update_manual_work_period(pid, **a)
        case "delete_manual_work_period": return await client.delete_manual_work_period(a["id"])
        # Off Days
        case "list_off_days": return await client.list_off_days(**a)
        case "get_off_day": return await client.get_off_day(a["id"])
        case "create_off_day": return await client.create_off_day(**a)
        case "update_off_day":
            oid = a.pop("id"); return await client.update_off_day(oid, **a)
        case "delete_off_day": return await client.delete_off_day(a["id"])
        # Partners
        case "list_partners": return await client.list_partners(a["user_id"])
        case "add_partner": return await client.add_partner(a["user_id"], a["partner_id"])
        case "replace_partners": return await client.replace_partners(a["user_id"], a["partner_ids"])
        case "delete_partner": return await client.delete_partner(a["user_id"], a["partner_id"])
        # Prerequisites
        case "list_prerequisites": return await client.list_prerequisites(a["task_id"])
        case "add_prerequisite": return await client.add_prerequisite(a["task_id"], a["prerequisite_id"])
        case "delete_prerequisite": return await client.delete_prerequisite(a["task_id"], a["prerequisite_id"])
        # Project Groups
        case "list_project_groups": return await client.list_project_groups()
        case "create_project_group": return await client.create_project_group(**a)
        case "update_project_group":
            gid = a.pop("id"); return await client.update_project_group(gid, **a)
        case "delete_project_group": return await client.delete_project_group(a["id"])
        # Project Sub Groups
        case "list_project_sub_groups": return await client.list_project_sub_groups(a["group_id"])
        case "create_project_sub_group":
            gid = a.pop("group_id"); return await client.create_project_sub_group(gid, **a)
        case "update_project_sub_group":
            gid, sid = a.pop("group_id"), a.pop("sub_group_id"); return await client.update_project_sub_group(gid, sid, **a)
        case "delete_project_sub_group": return await client.delete_project_sub_group(a["group_id"], a["sub_group_id"])
        case "move_project_sub_group":
            sid = a.pop("sub_group_id"); return await client.move_project_sub_group(sid, **a)
        # Project Templates
        case "list_project_templates": return await client.list_project_templates()
        case "get_project_template": return await client.get_project_template(a["id"])
        case "create_project_template":
            a2 = dict(a); a2.pop("id", None); return await client.create_project_template(**a2)
        case "update_project_template":
            tid = a.pop("id"); return await client.update_project_template(tid, **a)
        case "delete_project_template": return await client.delete_project_template(a["id"])
        # Projects
        case "list_projects":
            detail = a.pop("detail_level", "summary")
            a.setdefault("limit", 50)
            return _summarize_page(await client.list_projects(**a), _PROJECT_SUMMARY_FIELDS, detail)
        case "get_project": return await client.get_project(a["id"])
        case "create_project":
            a2 = dict(a); a2.pop("id", None); return await client.create_project(**a2)
        case "update_project":
            pid = a.pop("id"); return await client.update_project(pid, **a)
        case "get_project_related_users": return await client.get_project_related_users(a["id"])
        case "move_project":
            pid = a.pop("id"); return await client.move_project(pid, **a)
        case "clone_project":
            pid = a.pop("id"); return await client.clone_project(pid, **a)
        case "change_project_board_stage": return await client.change_project_board_stage(a["id"], a["board_stage_id"])
        # Project Extra Costs
        case "list_project_extra_costs": return await client.list_project_extra_costs(a["project_id"])
        case "get_project_extra_cost": return await client.get_project_extra_cost(a["project_id"], a["cost_id"])
        case "create_project_extra_cost":
            pid = a.pop("project_id"); return await client.create_project_extra_cost(pid, **a)
        # Reports
        case "get_time_worked_report": return await client.get_time_worked_report(**a)
        # Tags
        case "list_tags": return await client.list_tags()
        # Task Assignments
        case "delete_task_assignment": return await client.delete_task_assignment(a["task_id"], a["assignment_id"])
        case "play_task_assignment": return await client.play_task_assignment(a["task_id"], a["assignment_id"])
        case "pause_task_assignment": return await client.pause_task_assignment(a["task_id"], a["assignment_id"])
        case "deliver_task_assignment": return await client.deliver_task_assignment(a["task_id"], a["assignment_id"])
        case "reopen_task_assignment": return await client.reopen_task_assignment(a["task_id"], a["assignment_id"])
        # Task Evaluations
        case "list_task_evaluations": return await client.list_task_evaluations(**a)
        case "create_task_evaluation": return await client.create_task_evaluation(**a)
        # Task Types
        case "list_task_types": return await client.list_task_types()
        case "get_task_type": return await client.get_task_type(a["id"])
        case "create_task_type":
            a2 = dict(a); a2.pop("id", None); return await client.create_task_type(**a2)
        case "update_task_type":
            tid = a.pop("id"); return await client.update_task_type(tid, **a)
        # Tasks
        case "list_tasks":
            detail = a.pop("detail_level", "summary")
            a.setdefault("limit", 50)
            page = await client.list_tasks(**a)
            return _summarize_page(page, _TASK_SUMMARY_FIELDS, detail)
        case "list_my_tasks":
            detail = a.pop("detail_level", "summary")
            role = a.pop("role", "created")
            a.setdefault("limit", 50)
            me = await client.get_current_user()
            my_id = me.get("id") if isinstance(me, dict) else None
            if not my_id:
                raise ValueError("Could not resolve current user from /users/me")
            if role == "responsible":
                a["responsible_id"] = my_id
            else:
                a["user_id"] = my_id
            page = await client.list_tasks(**a)
            result = _summarize_page(page, _TASK_SUMMARY_FIELDS, detail)
            result["filtered_by"] = {"role": role, "user_id": my_id, "user_name": me.get("name")}
            return result
        case "get_task": return await client.get_task(a["id"])
        case "get_task_content":
            return await client.get_task_content(
                a["task_id"],
                include_description=a.get("include_description", True),
                include_comments=a.get("include_comments", False),
                html_format=a.get("html_format", "text"))
        case "export_tasks":
            filter_keys = ("user_id", "responsible_id", "project_id", "board_id", "team_id", "type_id", "is_closed")
            filters = {k: a[k] for k in filter_keys if k in a}
            return await client.export_tasks(
                filters=filters,
                include_description=a.get("include_description", True),
                include_comments=a.get("include_comments", False),
                html_format=a.get("html_format", "text"),
                flatten_custom_fields=a.get("flatten_custom_fields", False),
                max_tasks=a.get("max_tasks", 50))
        case "get_task_subtasks": return await client.get_task_subtasks(a["task_id"])
        case "create_task":
            a2 = dict(a); a2.pop("id", None)
            board_id = a2.pop("board_id", None)
            board_stage_id = a2.pop("board_stage_id", None)
            task = await client.create_task(**a2)
            if board_id and isinstance(task, dict) and task.get("id"):
                extra = {"board_stage_id": board_stage_id} if board_stage_id else {}
                await client.change_task_board(task["id"], board_id, **extra)
                task = await client.get_task(task["id"])
            return task
        case "update_task":
            tid = a.pop("id"); return await client.update_task(tid, **a)
        case "delete_task": return await client.delete_task(a["id"])
        case "play_task": return await client.play_task(a["id"])
        case "pause_task": return await client.pause_task(a["id"])
        case "deliver_task": return await client.deliver_task(a["id"])
        case "reopen_task": return await client.reopen_task(a["id"])
        case "change_task_board":
            tid = a.pop("id"); return await client.change_task_board(tid, a.pop("board_id"), **a)
        case "change_task_project": return await client.change_task_project(a["id"], a["project_id"])
        case "change_task_type": return await client.change_task_type(a["id"], a["type_id"])
        case "mark_task_urgent": return await client.mark_task_urgent(a["id"])
        case "unmark_task_urgent": return await client.unmark_task_urgent(a["id"])
        case "create_task_assignments":
            tid = a.pop("id"); return await client.create_task_assignments(tid, **a)
        case "move_task_to_top": return await client.move_task_to_top(a["id"])
        case "move_task_to_next_stage": return await client.move_task_to_next_stage(a["id"])
        case "clone_task":
            tid = a.pop("task_id"); return await client.clone_task(task_id=tid, **a)
        case "get_task_form_answers": return await client.get_task_form_answers(a["task_id"])
        case "get_task_fields": return await client.get_task_fields(a["task_id"])
        case "list_field_options": return await client.list_field_options(a["field_id"])
        case "set_task_fields": return await client.set_task_fields(a["task_id"], a["values"])
        case "list_task_attachments": return await client.list_task_attachments(a["task_id"])
        # Teams
        case "list_teams": return await client.list_teams()
        case "get_team": return await client.get_team(a["id"])
        case "create_team":
            a2 = dict(a); a2.pop("id", None); return await client.create_team(**a2)
        case "update_team":
            tid = a.pop("id"); return await client.update_team(tid, **a)
        case "delete_team": return await client.delete_team(a["id"])
        case "add_team_member": return await client.add_team_member(a["team_id"], a["user_id"])
        case "remove_team_member": return await client.remove_team_member(a["team_id"], a["user_id"])
        # Users
        case "list_users":
            detail = a.pop("detail_level", "summary")
            a.setdefault("limit", 50)
            return _summarize_page(await client.list_users(**a), _USER_SUMMARY_FIELDS, detail)
        case "get_user": return await client.get_user(a["id"])
        case "get_current_user": return await client.get_current_user()
        case "create_user": return await client.create_user(**a)
        case "update_user":
            uid = a.pop("id"); return await client.update_user(uid, **a)
        # Vacations
        case "list_vacations": return await client.list_vacations(**a)
        case "get_vacation": return await client.get_vacation(a["id"])
        case "create_vacation":
            uid = a.pop("user_id"); return await client.create_vacation(uid, **a)
        case "update_vacation":
            vid = a.pop("id"); return await client.update_vacation(vid, **a)
        case "delete_vacation": return await client.delete_vacation(a["id"])
        # Workflows
        case "list_workflow_elements": return await client.list_workflow_elements(a["workflow_id"])
        case "get_workflow_element": return await client.get_workflow_element(a["workflow_id"], a["element_id"])
        case "create_workflow_element":
            wid = a.pop("workflow_id"); return await client.create_workflow_element(wid, **a)
        case "reorder_workflow_element":
            return await client.reorder_workflow_element(a["workflow_id"], a["element_id"],
                                                          **{k: v for k, v in a.items() if k not in ("workflow_id", "element_id")})
        case "delete_workflow_element": return await client.delete_workflow_element(a["workflow_id"], a["element_id"])
        case _:
            raise ValueError(f"Unknown tool: {name}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app_key = os.environ.get("RUNRUN_APP_KEY", "")
    user_token = os.environ.get("RUNRUN_USER_TOKEN", "")

    if not app_key or not user_token:
        print("Error: RUNRUN_APP_KEY and RUNRUN_USER_TOKEN environment variables are required.",
              file=sys.stderr)
        sys.exit(1)

    async def run() -> None:
        client = RunrunClient(app_key, user_token)
        server = Server("mcp-runrun")

        @server.list_tools()
        async def list_tools() -> list[Tool]:
            return TOOLS

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            try:
                result = await call_tool(client, name, dict(arguments))
                return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
            except httpx.HTTPStatusError as e:
                return [TextContent(type="text", text=f"API error {e.response.status_code}: {e.response.text}")]
            except Exception as e:
                return [TextContent(type="text", text=f"Error: {e}")]

        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

        await client.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
