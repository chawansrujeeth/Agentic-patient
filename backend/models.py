# models.py
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


Role = Literal["doctor", "patient", "system"]
SessionStatus = Literal["active", "closed"]
CaseDifficulty = Literal["Easy", "Medium", "Hard"]


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
    visit_no: int = 1
    stage: int = 0  # finer granularity within a visit
    kind: Literal["baseline", "symptoms", "history", "exam", "tests", "assessment", "plan"] = "symptoms"
    detail_depth: int = 1  # 1=coarse, 2=moderate, 3=full
    content: str
    tags: List[str] = Field(default_factory=list)


class CaseModel(BaseModel):
    case_id: str
    title: str
    difficulty: CaseDifficulty = "Easy"
    tags: List[str] = Field(default_factory=list)
    short_prompt: str = ""
    estimated_time_min: int = 15
    version: int = 1
    is_published: bool = True
    dx: str = ""  # can be hidden from low levels; still stored
    case_type: str = ""
    chunks: List[CaseChunkModel] = Field(default_factory=list)


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
COL_USERS = "users"
COL_CASES = "cases"
COL_SESSIONS = "sessions"
COL_MESSAGES = "messages"
COL_SUMMARIES = "visit_summaries"
COL_USER_CASE_PROGRESS = "user_case_progress"
COL_EVALUATION_ARTIFACTS = "evaluation_artifacts"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -----------------------------
# Indexes
# -----------------------------
def ensure_indexes(db: Any) -> None:
    # Indexes are managed in Supabase SQL migrations/scripts.
    return None


# -----------------------------
# Case metadata normalization (problemset)
# -----------------------------
_ALLOWED_DIFFICULTIES = {"Easy", "Medium", "Hard"}
_DEFAULT_ESTIMATED_TIME_MIN = 15
_DEFAULT_CASE_VERSION = 1
_SHORT_PROMPT_LEN = 140


def _normalize_difficulty(raw: Any) -> str:
    val = str(raw or "").strip()
    if not val:
        return ""
    lowered = val.lower()
    if lowered == "easy":
        return "Easy"
    if lowered == "medium":
        return "Medium"
    if lowered == "hard":
        return "Hard"
    return ""


def _first_chunk_text(case: Dict[str, Any]) -> str:
    chunks = case.get("chunks")
    if not isinstance(chunks, list):
        return ""
    baseline = None
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        if chunk.get("kind") == "baseline":
            baseline = chunk
            break
    candidate = baseline if isinstance(baseline, dict) else (chunks[0] if chunks else None)
    if isinstance(candidate, dict):
        return str(candidate.get("content") or "").strip()
    return ""


def _derive_short_prompt(case: Dict[str, Any]) -> str:
    text = _first_chunk_text(case)
    if not text:
        text = str(case.get("case_text") or case.get("prompt") or "").strip()
    if not text:
        fallback = str(case.get("title") or case.get("dx") or "").strip()
        return fallback[:_SHORT_PROMPT_LEN].strip()
    normalized = " ".join(text.split())
    return normalized[:_SHORT_PROMPT_LEN].strip()


_AGE_PATTERNS = (
    re.compile(r"\b(\d{1,3})\s*-\s*year\s*-\s*old\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})\s*(?:yo|y/o)\b", re.IGNORECASE),
)
_SEX_PATTERN = re.compile(r"\b(female|male|woman|man|girl|boy)\b", re.IGNORECASE)


def _derive_title(case: Dict[str, Any]) -> str:
    """
    Best-effort derivation used only when no explicit title exists.
    """
    text = _first_chunk_text(case) or str(case.get("short_prompt") or "").strip()
    dx = str(case.get("dx") or "").strip()
    if not text and dx:
        return dx
    if not text:
        return "Untitled Case"

    normalized = " ".join(text.split()).strip().rstrip(".")

    complaint = normalized
    for prefix in (
        "i'm here because",
        "im here because",
        "i am here because",
        "i came in because",
        "i'm here for",
        "im here for",
        "i am here for",
        "i have",
        "i've had",
        "ive had",
        "i've been",
        "ive been",
    ):
        if complaint.lower().startswith(prefix):
            complaint = complaint[len(prefix) :].strip(" :,-")
            break
    complaint = complaint.split(".")[0].strip().rstrip(".")
    if complaint:
        complaint = complaint[:80].strip()
        complaint = complaint[:1].upper() + complaint[1:]

    age = None
    for pat in _AGE_PATTERNS:
        match = pat.search(normalized)
        if match:
            age = match.group(1)
            break
    sex_raw = None
    match = _SEX_PATTERN.search(normalized)
    if match:
        sex_raw = match.group(1).lower()
    sex = None
    if sex_raw:
        sex = "Female" if sex_raw in ("female", "woman", "girl") else "Male"

    if complaint and age and sex:
        return f"{complaint} in a {age}-year-old {sex}"
    return complaint or "Untitled Case"


