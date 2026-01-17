# repos.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from db import SupabaseClient, SupabaseError
from models import (
    COL_DOCTORS,
    COL_USERS,
    COL_CASES,
    COL_EVALUATION_ARTIFACTS,
    COL_MESSAGES,
    COL_SESSIONS,
    COL_SUMMARIES,
    COL_USER_CASE_PROGRESS,
    normalize_case_problemset_fields,
    utcnow,
)

_USER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "docs-ai-users")


def _normalize_user_id(raw: str) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError):
        return str(uuid.uuid5(_USER_NAMESPACE, str(raw)))


def normalize_user_id(raw: str) -> str:
    """
    Public wrapper for stable user_id normalization used across repos/API.
    """
    return _normalize_user_id(raw)


def _merge_graph_state(existing: Any, updates: Dict[str, Any]) -> Dict[str, Any]:
    state = dict(existing or {}) if isinstance(existing, dict) else {}
    state.update(updates)
    return state


def _session_row_to_doc(row: Dict[str, Any]) -> Dict[str, Any]:
    graph_state = row.get("graph_state") if isinstance(row, dict) else None
    if not isinstance(graph_state, dict):
        graph_state = {}

    visit_number = int(row.get("visit_number") or graph_state.get("visit_no") or 1)
    turn_no = graph_state.get("turn_no")
    if turn_no is None:
        turn_in_visit = row.get("turn_in_visit")
        turn_no = int(turn_in_visit or 0) * 2

    return {
        "session_id": row.get("session_id"),
        "doctor_id": graph_state.get("doctor_id") or row.get("user_id"),
        "case_id": row.get("case_id"),
        "visit_no": visit_number,
        "turn_no": int(turn_no or 0),
        "status": row.get("status", "active"),
        "is_public": bool(row.get("is_public", False)),
        "ended_at": row.get("ended_at"),
        "disclosed_fact_ids": list(graph_state.get("disclosed_fact_ids", [])),
        "performed_exams": list(graph_state.get("performed_exams", [])),
        "performed_tests": list(graph_state.get("performed_tests", [])),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _message_row_to_doc(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": row.get("session_id"),
        "visit_no": int(row.get("visit_number") or 1),
        "turn_no": int(row.get("turn_index") or 0),
        "role": row.get("role"),
        "content": row.get("content"),
        "ts": row.get("created_at"),
        "meta": row.get("meta") or {},
    }


@dataclass
class DoctorRepo:
    db: SupabaseClient

    def get_or_create(self, doctor_id: str, level: int = 0) -> Dict[str, Any]:
        now = utcnow().isoformat()
        normalized = _normalize_user_id(doctor_id)
        fallback = {"doctor_id": normalized, "display_name": "", "level": int(level), "created_at": now, "updated_at": now}

        try:
            rows = self.db.table(COL_DOCTORS).select(filters={"doctor_id": normalized}, limit=1)
        except SupabaseError:
            return fallback

        if rows and isinstance(rows[0], dict):
            row = rows[0]
            return {
                "doctor_id": str(row.get("doctor_id") or normalized),
                "display_name": str(row.get("display_name") or ""),
                "level": int(row.get("level") or 0),
                "created_at": row.get("created_at") or now,
                "updated_at": row.get("updated_at") or now,
            }

        try:
            inserted = self.db.table(COL_DOCTORS).insert(
                {"doctor_id": normalized, "display_name": "", "level": int(level), "created_at": now, "updated_at": now},
                returning=True,
            )
            if inserted and isinstance(inserted[0], dict):
                row = inserted[0]
                return {
                    "doctor_id": str(row.get("doctor_id") or normalized),
                    "display_name": str(row.get("display_name") or ""),
                    "level": int(row.get("level") or 0),
                    "created_at": row.get("created_at") or now,
                    "updated_at": row.get("updated_at") or now,
                }
        except SupabaseError:
            return fallback

        return fallback

    def upsert_display_name(self, doctor_id: str, display_name: str) -> Dict[str, Any]:
        now = utcnow().isoformat()
        normalized = _normalize_user_id(doctor_id)
        safe_name = (display_name or "").strip()
        safe_name = safe_name[:80]
        fallback = {"doctor_id": normalized, "display_name": safe_name, "level": 0, "created_at": now, "updated_at": now}

        if not safe_name:
            return self.get_or_create(normalized, level=0)

        try:
            rows = self.db.table(COL_DOCTORS).select(filters={"doctor_id": normalized}, limit=1)
        except SupabaseError:
            return fallback

        if rows and isinstance(rows[0], dict):
            row = rows[0]
            current = str(row.get("display_name") or "").strip()
            if current == safe_name:
                return {
                    "doctor_id": str(row.get("doctor_id") or normalized),
                    "display_name": current,
                    "level": int(row.get("level") or 0),
                    "created_at": row.get("created_at") or now,
                    "updated_at": row.get("updated_at") or now,
                }
            try:
                self.db.table(COL_DOCTORS).update(
                    {"display_name": safe_name, "updated_at": now},
                    filters={"doctor_id": normalized},
                    returning=False,
                )
            except SupabaseError:
                pass
            row["display_name"] = safe_name
            row["updated_at"] = now
            return {
                "doctor_id": str(row.get("doctor_id") or normalized),
                "display_name": safe_name,
                "level": int(row.get("level") or 0),
                "created_at": row.get("created_at") or now,
                "updated_at": row.get("updated_at") or now,
            }

        try:
            inserted = self.db.table(COL_DOCTORS).insert(
                {"doctor_id": normalized, "display_name": safe_name, "level": 0, "created_at": now, "updated_at": now},
                returning=True,
            )
            if inserted and isinstance(inserted[0], dict):
                row = inserted[0]
                return {
                    "doctor_id": str(row.get("doctor_id") or normalized),
                    "display_name": str(row.get("display_name") or safe_name),
                    "level": int(row.get("level") or 0),
                    "created_at": row.get("created_at") or now,
                    "updated_at": row.get("updated_at") or now,
                }
        except SupabaseError:
            return fallback

        return fallback

    def get_display_names(self, doctor_ids: List[str]) -> Dict[str, str]:
        ids = [(_normalize_user_id(val) if val else "") for val in doctor_ids]
        ids = [val for val in ids if val]
        if not ids:
            return {}
        unique = sorted(set(ids))
        try:
            rows = self.db.table(COL_DOCTORS).select(
                filters={"doctor_id": ("in", unique)},
                columns="doctor_id,display_name",
                limit=len(unique),
            )
        except SupabaseError:
            return {}
        out: Dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            did = str(row.get("doctor_id") or "").strip()
            name = str(row.get("display_name") or "").strip()
            if did and name:
                out[did] = name
        return out


@dataclass
class UserRepo:
    db: SupabaseClient

    def get_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        normalized = _normalize_user_id(user_id)
        try:
            rows = self.db.table(COL_USERS).select(filters={"user_id": normalized}, limit=1)
        except SupabaseError:
            return None
        if not rows or not isinstance(rows[0], dict):
            return None
        return rows[0]

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        handle = (username or "").strip()
        if not handle:
            return None
        try:
            rows = self.db.table(COL_USERS).select(filters={"username": handle}, limit=1)
        except SupabaseError:
            return None
        if not rows or not isinstance(rows[0], dict):
            return None
        return rows[0]

    def get_usernames(self, user_ids: List[str]) -> Dict[str, str]:
        ids = [(_normalize_user_id(val) if val else "") for val in user_ids]
        ids = [val for val in ids if val]
        if not ids:
            return {}
        unique = sorted(set(ids))
        try:
            rows = self.db.table(COL_USERS).select(
                filters={"user_id": ("in", unique)},
                columns="user_id,username",
                limit=len(unique),
            )
        except SupabaseError:
            return {}
        out: Dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("user_id") or "").strip()
            uname = str(row.get("username") or "").strip()
            if uid and uname:
                out[uid] = uname
        return out

    def ensure_user(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str = "",
        avatar_url: str = "",
        bio: str = "",
    ) -> Optional[Dict[str, Any]]:
        normalized = _normalize_user_id(user_id)
        desired = (username or "").strip()
        if not desired:
            desired = f"u-{normalized}"

        existing = self.get_by_user_id(normalized)
        if existing:
            return existing

        now = utcnow().isoformat()
        base = desired
        for attempt in range(1, 11):
            candidate = base if attempt == 1 else f"{base}-{attempt}"
            try:
                inserted = self.db.table(COL_USERS).insert(
                    {
                        "user_id": normalized,
                        "username": candidate,
                        "display_name": (display_name or "").strip()[:80],
                        "avatar_url": (avatar_url or "").strip(),
                        "bio": (bio or "").strip()[:280],
                        "created_at": now,
                    },
                    returning=True,
                )
                if inserted and isinstance(inserted, list) and inserted and isinstance(inserted[0], dict):
                    return inserted[0]
            except SupabaseError:
                continue

        return self.get_by_user_id(normalized)


