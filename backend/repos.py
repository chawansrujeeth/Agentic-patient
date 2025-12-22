# repos.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from db import SupabaseClient
from models import COL_CASES, COL_MESSAGES, COL_SESSIONS, COL_SUMMARIES, utcnow

_USER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "docs-ai-users")


def _normalize_user_id(raw: str) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError):
        return str(uuid.uuid5(_USER_NAMESPACE, str(raw)))


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
        # Supabase schema does not include doctors; default to level 0.
        now = utcnow().isoformat()
        return {"doctor_id": doctor_id, "level": level, "created_at": now, "updated_at": now}


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


@dataclass
class SessionRepo:
    db: SupabaseClient

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