def normalize_case_problemset_fields(case: Dict[str, Any], *, default_tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Ensures a case has the minimal metadata needed for listing as a "problem".
    This is designed to be safe to run repeatedly (idempotent) and does not
    overwrite valid, existing fields.
    """
    out = dict(case)

    title = str(out.get("title") or "").strip()
    if not title:
        title = _derive_title(out)
        out["title"] = title

    difficulty = _normalize_difficulty(out.get("difficulty"))
    out["difficulty"] = difficulty if difficulty in _ALLOWED_DIFFICULTIES else "Easy"

    tags = out.get("tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        out["tags"] = list(default_tags) if default_tags is not None else []
    else:
        out["tags"] = [tag.strip() for tag in tags if tag and tag.strip()]

    short_prompt = str(out.get("short_prompt") or "").strip()
    if not short_prompt:
        out["short_prompt"] = _derive_short_prompt(out)

    estimated = out.get("estimated_time_min")
    if not isinstance(estimated, int) or estimated <= 0:
        out["estimated_time_min"] = _DEFAULT_ESTIMATED_TIME_MIN

    version = out.get("version")
    if not isinstance(version, int) or version <= 0:
        out["version"] = _DEFAULT_CASE_VERSION

    is_published = out.get("is_published")
    if not isinstance(is_published, bool):
        out["is_published"] = True

    return out


def _strip_unknown_columns(row: Dict[str, Any], response_text: str) -> Dict[str, Any]:
    # PostgREST errors include the missing column name; remove known optional
    # problemset columns and retry.
    optional_cols = ("difficulty", "tags", "short_prompt", "estimated_time_min", "version", "is_published")
    cleaned = dict(row)
    lowered = (response_text or "").lower()
    for col in optional_cols:
        if col in cleaned and col.lower() in lowered:
            cleaned.pop(col, None)
    return cleaned


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

        normalized_case = normalize_case_problemset_fields(case, default_tags=["General Medicine"])
        normalized_case["case_id"] = case_id

        existing_rows = case_table.select(filters={"case_id": case_id}, limit=1)
        existing = existing_rows[0] if existing_rows else None

        if not existing:
            row = {
                "case_id": case_id,
                "title": normalized_case.get("title", ""),
                "seed": normalized_case,
                "difficulty": normalized_case.get("difficulty", "Easy"),
                "tags": normalized_case.get("tags", []),
                "short_prompt": normalized_case.get("short_prompt", ""),
                "estimated_time_min": normalized_case.get("estimated_time_min", _DEFAULT_ESTIMATED_TIME_MIN),
                "version": normalized_case.get("version", _DEFAULT_CASE_VERSION),
                "is_published": normalized_case.get("is_published", True),
            }
            try:
                case_table.insert(row, returning=False)
            except Exception as exc:
                # Backwards compatibility: older Supabase schemas only include
                # case_id/title/seed columns.
                try:
                    from db import SupabaseError

                    if isinstance(exc, SupabaseError):
                        row = _strip_unknown_columns(row, exc.response_text or "")
                        row = {key: row[key] for key in ("case_id", "title", "seed") if key in row}
                        case_table.insert(row, returning=False)
                    else:
                        raise
                except Exception:
                    raise
            inserted_or_updated += 1
            continue

        existing_seed = existing.get("seed") if isinstance(existing, dict) else None
        if not isinstance(existing_seed, dict):
            existing_seed = {}

        needs_chunks = "chunks" not in existing_seed and normalized_case.get("chunks")
        needs_case_type = "case_type" not in existing_seed and normalized_case.get("case_type")
        if needs_chunks or needs_case_type:
            update_row = {
                "title": normalized_case.get("title", existing.get("title", "")),
                "seed": normalized_case,
                "difficulty": normalized_case.get("difficulty", "Easy"),
                "tags": normalized_case.get("tags", []),
                "short_prompt": normalized_case.get("short_prompt", ""),
                "estimated_time_min": normalized_case.get("estimated_time_min", _DEFAULT_ESTIMATED_TIME_MIN),
                "version": normalized_case.get("version", _DEFAULT_CASE_VERSION),
                "is_published": normalized_case.get("is_published", True),
            }
            try:
                case_table.update(update_row, filters={"case_id": case_id})
            except Exception as exc:
                try:
                    from db import SupabaseError

                    if isinstance(exc, SupabaseError):
                        update_row = _strip_unknown_columns(update_row, exc.response_text or "")
                        update_row = {key: update_row[key] for key in ("title", "seed") if key in update_row}
                        case_table.update(update_row, filters={"case_id": case_id})
                    else:
                        raise
                except Exception:
                    raise
            inserted_or_updated += 1

    return inserted_or_updated


# Repository layer moved to repos.py for clarity
