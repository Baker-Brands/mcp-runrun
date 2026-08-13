"""HTTP client for the Runrun.it REST API v1.0 — full endpoint coverage."""

import asyncio
from typing import Any

import httpx

from . import content as _content

BASE_URL = "https://secure.runrun.it/api/v1.0"


class RunrunClient:
    def __init__(self, app_key: str, user_token: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "App-Key": app_key,
                "User-Token": user_token,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        # field definitions are org-wide and identical across tasks → cache once
        self._field_label_map: dict[str, str] | None = None

    async def close(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                    json: Any = None, max_retries: int = 3) -> httpx.Response:
        """Single choke point for all HTTP calls, with 429 rate-limit backoff.

        Runrun.it allows 100 req/min and returns 429 with a Retry-After header
        when exceeded. We honor it (capped) and retry a few times.
        """
        clean = {k: v for k, v in (params or {}).items() if v is not None} if params else None
        attempt = 0
        while True:
            resp = await self._http.request(method, path, params=clean, json=json)
            if resp.status_code == 429 and attempt < max_retries:
                try:
                    wait = float(resp.headers.get("Retry-After", "2"))
                except ValueError:
                    wait = 2.0
                await asyncio.sleep(min(max(wait, 1.0), 30.0))
                attempt += 1
                continue
            resp.raise_for_status()
            return resp

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._send("GET", path, params=params)
        return resp.json()

    @staticmethod
    def _parse_range(header: str | None) -> dict[str, Any]:
        """Parse the X-Item-Range header (e.g. 'items 1-50/987') into pagination metadata."""
        meta: dict[str, Any] = {"total": None, "first": None, "last": None}
        if not header:
            return meta
        try:
            part = header.replace("items", "").strip()
            span, total = part.split("/")
            meta["total"] = int(total)
            if "-" in span:
                first, last = span.split("-")
                meta["first"] = int(first)
                meta["last"] = int(last)
        except (ValueError, AttributeError):
            pass
        return meta

    async def _get_page(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a list endpoint and return items plus pagination metadata from response headers."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        if "limit" in clean:  # API hard-caps page size at 100
            clean["limit"] = min(int(clean["limit"]), 100)
        resp = await self._send("GET", path, params=clean)
        items = resp.json()
        meta = self._parse_range(resp.headers.get("X-Item-Range"))
        limit = clean.get("limit", 100)
        page = clean.get("page", 1)
        has_more = meta["last"] is not None and meta["total"] is not None and meta["last"] < meta["total"]
        return {
            "items": items if isinstance(items, list) else [items],
            "total": meta["total"],
            "page": page,
            "limit": limit,
            "returned": len(items) if isinstance(items, list) else 1,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
        }

    async def _post(self, path: str, body: Any = None) -> Any:
        resp = await self._send("POST", path, json=body)
        if resp.status_code == 204:
            return {}
        return resp.json()

    async def _put(self, path: str, body: Any = None) -> Any:
        resp = await self._send("PUT", path, json=body)
        return resp.json()

    async def _delete(self, path: str) -> Any:
        await self._send("DELETE", path)
        return {}

    # ── Activities ─────────────────────────────────────────────────────────────
    async def list_activities(self, **params: Any) -> Any:
        return await self._get("/activities", params)

    # ── Boards ─────────────────────────────────────────────────────────────────
    async def list_boards(self, **params: Any) -> Any:
        return await self._get_page("/boards", params)

    async def get_board(self, board_id: int) -> Any:
        return await self._get(f"/boards/{board_id}")

    # ── Board Stages ───────────────────────────────────────────────────────────
    async def list_board_stages(self, board_id: int, **params: Any) -> Any:
        return await self._get(f"/boards/{board_id}/stages", params)

    async def get_board_stage(self, board_id: int, stage_id: int) -> Any:
        return await self._get(f"/boards/{board_id}/stages/{stage_id}")

    async def create_board_stage(self, board_id: int, **data: Any) -> Any:
        return await self._post(f"/boards/{board_id}/stages", {"board_stage": data})

    async def update_board_stage(self, board_id: int, stage_id: int, **data: Any) -> Any:
        return await self._put(f"/boards/{board_id}/stages/{stage_id}", {"board_stage": data})

    async def delete_board_stage(self, board_id: int, stage_id: int) -> Any:
        return await self._delete(f"/boards/{board_id}/stages/{stage_id}")

    async def move_board_stage(self, board_id: int, stage_id: int, **data: Any) -> Any:
        return await self._post(f"/boards/{board_id}/stages/{stage_id}/move", data)

    async def update_board_stage_latency(self, board_id: int, stage_id: int, use_latency_time: bool) -> Any:
        return await self._post(f"/boards/{board_id}/stages/{stage_id}/update_use_latency_time",
                                {"use_latency_time": use_latency_time})

    async def update_board_stage_scrum(self, board_id: int, stage_id: int, use_scrum_points: bool) -> Any:
        return await self._post(f"/boards/{board_id}/stages/{stage_id}/update_use_scrum_points",
                                {"use_scrum_points": use_scrum_points})

    # ── Checklist Items ────────────────────────────────────────────────────────
    async def list_checklist_items(self, checklist_id: int) -> Any:
        return await self._get(f"/checklists/{checklist_id}/items")

    async def get_checklist_item(self, checklist_id: int, item_id: int) -> Any:
        return await self._get(f"/checklists/{checklist_id}/items/{item_id}")

    async def create_checklist_item(self, checklist_id: int, **data: Any) -> Any:
        return await self._post(f"/checklists/{checklist_id}/items", {"checklist_item": data})

    async def update_checklist_item(self, checklist_id: int, item_id: int, **data: Any) -> Any:
        return await self._put(f"/checklists/{checklist_id}/items/{item_id}", {"checklist_item": data})

    async def delete_checklist_item(self, checklist_id: int, item_id: int) -> Any:
        return await self._delete(f"/checklists/{checklist_id}/items/{item_id}")

    # ── Checklists ─────────────────────────────────────────────────────────────
    async def get_task_checklist(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/checklist")

    async def create_task_checklist(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/checklist", {"checklist": data})

    async def update_task_checklist(self, task_id: int, **data: Any) -> Any:
        return await self._put(f"/tasks/{task_id}/checklist", {"checklist": data})

    async def delete_task_checklist(self, task_id: int) -> Any:
        return await self._delete(f"/tasks/{task_id}/checklist")

    async def get_template_checklist(self, template_id: int) -> Any:
        return await self._get(f"/task_templates/{template_id}/checklist")

    async def create_template_checklist(self, template_id: int, **data: Any) -> Any:
        return await self._post(f"/task_templates/{template_id}/checklist", {"checklist": data})

    async def update_template_checklist(self, template_id: int, **data: Any) -> Any:
        return await self._put(f"/task_templates/{template_id}/checklist", {"checklist": data})

    async def delete_template_checklist(self, template_id: int) -> Any:
        return await self._delete(f"/task_templates/{template_id}/checklist")

    # ── Clients ────────────────────────────────────────────────────────────────
    async def list_clients(self, **params: Any) -> Any:
        return await self._get_page("/clients", params)

    async def get_client(self, client_id: int) -> Any:
        return await self._get(f"/clients/{client_id}")

    async def create_client(self, **data: Any) -> Any:
        return await self._post("/clients", {"client": data})

    async def update_client(self, client_id: int, **data: Any) -> Any:
        return await self._put(f"/clients/{client_id}", {"client": data})

    async def list_client_budgets(self, client_id: int) -> Any:
        return await self._get(f"/clients/{client_id}/monthly_budgets")

    async def update_client_budget(self, client_id: int, **data: Any) -> Any:
        return await self._post(f"/clients/{client_id}/update_monthly_budget", data)

    # ── Comments ───────────────────────────────────────────────────────────────
    async def list_task_comments(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/comments")

    async def get_comment(self, comment_id: int) -> Any:
        return await self._get(f"/comments/{comment_id}")

    async def create_comment(self, **data: Any) -> Any:
        return await self._post("/comments", {"comment": data})

    async def update_comment(self, comment_id: int, **data: Any) -> Any:
        return await self._put(f"/comments/{comment_id}", {"comment": data})

    async def delete_comment(self, comment_id: int) -> Any:
        return await self._delete(f"/comments/{comment_id}")

    async def react_to_comment(self, comment_id: int, reaction: str) -> Any:
        return await self._post(f"/comments/{comment_id}/reaction", {"reaction": reaction})

    # ── Demanders ──────────────────────────────────────────────────────────────
    async def list_demanders(self, user_id: str) -> Any:
        return await self._get(f"/users/{user_id}/demanders")

    async def add_demander(self, user_id: str, demander_id: str) -> Any:
        return await self._post(f"/users/{user_id}/demanders", {"demander_id": demander_id})

    async def replace_demanders(self, user_id: str, demander_ids: list[str]) -> Any:
        return await self._post(f"/users/{user_id}/demanders/replace", {"demander_ids": demander_ids})

    async def delete_demander(self, user_id: str, demander_id: str) -> Any:
        return await self._delete(f"/users/{user_id}/demanders/{demander_id}")

    # ── Descendants ────────────────────────────────────────────────────────────
    async def list_descendants(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/descendants")

    async def add_descendant(self, task_id: int, descendant_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/descendants", {"descendant_id": descendant_id})

    async def delete_descendant(self, task_id: int, descendant_id: int) -> Any:
        return await self._delete(f"/tasks/{task_id}/descendants/{descendant_id}")

    # ── Description ────────────────────────────────────────────────────────────
    async def get_descriptions(self, **params: Any) -> Any:
        return await self._get("/descriptions", params)

    async def update_descriptions(self, **data: Any) -> Any:
        return await self._put("/descriptions", data)

    async def get_task_description(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/description")

    async def update_task_description(self, task_id: int, description: str) -> Any:
        return await self._put(f"/tasks/{task_id}/description", {"description": description})

    # ── Documents ──────────────────────────────────────────────────────────────
    async def list_task_documents(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/documents")

    async def get_document(self, document_id: int) -> Any:
        return await self._get(f"/documents/{document_id}")

    async def create_document(self, **data: Any) -> Any:
        return await self._post("/documents", {"document": data})

    async def mark_document_uploaded(self, document_id: int) -> Any:
        return await self._put(f"/documents/{document_id}", {"transferred": True})

    async def delete_document(self, document_id: int) -> Any:
        return await self._delete(f"/documents/{document_id}")

    # ── Enterprises ────────────────────────────────────────────────────────────
    async def get_enterprise(self) -> Any:
        return await self._get("/enterprises")

    async def update_enterprise(self, **data: Any) -> Any:
        return await self._put("/enterprises", {"enterprise": data})

    # ── Estimates ──────────────────────────────────────────────────────────────
    async def list_task_estimates(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/estimates")

    async def create_task_estimate(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/estimates", {"estimate": data})

    # ── Event Notifications ────────────────────────────────────────────────────
    async def list_event_notifications(self, user_id: str, **params: Any) -> Any:
        return await self._get(f"/users/{user_id}/event_notifications", params)

    async def mark_notification_read(self, user_id: str, notification_id: int) -> Any:
        return await self._put(f"/users/{user_id}/event_notifications/{notification_id}")

    async def mark_all_notifications_read(self, user_id: str) -> Any:
        return await self._put(f"/users/{user_id}/event_notifications/read_all")

    async def delete_notification(self, user_id: str, notification_id: int) -> Any:
        return await self._delete(f"/users/{user_id}/event_notifications/{notification_id}")

    async def delete_all_notifications(self, user_id: str) -> Any:
        return await self._post(f"/users/{user_id}/event_notifications/destroy_all")

    # ── Filters ────────────────────────────────────────────────────────────────
    async def list_project_filters(self) -> Any:
        return await self._get("/projects/filters")

    async def get_project_filter(self, filter_id: int) -> Any:
        return await self._get(f"/projects/filters/{filter_id}")

    async def delete_project_filter(self, filter_id: int) -> Any:
        return await self._delete(f"/projects/filters/{filter_id}")

    async def list_task_filters(self) -> Any:
        return await self._get("/tasks/filters")

    async def get_task_filter(self, filter_id: int) -> Any:
        return await self._get(f"/tasks/filters/{filter_id}")

    async def delete_task_filter(self, filter_id: int) -> Any:
        return await self._delete(f"/tasks/filters/{filter_id}")

    # ── Justifications ─────────────────────────────────────────────────────────
    async def create_justification(self, **data: Any) -> Any:
        return await self._post("/justifications", {"justification": data})

    async def update_justification(self, justification_id: int, text: str) -> Any:
        return await self._put(f"/justifications/{justification_id}", {"justification": {"text": text}})

    # ── Manual Work Periods ────────────────────────────────────────────────────
    async def list_manual_work_periods(self, **params: Any) -> Any:
        return await self._get("/manual_work_periods", params)

    async def get_manual_work_period(self, period_id: int) -> Any:
        return await self._get(f"/manual_work_periods/{period_id}")

    async def create_manual_work_period(self, **data: Any) -> Any:
        return await self._post("/manual_work_periods", {"manual_work_period": data})

    async def update_manual_work_period(self, period_id: int, **data: Any) -> Any:
        return await self._put(f"/manual_work_periods/{period_id}", {"manual_work_period": data})

    async def delete_manual_work_period(self, period_id: int) -> Any:
        return await self._delete(f"/manual_work_periods/{period_id}")

    # ── Off Days ───────────────────────────────────────────────────────────────
    async def list_off_days(self, **params: Any) -> Any:
        return await self._get("/off_days", params)

    async def get_off_day(self, off_day_id: int) -> Any:
        return await self._get(f"/off_days/{off_day_id}")

    async def create_off_day(self, **data: Any) -> Any:
        return await self._post("/off_days", {"off_day": data})

    async def update_off_day(self, off_day_id: int, **data: Any) -> Any:
        return await self._put(f"/off_days/{off_day_id}", {"off_day": data})

    async def delete_off_day(self, off_day_id: int) -> Any:
        return await self._delete(f"/off_days/{off_day_id}")

    # ── Partners ───────────────────────────────────────────────────────────────
    async def list_partners(self, user_id: str) -> Any:
        return await self._get(f"/users/{user_id}/partners")

    async def add_partner(self, user_id: str, partner_id: str) -> Any:
        return await self._post(f"/users/{user_id}/partners", {"partner_id": partner_id})

    async def replace_partners(self, user_id: str, partner_ids: list[str]) -> Any:
        return await self._post(f"/users/{user_id}/partners/replace", {"partner_ids": partner_ids})

    async def delete_partner(self, user_id: str, partner_id: str) -> Any:
        return await self._delete(f"/users/{user_id}/partners/{partner_id}")

    # ── Prerequisites ──────────────────────────────────────────────────────────
    async def list_prerequisites(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/prerequisites")

    async def add_prerequisite(self, task_id: int, prerequisite_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/prerequisites", {"prerequisite_id": prerequisite_id})

    async def delete_prerequisite(self, task_id: int, prerequisite_id: int) -> Any:
        return await self._delete(f"/tasks/{task_id}/prerequisites/{prerequisite_id}")

    # ── Project Groups ─────────────────────────────────────────────────────────
    async def list_project_groups(self) -> Any:
        return await self._get("/project_groups")

    async def create_project_group(self, **data: Any) -> Any:
        return await self._post("/project_groups", {"project_group": data})

    async def update_project_group(self, group_id: int, **data: Any) -> Any:
        return await self._put(f"/project_groups/{group_id}", {"project_group": data})

    async def delete_project_group(self, group_id: int) -> Any:
        return await self._delete(f"/project_groups/{group_id}")

    # ── Project Sub Groups ─────────────────────────────────────────────────────
    async def list_project_sub_groups(self, group_id: int) -> Any:
        return await self._get(f"/project_groups/{group_id}/project_sub_groups")

    async def create_project_sub_group(self, group_id: int, **data: Any) -> Any:
        return await self._post(f"/project_groups/{group_id}/project_sub_groups", {"project_sub_group": data})

    async def update_project_sub_group(self, group_id: int, sub_group_id: int, **data: Any) -> Any:
        return await self._put(f"/project_groups/{group_id}/project_sub_groups/{sub_group_id}",
                               {"project_sub_group": data})

    async def delete_project_sub_group(self, group_id: int, sub_group_id: int) -> Any:
        return await self._delete(f"/project_groups/{group_id}/project_sub_groups/{sub_group_id}")

    async def move_project_sub_group(self, sub_group_id: int, **data: Any) -> Any:
        return await self._post(f"/project_sub_groups/{sub_group_id}/move", data)

    # ── Project Templates ──────────────────────────────────────────────────────
    async def list_project_templates(self) -> Any:
        return await self._get("/project_templates")

    async def get_project_template(self, template_id: int) -> Any:
        return await self._get(f"/project_templates/{template_id}")

    async def create_project_template(self, **data: Any) -> Any:
        return await self._post("/project_templates/", {"project_template": data})

    async def update_project_template(self, template_id: int, **data: Any) -> Any:
        return await self._put(f"/project_templates/{template_id}", {"project_template": data})

    async def delete_project_template(self, template_id: int) -> Any:
        return await self._delete(f"/project_templates/{template_id}")

    # ── Projects ───────────────────────────────────────────────────────────────
    async def list_projects(self, **params: Any) -> Any:
        return await self._get_page("/projects", params)

    async def get_project(self, project_id: int) -> Any:
        return await self._get(f"/projects/{project_id}")

    async def create_project(self, **data: Any) -> Any:
        return await self._post("/projects/", {"project": data})

    async def update_project(self, project_id: int, **data: Any) -> Any:
        return await self._put(f"/projects/{project_id}", {"project": data})

    async def get_project_related_users(self, project_id: int) -> Any:
        return await self._get(f"/projects/{project_id}/related_users")

    async def move_project(self, project_id: int, **data: Any) -> Any:
        return await self._post(f"/projects/{project_id}/move", data)

    async def share_project(self, project_id: int, **data: Any) -> Any:
        return await self._post(f"/projects/{project_id}/share", data)

    async def unshare_project(self, project_id: int, **data: Any) -> Any:
        return await self._post(f"/projects/{project_id}/unshare", data)

    async def clone_project(self, project_id: int, **data: Any) -> Any:
        return await self._post(f"/projects/{project_id}/clone", data)

    async def change_project_board_stage(self, project_id: int, board_stage_id: int) -> Any:
        return await self._post(f"/projects/{project_id}/change_board_stage",
                                {"board_stage_id": board_stage_id})

    # ── Project Extra Costs ────────────────────────────────────────────────────
    async def list_project_extra_costs(self, project_id: int) -> Any:
        return await self._get(f"/projects/{project_id}/extra_costs")

    async def get_project_extra_cost(self, project_id: int, cost_id: int) -> Any:
        return await self._get(f"/projects/{project_id}/extra_costs/{cost_id}")

    async def create_project_extra_cost(self, project_id: int, **data: Any) -> Any:
        return await self._post(f"/projects/{project_id}/extra_costs", {"extra_cost": data})

    # ── Reports ────────────────────────────────────────────────────────────────
    async def get_time_worked_report(self, **params: Any) -> Any:
        return await self._get("/reports/time_worked", params)

    # ── Tags ───────────────────────────────────────────────────────────────────
    async def list_tags(self) -> Any:
        return await self._get("/tags")

    # ── Task Assignments ───────────────────────────────────────────────────────
    async def delete_task_assignment(self, task_id: int, assignment_id: int) -> Any:
        return await self._delete(f"/tasks/{task_id}/assignments/{assignment_id}")

    async def play_task_assignment(self, task_id: int, assignment_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/assignments/{assignment_id}/play")

    async def pause_task_assignment(self, task_id: int, assignment_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/assignments/{assignment_id}/pause")

    async def deliver_task_assignment(self, task_id: int, assignment_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/assignments/{assignment_id}/deliver")

    async def reopen_task_assignment(self, task_id: int, assignment_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/assignments/{assignment_id}/reopen")

    async def reposition_task_assignment(self, task_id: int, assignment_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/assignments/{assignment_id}/reposition", data)

    async def reestimate_task_assignment(self, task_id: int, assignment_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/assignments/{assignment_id}/reestimate", data)

    # ── Task Evaluations ───────────────────────────────────────────────────────
    async def list_task_evaluations(self, **params: Any) -> Any:
        return await self._get("/task_evaluations", params)

    async def create_task_evaluation(self, **data: Any) -> Any:
        return await self._post("/task_evaluations/", {"task_evaluation": data})

    # ── Task Types ─────────────────────────────────────────────────────────────
    async def list_task_types(self) -> Any:
        return await self._get("/task_types")

    async def get_task_type(self, type_id: int) -> Any:
        return await self._get(f"/task_types/{type_id}")

    async def create_task_type(self, **data: Any) -> Any:
        return await self._post("/task_types", {"task_type": data})

    async def update_task_type(self, type_id: int, **data: Any) -> Any:
        return await self._put(f"/task_types/{type_id}", {"task_type": data})

    # ── Tasks ──────────────────────────────────────────────────────────────────
    async def list_tasks(self, **params: Any) -> Any:
        return await self._get_page("/tasks", params)

    async def get_task(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}")

    async def get_task_subtasks(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/subtasks")

    async def create_task(self, **data: Any) -> Any:
        return await self._post("/tasks", {"task": data})

    async def update_task(self, task_id: int, **data: Any) -> Any:
        return await self._put(f"/tasks/{task_id}", {"task": data})

    async def delete_task(self, task_id: int) -> Any:
        return await self._delete(f"/tasks/{task_id}")

    async def play_task(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/play")

    async def pause_task(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/pause")

    async def deliver_task(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/deliver")

    async def reopen_task(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/reopen")

    async def change_task_board(self, task_id: int, board_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/change_board", {"board_id": board_id, **data})

    async def change_task_project(self, task_id: int, project_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/change_project", {"project_id": project_id})

    async def change_task_type(self, task_id: int, type_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/change_type", {"type_id": type_id})

    async def reposition_task(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/reposition", data)

    async def reestimate_task(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/reestimate", data)

    async def share_task(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/share", data)

    async def unshare_task(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/unshare", data)

    async def mark_task_urgent(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/mark_as_urgent")

    async def unmark_task_urgent(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/unmark_as_urgent")

    async def create_task_assignments(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/create_assignments", data)

    async def move_task_to_top(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/move_to_top")

    async def clone_task(self, **data: Any) -> Any:
        return await self._post("/tasks/clone", data)

    async def move_task_to_next_stage(self, task_id: int) -> Any:
        return await self._post(f"/tasks/{task_id}/move_to_next_stage")

    async def move_task(self, task_id: int, **data: Any) -> Any:
        return await self._post(f"/tasks/{task_id}/move", data)

    async def get_task_form_answers(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/form_answers")

    async def get_task_fields(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/fields")

    async def list_field_options(self, field_id: str) -> Any:
        return await self._get(f"/fields/{field_id}/options")

    async def set_task_fields(self, task_id: int, values: dict[str, Any]) -> Any:
        """Write custom-field values on a task.

        Keys may be field ids ("custom_30") or field labels ("Área").
        Values may be option ids, option labels, lists of either (for
        multiple_options fields), None to clear, or raw values for
        non-option field types. Labels are matched case-insensitively.
        """
        defs = await self._get(f"/tasks/{task_id}/fields")
        by_id: dict[str, dict[str, Any]] = {}
        for f in defs if isinstance(defs, list) else []:
            if f.get("category") == "custom":
                by_id[f["id"]] = f

        def _find_field(key: str) -> dict[str, Any]:
            if key in by_id:
                return by_id[key]
            # Labels are not unique org-wide (e.g. "PAÍS" and "País" coexist):
            # prefer an exact match, fall back to case-insensitive, and refuse
            # to guess when still ambiguous.
            exact = [f for f in by_id.values() if (f.get("label") or "").strip() == key.strip()]
            if len(exact) == 1:
                return exact[0]
            loose = [f for f in by_id.values()
                     if (f.get("label") or "").strip().casefold() == key.strip().casefold()]
            candidates = exact or loose
            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                ids = [f"{f['id']} ({f.get('label')})" for f in candidates]
                raise ValueError(f"Field label {key!r} is ambiguous on task {task_id}: {ids}. "
                                 "Use the field id instead.")
            known = sorted(f"{fid} ({f.get('label')})" for fid, f in by_id.items() if f.get("label"))
            raise ValueError(f"Unknown custom field {key!r} on task {task_id}. Available: {known}")

        def _match(field: dict[str, Any], options: list[dict[str, Any]], value: Any) -> dict[str, str]:
            wanted = str(value).strip()
            for o in options:
                if str(o.get("id")) == wanted:
                    return {"id": o["id"]}
            for o in options:
                if str(o.get("label") or "").strip().casefold() == wanted.casefold():
                    return {"id": o["id"]}
            labels = [o.get("label") for o in options]
            raise ValueError(f"Option {value!r} not found for field "
                             f"'{field.get('label') or field['id']}'. Available options: {labels}")

        payload: dict[str, Any] = {}
        for key, value in values.items():
            field = _find_field(str(key))
            fid, ftype = field["id"], field.get("field_type")
            if value is None:
                payload[fid] = None
            elif ftype in ("single_option", "multiple_options"):
                options = await self.list_field_options(fid)
                options = options if isinstance(options, list) else []
                if ftype == "single_option":
                    if isinstance(value, list):
                        raise ValueError(f"Field '{field.get('label') or fid}' is single_option; "
                                         "pass one value, not a list")
                    payload[fid] = _match(field, options, value)
                else:
                    items = value if isinstance(value, list) else [value]
                    payload[fid] = [_match(field, options, v) for v in items]
            else:
                payload[fid] = value

        updated = await self._put(f"/tasks/{task_id}", {"task": {"custom_fields": payload}})
        return {"task_id": task_id, "written_fields": sorted(payload),
                "custom_fields": updated.get("custom_fields") if isinstance(updated, dict) else None}

    async def list_task_attachments(self, task_id: int) -> Any:
        return await self._get(f"/tasks/{task_id}/documents")

    # ── Content extraction & standardized export ────────────────────────────────
    async def _field_label_map_cached(self, sample_task_id: int) -> dict[str, str]:
        """Fetch & cache the org-wide custom-field id→label map (uses any task).

        Only a non-empty map is memoized, so a transient/empty response does not
        permanently poison the cache.
        """
        if self._field_label_map:
            return self._field_label_map
        defs = await self._get(f"/tasks/{sample_task_id}/fields")
        label_map = _content.build_field_label_map(defs)
        if label_map:
            self._field_label_map = label_map
        return label_map

    async def _description_html(self, task_id: int) -> str | None:
        data = await self._get(f"/tasks/{task_id}/description")
        if isinstance(data, dict):
            return data.get("description") or data.get("text") or ""
        return None

    async def get_task_content(self, task_id: int, *, include_description: bool = True,
                               include_comments: bool = False,
                               html_format: str = "text") -> dict[str, Any]:
        """Return one standardized, spreadsheet-ready record for a single task."""
        task = await self._get(f"/tasks/{task_id}")
        label_map = await self._field_label_map_cached(task_id)
        desc = await self._description_html(task_id) if include_description else None
        comments = await self.list_task_comments(task_id) if include_comments else None
        return _content.task_record(task, label_map, description_html=desc,
                                    comments=comments, html_format=html_format)

    async def _collect_tasks(self, filters: dict[str, Any], max_tasks: int) -> tuple[list[dict[str, Any]], int | None]:
        """Page through /tasks until max_tasks rows are gathered or results run out."""
        collected: list[dict[str, Any]] = []
        total: int | None = None
        page_num = 1
        while len(collected) < max_tasks:
            params = dict(filters)
            params["page"] = page_num
            params["limit"] = min(max_tasks - len(collected), 100)
            page = await self._get_page("/tasks", params)
            total = page.get("total")
            items = page.get("items", [])
            collected.extend(items)
            if not page.get("has_more") or not items:
                break
            page_num += 1
        return collected[:max_tasks], total

    async def export_tasks(self, *, filters: dict[str, Any], include_description: bool = True,
                           include_comments: bool = False, html_format: str = "text",
                           max_tasks: int = 50, flatten_custom_fields: bool = False,
                           concurrency: int = 6) -> dict[str, Any]:
        """Filter tasks, extract standardized content for each, return rows + metadata.

        Pages through results up to max_tasks (so it can exceed the 100-per-page API
        ceiling). Descriptions/comments are fetched concurrently (bounded) with 429
        backoff. Per-task failures become an `error` field on that row instead of
        aborting the whole export.
        """
        tasks, total = await self._collect_tasks(filters, max_tasks)
        tasks = [t for t in tasks if isinstance(t, dict) and t.get("id") is not None]

        if not tasks:
            return {"pagination": {"total": total, "exported": 0, "capped": False, "capped_by": None,
                                   "errors": 0, "note": "No tasks matched the given filters."},
                    "field_label_map": {}, "columns": [], "rows": []}

        label_map = await self._field_label_map_cached(tasks[0]["id"])
        sem = asyncio.Semaphore(concurrency)

        async def enrich(task: dict[str, Any]) -> dict[str, Any]:
            tid = task["id"]
            desc = None
            comments = None
            errors: list[str] = []
            async with sem:
                if include_description:
                    try:
                        desc = await self._description_html(tid)
                    except httpx.HTTPStatusError as e:
                        errors.append(f"description: {e.response.status_code}")
                if include_comments:
                    try:
                        comments = await self.list_task_comments(tid)
                    except httpx.HTTPStatusError as e:
                        comments = []
                        errors.append(f"comments: {e.response.status_code}")
            rec = _content.task_record(task, label_map, description_html=desc,
                                       comments=comments, html_format=html_format)
            if errors:
                rec["error"] = "; ".join(errors)
            return rec

        results = await asyncio.gather(*(enrich(t) for t in tasks), return_exceptions=True)
        rows = [r for r in results if isinstance(r, dict)]
        failed = len(results) - len(rows)
        error_rows = sum(1 for r in rows if r.get("error"))

        capped = total is not None and total > len(rows)
        capped_by = None
        if capped:
            capped_by = "max_tasks" if len(rows) >= max_tasks else "fetch_errors"

        result: dict[str, Any] = {
            "pagination": {
                "total": total,
                "exported": len(rows),
                "capped": capped,
                "capped_by": capped_by,
                "max_tasks": max_tasks,
                "errors": failed + error_rows,
                "note": (f"{total} tasks match; exported {len(rows)}. Raise max_tasks to get more."
                         if capped_by == "max_tasks" else None),
            },
            "field_label_map": label_map,
        }

        if flatten_custom_fields:
            cf_columns = list(dict.fromkeys(label_map.values()))  # deterministic order
            flat_rows = [_content.flatten_record(r, cf_columns) for r in rows]
            base_cols = [k for k in (flat_rows[0].keys() if flat_rows else []) if not k.startswith("cf: ")]
            result["columns"] = base_cols + [f"cf: {c}" for c in cf_columns]
            result["rows"] = flat_rows
        else:
            result["columns"] = []
            result["rows"] = rows
        return result

    # ── Teams ──────────────────────────────────────────────────────────────────
    async def list_teams(self) -> Any:
        return await self._get("/teams")

    async def get_team(self, team_id: int) -> Any:
        return await self._get(f"/teams/{team_id}")

    async def create_team(self, **data: Any) -> Any:
        return await self._post("/teams", {"team": data})

    async def update_team(self, team_id: int, **data: Any) -> Any:
        return await self._put(f"/teams/{team_id}", {"team": data})

    async def delete_team(self, team_id: int) -> Any:
        return await self._delete(f"/teams/{team_id}")

    async def add_team_member(self, team_id: int, user_id: str) -> Any:
        return await self._post(f"/teams/{team_id}/add_member", {"user_id": user_id})

    async def remove_team_member(self, team_id: int, user_id: str) -> Any:
        return await self._post(f"/teams/{team_id}/remove_member", {"user_id": user_id})

    # ── Users ──────────────────────────────────────────────────────────────────
    async def list_users(self, **params: Any) -> Any:
        return await self._get_page("/users", params)

    async def get_user(self, user_id: str) -> Any:
        return await self._get(f"/users/{user_id}")

    async def get_current_user(self) -> Any:
        return await self._get("/users/me")

    async def create_user(self, **data: Any) -> Any:
        return await self._post("/users", {"user": data})

    async def update_user(self, user_id: str, **data: Any) -> Any:
        return await self._put(f"/users/{user_id}", {"user": data})

    # ── User Vacations ─────────────────────────────────────────────────────────
    async def list_vacations(self, **params: Any) -> Any:
        return await self._get("/users_vacations", params)

    async def get_vacation(self, vacation_id: int) -> Any:
        return await self._get(f"/users_vacations/{vacation_id}")

    async def create_vacation(self, user_id: str, **data: Any) -> Any:
        return await self._post(f"/users/{user_id}/vacations", {"vacation": data})

    async def update_vacation(self, vacation_id: int, **data: Any) -> Any:
        return await self._put(f"/users_vacations/{vacation_id}", {"vacation": data})

    async def delete_vacation(self, vacation_id: int) -> Any:
        return await self._delete(f"/users_vacations/{vacation_id}")

    # ── Workflows ──────────────────────────────────────────────────────────────
    async def list_workflow_elements(self, workflow_id: int) -> Any:
        return await self._get(f"/workflows/{workflow_id}/workflow_elements")

    async def get_workflow_element(self, workflow_id: int, element_id: int) -> Any:
        return await self._get(f"/workflows/{workflow_id}/workflow_elements/{element_id}")

    async def create_workflow_element(self, workflow_id: int, **data: Any) -> Any:
        return await self._post(f"/workflows/{workflow_id}/workflow_elements", {"workflow_element": data})

    async def reorder_workflow_element(self, workflow_id: int, element_id: int, **data: Any) -> Any:
        return await self._post(f"/workflows/{workflow_id}/workflow_elements/{element_id}/reorder", data)

    async def delete_workflow_element(self, workflow_id: int, element_id: int) -> Any:
        return await self._delete(f"/workflows/{workflow_id}/workflow_elements/{element_id}")
