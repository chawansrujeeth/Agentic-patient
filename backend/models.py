# models.py
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


Role = Literal["doctor", "patient", "system"]
SessionStatus = Literal["active", "closed"]


class SessionModel(BaseModel):
    session_id: str
    doctor_id: str
    case_id: str
    visit_no: int = 1
    turn_no: int = 0
    status: SessionStatus = "active"
    disclosed_fact_ids: List[str] = Field(default_factory=list)
    performed_exams: List[str] = Field(default_factory=list)
    performed_tests: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MessageModel(BaseModel):
    session_id: str
    visit_no: int
    turn_no: int
    role: Role
    content: str
    ts: datetime
    meta: Dict[str, Any] = Field(default_factory=dict)


class CaseChunkModel(BaseModel):
    """
    One progressive disclosure unit.
    """

    chunk_id: str
    visit_no: int
    stage: int = 0  # finer granularity within a visit
    kind: Literal["baseline", "symptoms", "history", "exam", "tests", "assessment", "plan"] = "symptoms"
    detail_depth: int = 1  # 1=coarse, 2=moderate, 3=full
    content: str
    tags: List[str] = Field(default_factory=list)


class CaseModel(BaseModel):
    case_id: str
    title: str = ""
    dx: str = ""  # can be hidden from low levels; still stored
    case_type: str = ""
    chunks: List[CaseChunkModel]


class GraphStateModel(BaseModel):
    """
    Define the LangGraph state contract even before wiring up a graph.
    """

    doctor_id: str
    level: int
    session: Optional[SessionModel] = None
    case: Optional[CaseModel] = None
    allowed: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------
# Collections (single source of truth)
# -----------------------------
COL_DOCTORS = "doctors"
COL_CASES = "cases"
COL_SESSIONS = "sessions"
COL_MESSAGES = "messages"
COL_SUMMARIES = "visit_summaries"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -----------------------------
# Indexes
# -----------------------------
def ensure_indexes(db: Any) -> None:
    # Indexes are managed in Supabase migrations.
    return None


# -----------------------------
# Seeding cases
# -----------------------------
def seed_cases_if_missing(db: Any, seed_path: Optional[str] = None) -> int:
    """
    Reads seed cases JSON and inserts any cases not already present.
    Returns number of inserted cases.
    """
    if seed_path is None:
        seed_path = os.path.join(os.path.dirname(__file__), "cases", "seed_cases.json")
    if not os.path.exists(seed_path):
        raise RuntimeError(f"Seed file not found: {seed_path}")

    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise RuntimeError("seed_cases.json must be a JSON array of case objects")

    inserted_or_updated = 0
    case_table = db.table(COL_CASES)

    for case in data:
        if not isinstance(case, dict):
            continue
        case_id = case.get("case_id")
        if not case_id:
            continue

        existing_rows = case_table.select(filters={"case_id": case_id}, limit=1)
        existing = existing_rows[0] if existing_rows else None

        if not existing:
            row = {
                "case_id": case_id,
                "title": case.get("title", ""),
                "seed": case,
            }
            case_table.insert(row, returning=False)
            inserted_or_updated += 1
            continue

        existing_seed = existing.get("seed") if isinstance(existing, dict) else None
        if not isinstance(existing_seed, dict):
            existing_seed = {}

        needs_chunks = "chunks" not in existing_seed and case.get("chunks")
        needs_case_type = "case_type" not in existing_seed and case.get("case_type")
        if needs_chunks or needs_case_type:
            update_row = {
                "title": case.get("title", existing.get("title", "")),
                "seed": case,
            }
            case_table.update(update_row, filters={"case_id": case_id})
            inserted_or_updated += 1

    return inserted_or_updated


# Repository layer moved to repos.py for clarity