@dataclass
class CaseRepo:
    db: SupabaseClient

    def get(self, case_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_CASES).select(filters={"case_id": case_id}, limit=1)
        if not rows:
            return None
        row = rows[0]
        seed = row.get("seed") if isinstance(row, dict) else None
        if isinstance(seed, dict):
            return seed
        return {
            "case_id": row.get("case_id"),
            "title": row.get("title", ""),
            "chunks": [],
        }

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.db.table(COL_CASES).select(limit=limit)
        out: List[Dict[str, Any]] = []
        for row in rows:
            seed = row.get("seed") if isinstance(row, dict) else None
            out.append(seed if isinstance(seed, dict) else row)
        return out

    def list_cases(
        self,
        *,
        search: Optional[str] = None,
        difficulty: Optional[str] = None,
        tag: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        published_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Problemset listing endpoint backing store: returns only metadata fields,
        not the full case JSON.
        """
        page = max(1, int(page))
        limit = max(1, min(100, int(limit)))
        offset = (page - 1) * limit

        search = (search or "").strip() or None
        difficulty = (difficulty or "").strip() or None
        tag = (tag or "").strip() or None
        sort = (sort or "").strip() or None

        if sort == "difficulty":
            sort = "difficulty_asc"

        # Prefer database full-text search (ranked) if available via RPC.
        if search and sort not in {"difficulty_asc", "difficulty_desc"}:
            try:
                rows = self.db.rpc(
                    "problemset_search_cases",
                    {
                        "p_search": search,
                        "p_difficulty": difficulty,
                        "p_tag": tag,
                        "p_page": page,
                        "p_limit": limit,
                    },
                )
                total = 0
                if rows and isinstance(rows[0], dict):
                    try:
                        total = int(rows[0].get("total") or 0)
                    except (TypeError, ValueError):
                        total = 0
                items = [
                    {
                        "case_id": row.get("case_id"),
                        "title": row.get("title", ""),
                        "difficulty": row.get("difficulty", "Easy"),
                        "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
                        "short_prompt": row.get("short_prompt", ""),
                        "estimated_time_min": int(row.get("estimated_time_min") or 15),
                        "version": int(row.get("version") or 1),
                    }
                    for row in rows
                    if isinstance(row, dict)
                ]
                return {"items": items, "page": page, "limit": limit, "total": total}
            except SupabaseError:
                pass

        # Server-side query (preferred): uses dedicated columns.
        filters: Dict[str, Any] = {}
        if published_only:
            filters["is_published"] = True
        if difficulty:
            filters["difficulty"] = difficulty
        if tag:
            # PostgREST array contains operator: cs.{value}
            filters["tags"] = ("cs", "{" + tag + "}")

        raw_params: Dict[str, str] = {}
        if search:
            # PostgREST `or` parameter for ilike search across columns.
            safe = "".join(ch if ch.isalnum() or ch in {" ", "-", "_", "'"} else " " for ch in search)
            safe = " ".join(safe.split())
            if safe:
                raw_params["or"] = f"(title.ilike.*{safe}*,short_prompt.ilike.*{safe}*)"

        columns = "case_id,title,difficulty,tags,short_prompt,estimated_time_min,version"
        try:
            def _serialize_problemset_row(row: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "case_id": row.get("case_id"),
                    "title": row.get("title", ""),
                    "difficulty": row.get("difficulty", "Easy"),
                    "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
                    "short_prompt": row.get("short_prompt", ""),
                    "estimated_time_min": int(row.get("estimated_time_min") or 15),
                    "version": int(row.get("version") or 1),
                }

            if sort in {"difficulty_asc", "difficulty_desc"}:
                difficulty_groups = ["Easy", "Medium", "Hard"]
                if sort == "difficulty_desc":
                    difficulty_groups = list(reversed(difficulty_groups))
                if difficulty:
                    difficulty_groups = [difficulty]

                counts: List[int] = []
                for diff in difficulty_groups:
                    group_filters = dict(filters)
                    group_filters["difficulty"] = diff
                    _, group_total = self.db.table(COL_CASES).select_with_count(
                        filters=group_filters,
                        columns=columns,
                        limit=1,
                        offset=0,
                        order=("title", "asc"),
                        raw_params=raw_params or None,
                    )
                    counts.append(int(group_total))

                total = int(sum(counts))
                global_offset = offset
                items: List[Dict[str, Any]] = []
                remaining = limit

                for diff, group_total in zip(difficulty_groups, counts):
                    if remaining <= 0:
                        break
                    if global_offset >= group_total:
                        global_offset -= group_total
                        continue

                    group_filters = dict(filters)
                    group_filters["difficulty"] = diff
                    take = min(remaining, group_total - global_offset)
                    rows, _ = self.db.table(COL_CASES).select_with_count(
                        filters=group_filters,
                        columns=columns,
                        limit=take,
                        offset=global_offset,
                        order=("title", "asc"),
                        raw_params=raw_params or None,
                    )
                    for row in rows:
                        if isinstance(row, dict):
                            items.append(_serialize_problemset_row(row))
                    remaining = limit - len(items)
                    global_offset = 0

                return {"items": items, "page": page, "limit": limit, "total": total}

            rows, total = self.db.table(COL_CASES).select_with_count(
                filters=filters,
                columns=columns,
                limit=limit,
                offset=offset,
                order=("title", "asc"),
                raw_params=raw_params or None,
            )
            items: List[Dict[str, Any]] = []
            for row in rows:
                if isinstance(row, dict):
                    items.append(_serialize_problemset_row(row))
            return {"items": items, "page": page, "limit": limit, "total": int(total)}
        except SupabaseError:
            # Backwards compatibility: older schemas only have case_id/title/seed.
            rows = self.db.table(COL_CASES).select(columns="case_id,title,seed")

        # Client-side fallback: filter on normalized seed content.
        items_all: List[Dict[str, Any]] = []
        query = (search or "").lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            seed = row.get("seed")
            seed_dict = seed if isinstance(seed, dict) else {}
            merged = dict(seed_dict)
            if "case_id" not in merged and row.get("case_id"):
                merged["case_id"] = row.get("case_id")
            if (not merged.get("title")) and row.get("title"):
                merged["title"] = row.get("title")
            normalized = normalize_case_problemset_fields(merged, default_tags=[])

            if published_only and not bool(normalized.get("is_published", True)):
                continue
            if difficulty and normalized.get("difficulty") != difficulty:
                continue
            if tag and tag not in (normalized.get("tags") or []):
                continue
            if query:
                title_val = str(normalized.get("title") or "").lower()
                prompt_val = str(normalized.get("short_prompt") or "").lower()
                if query not in title_val and query not in prompt_val:
                    continue

            items_all.append(
                {
                    "case_id": normalized.get("case_id"),
                    "title": normalized.get("title", ""),
                    "difficulty": normalized.get("difficulty", "Easy"),
                    "tags": list(normalized.get("tags") or []),
                    "short_prompt": normalized.get("short_prompt", ""),
                    "estimated_time_min": int(normalized.get("estimated_time_min") or 15),
                    "version": int(normalized.get("version") or 1),
                }
            )

        difficulty_rank = {"Easy": 0, "Medium": 1, "Hard": 2}
        if sort in {"difficulty_asc", "difficulty_desc"}:
            if sort == "difficulty_desc":
                items_all.sort(
                    key=lambda item: (
                        -difficulty_rank.get(str(item.get("difficulty") or "Easy"), 0),
                        str(item.get("title") or "").lower(),
                    )
                )
            else:
                items_all.sort(
                    key=lambda item: (
                        difficulty_rank.get(str(item.get("difficulty") or "Easy"), 0),
                        str(item.get("title") or "").lower(),
                    )
                )
        else:
            items_all.sort(key=lambda item: str(item.get("title") or "").lower())
        total = len(items_all)
        items_page = items_all[offset : offset + limit]
        return {"items": items_page, "page": page, "limit": limit, "total": total}


@dataclass
class SessionRepo:
    db: SupabaseClient

    def find_latest_for_case(self, doctor_id: str, case_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(
            filters={"user_id": _normalize_user_id(doctor_id), "case_id": case_id},
            order="updated_at.desc",
            limit=1,
        )
        if not rows:
            return None
        if not isinstance(rows[0], dict):
            return None
        return _session_row_to_doc(rows[0])

    def find_latest_completed_for_case(self, doctor_id: str, case_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(
            filters={
                "user_id": _normalize_user_id(doctor_id),
                "case_id": case_id,
                "status": ("in", ["closed", "CLOSED", "completed", "COMPLETED"]),
            },
            order="ended_at.desc.nullslast,updated_at.desc",
            limit=1,
        )
        if not rows:
            return None
        if not isinstance(rows[0], dict):
            return None
        return _session_row_to_doc(rows[0])

    def create(self, doctor_id: str, case_id: str) -> Dict[str, Any]:
        session_id = str(uuid.uuid4())
        now = utcnow().isoformat()
        graph_state = {
            "doctor_id": doctor_id,
            "disclosed_fact_ids": [],
            "performed_exams": [],
            "performed_tests": [],
            "turn_no": 0,
        }
        row = {
            "session_id": session_id,
            "user_id": _normalize_user_id(doctor_id),
            "case_id": case_id,
            "status": "active",
            "visit_number": 1,
            "turn_in_visit": 0,
            "graph_state": graph_state,
            "updated_at": now,
        }
        inserted = self.db.table(COL_SESSIONS).insert(row)
        if inserted:
            return _session_row_to_doc(inserted[0])
        return _session_row_to_doc(row)

    def get_row(self, session_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(filters={"session_id": session_id}, limit=1)
        if not rows:
            return None
        row = rows[0]
        return row if isinstance(row, dict) else None

    def get_row_for_user(self, session_id: str, doctor_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(
            filters={"session_id": session_id, "user_id": _normalize_user_id(doctor_id)},
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        return row if isinstance(row, dict) else None

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(filters={"session_id": session_id}, limit=1)
        if not rows:
            return None
        return _session_row_to_doc(rows[0])

    def get_for_user(self, session_id: str, doctor_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(
            filters={"session_id": session_id, "user_id": _normalize_user_id(doctor_id)},
            limit=1,
        )
        if not rows:
            return None
        return _session_row_to_doc(rows[0])

    def find_active(self, doctor_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(
            filters={"user_id": _normalize_user_id(doctor_id), "status": "active"},
            order=("updated_at", "desc"),
            limit=1,
        )
        if not rows:
            return None
        return _session_row_to_doc(rows[0])

    def list_for_user(
        self,
        doctor_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        filters: Dict[str, Any] = {"user_id": _normalize_user_id(doctor_id)}
        if status:
            filters["status"] = status
        rows = self.db.table(COL_SESSIONS).select(
            filters=filters,
            order=("updated_at", "desc"),
            limit=limit,
        )
        return [_session_row_to_doc(row) for row in rows]

    def bump_turn(self, session_id: str, visit_no: int, turn_no: int) -> None:
        rows = self.db.table(COL_SESSIONS).select(filters={"session_id": session_id}, limit=1)
        if not rows:
            return
        row = rows[0]
        graph_state = _merge_graph_state(row.get("graph_state"), {"turn_no": int(turn_no)})
        self.db.table(COL_SESSIONS).update(
            {
                "visit_number": int(visit_no),
                "turn_in_visit": max(0, int(turn_no) // 2),
                "graph_state": graph_state,
                "updated_at": utcnow().isoformat(),
            },
            filters={"session_id": session_id},
        )

    def update_ledger(
        self,
        session_id: str,
        disclosed_fact_ids: List[str],
        performed_exams: List[str],
        performed_tests: List[str],
    ) -> None:
        rows = self.db.table(COL_SESSIONS).select(filters={"session_id": session_id}, limit=1)
        if not rows:
            return
        row = rows[0]
        graph_state = _merge_graph_state(
            row.get("graph_state"),
            {
                "disclosed_fact_ids": list(disclosed_fact_ids),
                "performed_exams": list(performed_exams),
                "performed_tests": list(performed_tests),
            },
        )
        self.db.table(COL_SESSIONS).update(
            {"graph_state": graph_state, "updated_at": utcnow().isoformat()},
            filters={"session_id": session_id},
        )

    def end_visit(self, session_id: str, new_visit_no: int, reset_turn_no: bool = True) -> None:
        rows = self.db.table(COL_SESSIONS).select(filters={"session_id": session_id}, limit=1)
        if not rows:
            return
        row = rows[0]
        graph_state_updates: Dict[str, Any] = {}
        if reset_turn_no:
            graph_state_updates["turn_no"] = 0
        graph_state = _merge_graph_state(row.get("graph_state"), graph_state_updates)
        update: Dict[str, Any] = {
            "visit_number": int(new_visit_no),
            "graph_state": graph_state,
            "updated_at": utcnow().isoformat(),
        }
        if reset_turn_no:
            update["turn_in_visit"] = 0
        self.db.table(COL_SESSIONS).update(update, filters={"session_id": session_id})

    def close_for_user(self, session_id: str, doctor_id: str, *, make_public: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        row = self.get_row_for_user(session_id, doctor_id)
        if not row:
            return None
        now = utcnow().isoformat()
        existing_status = str(row.get("status") or "active")
        update: Dict[str, Any] = {"updated_at": now}
        if existing_status.lower() not in {"closed", "completed"}:
            update["status"] = "COMPLETED"
            update["ended_at"] = now
        if make_public is not None:
            update["is_public"] = bool(make_public)
        updated = self.db.table(COL_SESSIONS).update(update, filters={"session_id": session_id}, returning=True)
        if updated and isinstance(updated, list):
            return _session_row_to_doc(updated[0])
        refreshed = self.get(session_id)
        return refreshed

    def set_visibility_for_user(self, session_id: str, doctor_id: str, *, is_public: bool) -> Optional[Dict[str, Any]]:
        row = self.get_row_for_user(session_id, doctor_id)
        if not row:
            return None
        if str(row.get("status") or "active").lower() not in {"closed", "completed"}:
            return None
        updated = self.db.table(COL_SESSIONS).update(
            {"is_public": bool(is_public), "updated_at": utcnow().isoformat()},
            filters={"session_id": session_id},
            returning=True,
        )
        if updated and isinstance(updated, list):
            return _session_row_to_doc(updated[0])
        return self.get(session_id)

    def list_public_submissions_for_case(self, case_id: str, *, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        rows = self.db.table(COL_SESSIONS).select(
            filters={"case_id": case_id, "status": ("in", ["closed", "COMPLETED"]), "is_public": True},
            columns="session_id,user_id,case_id,status,is_public,ended_at,created_at,updated_at",
            order="ended_at.desc.nullslast,created_at.desc",
            limit=limit,
            offset=offset,
        )
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(row)
        return out


@dataclass
class MessageRepo:
    db: SupabaseClient

    def append(
        self,
        session_id: str,
        visit_no: int,
        turn_no: int,
        role: str,
        content: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        row = {
            "session_id": session_id,
            "visit_number": int(visit_no),
            "turn_index": int(turn_no),
            "role": role,
            "content": content,
        }
        inserted = self.db.table(COL_MESSAGES).insert(row)
        if inserted:
            return _message_row_to_doc(inserted[0])
        return _message_row_to_doc(row)

    def get_turn_message(self, session_id: str, visit_no: int, turn_no: int) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_MESSAGES).select(
            filters={
                "session_id": session_id,
                "visit_number": int(visit_no),
                "turn_index": int(turn_no),
            },
            limit=1,
        )
        if not rows:
            return None
        return _message_row_to_doc(rows[0])

    def upsert_turn_message(
        self,
        *,
        turn_id: str,
        session_id: str,
        visit_no: int,
        turn_no: int,
        role: str,
        content: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        existing = self.get_turn_message(session_id, visit_no, turn_no)
        if existing:
            return
        self.append(session_id, visit_no, turn_no, role, content, meta=meta)

    def list_last(self, session_id: str, n: int = 20) -> List[Dict[str, Any]]:
        rows = self.db.table(COL_MESSAGES).select(
            filters={"session_id": session_id},
            order="visit_number.desc,turn_index.desc",
            limit=n,
        )
        items = [_message_row_to_doc(row) for row in rows]
        items.reverse()
        return items

    def list_by_visit(self, session_id: str, visit_no: int) -> List[Dict[str, Any]]:
        rows = self.db.table(COL_MESSAGES).select(
            filters={"session_id": session_id, "visit_number": int(visit_no)},
            order=("turn_index", "asc"),
        )
        return [_message_row_to_doc(row) for row in rows]

    def list_all(self, session_id: str, *, page_size: int = 1000) -> List[Dict[str, Any]]:
        page_size = max(1, min(10000, int(page_size)))
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            rows = self.db.table(COL_MESSAGES).select(
                filters={"session_id": session_id},
                order="visit_number.asc,turn_index.asc",
                limit=page_size,
                offset=offset,
            )
            batch = [_message_row_to_doc(row) for row in rows]
            items.extend(batch)
            if len(rows) < page_size:
                break
            offset += page_size
        return items

    def count_for_session(self, session_id: str) -> int:
        _rows, total = self.db.table(COL_MESSAGES).select_with_count(
            filters={"session_id": session_id},
            columns="id",
            limit=1,
            offset=0,
        )
        return int(total or 0)


@dataclass
class SummaryRepo:
    db: SupabaseClient

    def upsert_summary(self, session_id: str, visit_no: int, summary_text: str) -> None:
        table = self.db.table(COL_SUMMARIES)
        rows = table.select(
            filters={"session_id": session_id, "visit_number": int(visit_no)},
            limit=1,
        )
        if rows:
            table.update(
                {"summary": summary_text},
                filters={"session_id": session_id, "visit_number": int(visit_no)},
            )
        else:
            table.insert(
                {
                    "session_id": session_id,
                    "visit_number": int(visit_no),
                    "summary": summary_text,
                },
                returning=False,
            )

    def get_visit(self, session_id: str, visit_no: int) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_SUMMARIES).select(
            filters={"session_id": session_id, "visit_number": int(visit_no)},
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "session_id": row.get("session_id"),
            "visit_no": int(row.get("visit_number") or visit_no),
            "summary_text": row.get("summary", ""),
            "embedding": row.get("embedding"),
            "created_at": row.get("created_at"),
        }

    def list_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self.db.table(COL_SUMMARIES).select(
            filters={"session_id": session_id},
            order=("visit_number", "asc"),
        )
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "session_id": row.get("session_id"),
                    "visit_no": int(row.get("visit_number") or 1),
                    "summary_text": row.get("summary", ""),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        return out


@dataclass
class TurnRepo:
    db: SupabaseClient

    def start_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        visit_no: int,
        doctor_turn_no: int,
    ) -> Dict[str, Any]:
        message_repo = MessageRepo(self.db)
        if int(doctor_turn_no) == 0:
            existing = message_repo.get_turn_message(session_id, visit_no, 0)
            status = "persisted" if existing else "started"
        else:
            doctor_msg = message_repo.get_turn_message(session_id, visit_no, doctor_turn_no)
            patient_msg = message_repo.get_turn_message(session_id, visit_no, doctor_turn_no + 1)
            status = "persisted" if doctor_msg and patient_msg else "started"
        return {
            "turn_id": turn_id,
            "session_id": session_id,
            "visit_no": visit_no,
            "doctor_turn_no": doctor_turn_no,
            "status": status,
        }

    def mark_status(self, turn_id: str, status: str) -> None:
        return None

    def get(self, turn_id: str) -> Optional[Dict[str, Any]]:
        return None


@dataclass
class ProgressRepo:
    db: SupabaseClient

    def get_for_case(self, doctor_id: str, case_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_USER_CASE_PROGRESS).select(
            filters={"user_id": _normalize_user_id(doctor_id), "case_id": case_id},
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        return row

    def list_for_user(self, doctor_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self.db.table(COL_USER_CASE_PROGRESS).select(
            filters={"user_id": _normalize_user_id(doctor_id)},
            order=("updated_at", "desc"),
            limit=limit,
        )
        return [row for row in rows if isinstance(row, dict)]

    def mark_in_progress(self, doctor_id: str, case_id: str, *, session_id: str) -> None:
        now = utcnow().isoformat()
        table = self.db.table(COL_USER_CASE_PROGRESS)
        existing = self.get_for_case(doctor_id, case_id)
        if existing:
            if str(existing.get("status") or "") == "SOLVED":
                table.update(
                    {"last_session_id": session_id, "updated_at": now},
                    filters={"user_id": _normalize_user_id(doctor_id), "case_id": case_id},
                )
            else:
                table.update(
                    {"status": "IN_PROGRESS", "last_session_id": session_id, "updated_at": now},
                    filters={"user_id": _normalize_user_id(doctor_id), "case_id": case_id},
                )
            return
        table.insert(
            {
                "user_id": _normalize_user_id(doctor_id),
                "case_id": case_id,
                "status": "IN_PROGRESS",
                "last_session_id": session_id,
                "created_at": now,
                "updated_at": now,
            },
            returning=False,
        )

    def mark_solved(self, doctor_id: str, case_id: str, *, session_id: str) -> None:
        now = utcnow().isoformat()
        table = self.db.table(COL_USER_CASE_PROGRESS)
        existing = self.get_for_case(doctor_id, case_id)
        values = {
            "status": "SOLVED",
            "last_session_id": session_id,
            "solved_session_id": session_id,
            "solved_at": now,
            "updated_at": now,
        }
        if existing:
            table.update(values, filters={"user_id": _normalize_user_id(doctor_id), "case_id": case_id})
        else:
            table.insert(
                {
                    "user_id": _normalize_user_id(doctor_id),
                    "case_id": case_id,
                    **values,
                    "created_at": now,
                },
                returning=False,
            )


@dataclass
class EvaluationArtifactRepo:
    db: SupabaseClient

    def get_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.table(COL_EVALUATION_ARTIFACTS).select(filters={"session_id": session_id}, limit=1)
        if not rows:
            return None
        row = rows[0]
        return row if isinstance(row, dict) else None

    def create_if_missing(
        self,
        *,
        session_id: str,
        user_id: str,
        case_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        existing = self.get_for_session(session_id)
        if existing:
            return existing
        inserted = self.db.table(COL_EVALUATION_ARTIFACTS).insert(
            {
                "session_id": session_id,
                "user_id": user_id,
                "case_id": case_id,
                "status": "PENDING",
                "payload": payload,
            }
        )
        if inserted and isinstance(inserted, list) and inserted and isinstance(inserted[0], dict):
            return inserted[0]
        return {"session_id": session_id, "user_id": user_id, "case_id": case_id, "status": "PENDING", "payload": payload}
