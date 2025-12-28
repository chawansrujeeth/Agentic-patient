from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request

from db import SupabaseError, get_db, ping
from graph_state import GraphState
from llm import QuotaExhaustedError
from models import ensure_indexes, seed_cases_if_missing
from policy import max_visits
from repos import (
    CaseRepo,
    DoctorRepo,
    EvaluationArtifactRepo,
    MessageRepo,
    ProgressRepo,
    SessionRepo,
    SummaryRepo,
    UserRepo,
    normalize_user_id,
)
from session_utils import graph_state_from_session

K_MSGS = int(os.getenv("CONTEXT_LAST_K_MSGS", "12"))
DEFAULT_SESSION_LIST_LIMIT = int(os.getenv("SESSIONS_LIST_LIMIT", "50"))
API_PREFIX = "/api"
_ALLOWED_DIFFICULTIES = {"Easy", "Medium", "Hard"}
_COMPLETED_STATUSES = ["closed", "CLOSED", "completed", "COMPLETED"]


def _error(message: str, status: int) -> Any:
    return jsonify({"error": message}), status


class AuthError(RuntimeError):
    pass


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _verify_supabase_jwt(token: str) -> Dict[str, Any]:
    secret = os.getenv("SUPABASE_JWT_SECRET") or os.getenv("JWT_SECRET")
    if not secret:
        raise AuthError("Missing SUPABASE_JWT_SECRET")

    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("Malformed JWT")

    try:
        header_raw = _b64url_decode(parts[0])
        payload_raw = _b64url_decode(parts[1])
    except Exception as exc:
        raise AuthError("Invalid JWT encoding") from exc
    try:
        header = json.loads(header_raw.decode("utf-8"))
        payload = json.loads(payload_raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthError("Invalid JWT JSON") from exc

    if header.get("alg") != "HS256":
        raise AuthError("Unsupported JWT alg")

    signing_input = f"{parts[0]}.{parts[1]}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    expected_b64 = _b64url_encode(expected_sig)
    signature_segment = parts[2].rstrip("=")
    if not hmac.compare_digest(expected_b64, signature_segment):
        raise AuthError("Invalid JWT signature")

    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_val = int(exp)
        except (TypeError, ValueError):
            raise AuthError("Invalid JWT exp") from None
        if int(time.time()) >= exp_val:
            raise AuthError("JWT expired")

    aud = (os.getenv("SUPABASE_JWT_AUD") or os.getenv("JWT_AUD") or "").strip()
    if aud:
        payload_aud = payload.get("aud")
        if isinstance(payload_aud, list):
            valid = aud in payload_aud
        else:
            valid = payload_aud == aud
        if not valid:
            raise AuthError("JWT aud mismatch")

    return payload


def _require_doctor_id() -> Tuple[Optional[str], Optional[Tuple[str, int]]]:
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(None, 1)[1].strip()
        if not token:
            return None, ("Missing bearer token", 401)
        try:
            claims = _verify_supabase_jwt(token)
        except AuthError as exc:
            return None, (str(exc), 401)
        doctor_id = str(claims.get("sub") or claims.get("user_id") or "").strip()
        if not doctor_id:
            return None, ("JWT missing user id", 401)
        return doctor_id, None

    guest_id = request.headers.get("X-Guest-Id")
    if guest_id and guest_id.strip():
        return guest_id.strip(), None

    doctor_id = request.headers.get("X-Doctor-Id") or request.headers.get("X-User-Id")
    if doctor_id and doctor_id.strip():
        return doctor_id.strip(), None

    return None, ("Missing Authorization Bearer token, X-Guest-Id, or X-Doctor-Id header", 401)


def _parse_json_body() -> Optional[Dict[str, Any]]:
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        return None
    return payload


def _coerce_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Handle common ISO variant used by Supabase/Postgres (and JS): "Z" suffix.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _utc_date_str(value: Any) -> Optional[str]:
    dt = _parse_dt(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _coerce_datetime(val) for key, val in row.items()}


def _serialize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_serialize_row(msg) for msg in messages]


def _serialize_sessions(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_serialize_row(sess) for sess in sessions]


def _coerce_state(values: Any, fallback: GraphState) -> GraphState:
    if isinstance(values, GraphState):
        return values
    if isinstance(values, dict):
        try:
            return GraphState.model_validate(values)
        except Exception:
            return fallback.model_copy(update=values)
    return fallback


def _summarize_retrieved(retrieved: Dict[str, Any]) -> List[Dict[str, Any]]:
    summaries = retrieved.get("summaries")
    if not isinstance(summaries, list):
        return []
    return [
        {"doc_id": item.get("doc_id"), "text": item.get("text"), "visit_no": item.get("visit_no")}
        for item in summaries
        if isinstance(item, dict)
    ]


def create_app(*, init_db: bool = True, seed_cases: bool = True) -> Flask:
    app = Flask(__name__)
    # Vercel env vars: set SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY,
    # SUPABASE_JWT_SECRET, and optionally SUPABASE_JWT_AUD in Project Settings > Environment Variables.
    # Local dev: run `python backend/api.py` for Flask and `npm run dev` inside `frontend/`,
    # then check `curl http://localhost:5000/api/health`.
    try:
        from flask_cors import CORS
    except ImportError:
        CORS = None
    if CORS:
        CORS(app, resources={r"/api/*": {"origins": "*"}})

    @app.errorhandler(SupabaseError)
    def _handle_supabase_error(exc: SupabaseError) -> Any:
        return _error("Backend database is unavailable. Try again shortly.", 503)

    # Boot hooks similar to CLI startup.
    db = get_db()
    if init_db:
        ping()
        ensure_indexes(db)
    if seed_cases:
        seed_cases_if_missing(db)

    case_repo = CaseRepo(db)
    doctor_repo = DoctorRepo(db)
    user_repo = UserRepo(db)
    session_repo = SessionRepo(db)
    message_repo = MessageRepo(db)
    summary_repo = SummaryRepo(db)
    progress_repo = ProgressRepo(db)
    artifact_repo = EvaluationArtifactRepo(db)
    from graph import build_visit_graph, compose_visit_intro, make_persist_patient_intro

    visit_graph = build_visit_graph(db, use_checkpointer=False)

    def _email_to_display_name(email: str) -> str:
        raw = (email or "").strip()
        if not raw:
            return ""
        prefix = raw.split("@", 1)[0].strip()
        return prefix[:80]

    def _sync_doctor_profile(doctor_id: str) -> None:
        normalized_user_id = normalize_user_id(doctor_id)

        def _slugify_username(raw: str) -> str:
            cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in (raw or "").strip())
            cleaned = "-".join(part for part in cleaned.split("-") if part)
            if not cleaned:
                return ""
            return cleaned[:30]

        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(None, 1)[1].strip()
            if not token:
                return
            try:
                claims = _verify_supabase_jwt(token)
            except AuthError:
                return
            email = claims.get("email")
            if isinstance(email, str) and email.strip():
                display_name = _email_to_display_name(email)
                if display_name:
                    doctor_repo.upsert_display_name(doctor_id, display_name)
                suggested_username = _slugify_username(email.split("@", 1)[0])
                if suggested_username:
                    try:
                        user_repo.ensure_user(user_id=normalized_user_id, username=suggested_username, display_name=display_name)
                    except Exception:
                        pass
            return

        guest_id = (request.headers.get("X-Guest-Id") or "").strip()
        if guest_id:
            doctor_repo.upsert_display_name(doctor_id, f"Guest {guest_id[:8]}")
            suggested_username = _slugify_username(f"guest-{guest_id[:8]}")
            if suggested_username:
                try:
                    user_repo.ensure_user(
                        user_id=normalized_user_id,
                        username=suggested_username,
                        display_name=f"Guest {guest_id[:8]}",
                    )
                except Exception:
                    pass

    def require_doctor_id() -> Tuple[Optional[str], Optional[Tuple[str, int]]]:
        doctor_id, err = _require_doctor_id()
        if err:
            return None, err
        try:
            _sync_doctor_profile(doctor_id)
        except Exception:
            pass
        return doctor_id, None

    def _synthetic_username(user_id: str) -> str:
        return f"u-{normalize_user_id(user_id)}"

    def _resolve_user_id_from_username(username: str) -> Tuple[Optional[str], Optional[str]]:
        def _looks_like_uuid(value: str) -> bool:
            raw = (value or "").strip()
            if len(raw) != 36:
                return False
            try:
                import uuid as _uuid

                _uuid.UUID(raw)
                return True
            except Exception:
                return False

        handle = (username or "").strip()
        if not handle:
            return None, None
        if handle.startswith("u-"):
            candidate = handle[2:].strip()
            if candidate and _looks_like_uuid(candidate):
                return normalize_user_id(candidate), None
        if _looks_like_uuid(handle):
            return normalize_user_id(handle), None
        row = user_repo.get_by_username(handle)
        if row and isinstance(row, dict):
            uid = str(row.get("user_id") or "").strip()
            if uid:
                return normalize_user_id(uid), str(row.get("username") or "").strip() or None
        return None, None

    def _heatmap_last_365(*, user_id: str, public_only: bool) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=364)
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        start_iso = start_dt.isoformat().replace("+00:00", "Z")

        filters: Dict[str, Any] = {
            "user_id": normalize_user_id(user_id),
            "status": ("in", _COMPLETED_STATUSES),
            "ended_at": ("gte", start_iso),
        }
        if public_only:
            filters["is_public"] = True
        try:
            rows = db.table("sessions").select(
                filters=filters,
                columns="ended_at",
                limit=5000,
                order="ended_at.asc.nullslast",
            )
        except Exception:
            rows = []

        counts: Dict[str, int] = {}
        last_solved_at: Optional[str] = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            date_str = _utc_date_str(row.get("ended_at"))
            if not date_str:
                continue
            counts[date_str] = counts.get(date_str, 0) + 1
            last_solved_at = date_str

        heatmap: List[Dict[str, Any]] = []
        cursor = start_date
        for _ in range(365):
            key = cursor.isoformat()
            heatmap.append({"date": key, "count": int(counts.get(key, 0))})
            cursor = cursor + timedelta(days=1)
        return heatmap, last_solved_at

    def _compute_streaks(heatmap: List[Dict[str, Any]]) -> Tuple[int, int]:
        flags = [bool(int(item.get("count") or 0) > 0) for item in heatmap]
        current = 0
        for flag in reversed(flags):
            if not flag:
                break
            current += 1
        best = 0
        run = 0
        for flag in flags:
            if flag:
                run += 1
                if run > best:
                    best = run
            else:
                run = 0
        return current, best

    def _case_meta(case_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        unique = sorted({str(cid) for cid in case_ids if cid})
        if not unique:
            return {}
        try:
            rows = db.table("cases").select(
                filters={"case_id": ("in", unique)},
                columns="case_id,title,difficulty",
                limit=len(unique),
            )
        except Exception:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("case_id") or "").strip()
            if not cid:
                continue
            out[cid] = {
                "title": str(row.get("title") or "").strip(),
                "difficulty": str(row.get("difficulty") or "Easy").strip() or "Easy",
            }
        return out

    def _auto_badges(*, solved_count: int, max_streak: int, earned_at: Optional[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if solved_count >= 1:
            out.append({"key": "first_solve", "label": "First Solve", "earned_at": earned_at})
        if solved_count >= 5:
            out.append({"key": "five_solves", "label": "5 Solves", "earned_at": earned_at})
        if solved_count >= 10:
            out.append({"key": "ten_solves", "label": "10 Solves", "earned_at": earned_at})
        if max_streak >= 7:
            out.append({"key": "streak_7", "label": "7-Day Streak", "earned_at": earned_at})
        return out

    @app.route(f"{API_PREFIX}/me", methods=["GET"])
    def get_me() -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])
        uid = normalize_user_id(doctor_id)
        row = None
        try:
            row = user_repo.get_by_user_id(uid)
        except Exception:
            row = None
        if not row:
            try:
                row = user_repo.ensure_user(user_id=uid, username=_synthetic_username(uid), display_name="")
            except Exception:
                row = None
        display_name = ""
        try:
            display_name = doctor_repo.get_display_names([uid]).get(uid) or ""
        except Exception:
            display_name = ""
        user = {
            "user_id": uid,
            "username": str(row.get("username") if isinstance(row, dict) else "") or _synthetic_username(uid),
            "display_name": str(row.get("display_name") if isinstance(row, dict) else "") or display_name,
            "avatar_url": str(row.get("avatar_url") if isinstance(row, dict) else "") or None,
            "bio": str(row.get("bio") if isinstance(row, dict) else "") or None,
        }
        return jsonify({"user": user})

    @app.route(f"{API_PREFIX}/users/id/<user_id>", methods=["GET"])
    def get_user_by_id(user_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])
        target = normalize_user_id(user_id)
        row = user_repo.get_by_user_id(target)
        username = str(row.get("username") or "").strip() if isinstance(row, dict) else ""
        return jsonify({"user_id": target, "username": username or _synthetic_username(target)})

    @app.route(f"{API_PREFIX}/users/<username>/profile", methods=["GET"])
    def get_user_profile(username: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])
        viewer_id = normalize_user_id(doctor_id)

        target_id, canonical = _resolve_user_id_from_username(username)
        if not target_id:
            return _error("User not found", 404)

        is_owner = target_id == viewer_id
        public_only = not is_owner

        profile_row = user_repo.get_by_user_id(target_id)
        profile_username = canonical or (str(profile_row.get("username") or "").strip() if isinstance(profile_row, dict) else "") or _synthetic_username(target_id)
        display_name = str(profile_row.get("display_name") or "").strip() if isinstance(profile_row, dict) else ""
        if not display_name:
            display_name = doctor_repo.get_display_names([target_id]).get(target_id) or ""

        heatmap, last_solved_at = _heatmap_last_365(user_id=target_id, public_only=public_only)
        current_streak, max_streak = _compute_streaks(heatmap)

        solved_count = 0
        recent_solved: List[Dict[str, Any]] = []
        if is_owner:
            try:
                rows = db.table("user_case_progress").select(
                    filters={"user_id": target_id, "status": "SOLVED"},
                    columns="case_id,solved_at",
                    order="solved_at.desc.nullslast,updated_at.desc",
                    limit=10,
                )
            except Exception:
                rows = []
            case_ids = [str(r.get("case_id") or "") for r in rows if isinstance(r, dict)]
            meta = _case_meta(case_ids)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cid = str(row.get("case_id") or "")
                solved_at = row.get("solved_at")
                m = meta.get(cid, {})
                recent_solved.append(
                    {
                        "case_id": cid,
                        "title": m.get("title") or "",
                        "difficulty": m.get("difficulty") or "Easy",
                        "solved_at": solved_at,
                    }
                )
            try:
                _solved_rows, solved_total = db.table("user_case_progress").select_with_count(
                    filters={"user_id": target_id, "status": "SOLVED"},
                    columns="case_id",
                    limit=1,
                    offset=0,
                )
                solved_count = int(solved_total or 0)
            except Exception:
                solved_count = 0
        else:
            try:
                session_rows = db.table("sessions").select(
                    filters={"user_id": target_id, "is_public": True, "status": ("in", _COMPLETED_STATUSES)},
                    columns="case_id,ended_at",
                    order="ended_at.desc.nullslast,updated_at.desc",
                    limit=2000,
                )
            except Exception:
                session_rows = []
            seen_cases: set[str] = set()
            ordered_cases: List[Tuple[str, Any]] = []
            for row in session_rows:
                if not isinstance(row, dict):
                    continue
                cid = str(row.get("case_id") or "")
                if not cid:
                    continue
                if cid not in seen_cases:
                    seen_cases.add(cid)
                    if len(ordered_cases) < 10:
                        ordered_cases.append((cid, row.get("ended_at")))
            meta = _case_meta([cid for cid, _ in ordered_cases])
            for cid, ended_at in ordered_cases:
                m = meta.get(cid, {})
                recent_solved.append(
                    {
                        "case_id": cid,
                        "title": m.get("title") or "",
                        "difficulty": m.get("difficulty") or "Easy",
                        "solved_at": ended_at,
                    }
                )
            solved_count = len(seen_cases)

        badges = _auto_badges(solved_count=solved_count, max_streak=max_streak, earned_at=last_solved_at)

        return jsonify(
            {
                "user": {
                    "username": profile_username,
                    "display_name": display_name or profile_username,
                    "avatar_url": (str(profile_row.get("avatar_url") or "").strip() if isinstance(profile_row, dict) else "") or None,
                    "bio": (str(profile_row.get("bio") or "").strip() if isinstance(profile_row, dict) else "") or None,
                },
                "stats": {
                    "solved_count": int(solved_count),
                    "current_streak": int(current_streak),
                    "max_streak": int(max_streak),
                },
                "heatmap": heatmap,
                "recent_solved": _serialize_sessions(recent_solved),
                "badges": _serialize_sessions(badges),
            }
        )

    @app.route(f"{API_PREFIX}/users/<username>/submissions", methods=["GET"])
    def list_user_submissions(username: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        target_id, _canonical = _resolve_user_id_from_username(username)
        if not target_id:
            return _error("User not found", 404)

        case_filter = (request.args.get("case_id") or "").strip() or None
        page_raw = (request.args.get("page") or "").strip()
        limit_raw = (request.args.get("limit") or "").strip()
        page = 1
        limit = 20
        if page_raw:
            try:
                page = max(1, int(page_raw))
            except ValueError:
                return _error("page must be an integer", 400)
        if limit_raw:
            try:
                limit = max(1, min(50, int(limit_raw)))
            except ValueError:
                return _error("limit must be an integer", 400)
        offset = (page - 1) * limit

        filters: Dict[str, Any] = {"user_id": target_id, "is_public": True, "status": ("in", _COMPLETED_STATUSES)}
        if case_filter:
            filters["case_id"] = case_filter

        try:
            rows, total = db.table("sessions").select_with_count(
                filters=filters,
                columns="session_id,case_id,ended_at",
                order="ended_at.desc.nullslast,updated_at.desc",
                limit=limit,
                offset=offset,
            )
        except Exception:
            rows, total = ([], 0)

        case_ids = [str(r.get("case_id") or "") for r in rows if isinstance(r, dict)]
        meta = _case_meta(case_ids)
        items: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("session_id") or "")
            cid = str(row.get("case_id") or "")
            ended_at = row.get("ended_at")
            m = meta.get(cid, {})
            try:
                message_count = message_repo.count_for_session(sid) if sid else 0
            except Exception:
                message_count = 0
            items.append(
                {
                    "session_id": sid,
                    "case_id": cid,
                    "title": m.get("title") or "",
                    "ended_at": ended_at,
                    "message_count": int(message_count),
                }
            )

        return jsonify({"items": _serialize_sessions(items), "page": page, "limit": limit, "total": int(total or 0)})

    @app.route(f"{API_PREFIX}/sessions", methods=["POST"])
    def create_session() -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        payload = _parse_json_body()
        if payload is None:
            return _error("Invalid JSON body", 400)
        case_id = str(payload.get("case_id", "")).strip()
        if not case_id:
            return _error("Missing case_id", 400)

        try:
            case_doc = case_repo.get(case_id)
        except SupabaseError:
            return _error("Backend database is unavailable. Try again shortly.", 503)
        except Exception as exc:
            return _error(f"Unable to load case: {exc}", 500)
        if not case_doc:
            return _error(f"Case not found: {case_id}", 404)

        try:
            progress = progress_repo.get_for_case(doctor_id, case_id)
            if progress and str(progress.get("status") or "") == "SOLVED":
                solved_session_id = progress.get("solved_session_id")
                if solved_session_id:
                    return (
                        jsonify(
                            {
                                "error": "Case already solved; session is read-only.",
                                "solved_session_id": str(solved_session_id),
                            }
                        ),
                        409,
                    )
                return _error("Case already solved; session is read-only.", 409)
        except Exception:
            pass

        try:
            completed = session_repo.find_latest_completed_for_case(doctor_id, case_id)
            if completed and completed.get("session_id"):
                solved_session_id = str(completed["session_id"])
                try:
                    progress_repo.mark_solved(doctor_id, case_id, session_id=solved_session_id)
                except Exception:
                    pass
                return (
                    jsonify(
                        {
                            "error": "Case already solved; session is read-only.",
                            "solved_session_id": solved_session_id,
                        }
                    ),
                    409,
                )
        except Exception:
            pass

        session_doc = session_repo.create(doctor_id=doctor_id, case_id=case_id)
        try:
            progress_repo.mark_in_progress(doctor_id, case_id, session_id=session_doc["session_id"])
        except Exception:
            pass
        state = graph_state_from_session(session_doc)
        doctor = doctor_repo.get_or_create(doctor_id, level=0)
        state.doctor_level = int(doctor.get("level", 0))
        try:
            state = compose_visit_intro(state)
            persist_intro = make_persist_patient_intro(db)
            state = persist_intro(state)
        except Exception:
            pass
        last_messages = message_repo.list_last(session_doc["session_id"], n=K_MSGS)

        return (
            jsonify(
                {
                    "session_id": session_doc["session_id"],
                    "session": _serialize_row(session_doc),
                    "state": state.model_dump(mode="json"),
                    "last_messages": _serialize_messages(last_messages),
                }
            ),
            201,
        )

    @app.route(f"{API_PREFIX}/sessions", methods=["GET"])
    def list_sessions() -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        limit_raw = request.args.get("limit", "").strip()
        limit = DEFAULT_SESSION_LIST_LIMIT
        if limit_raw:
            try:
                limit = max(1, int(limit_raw))
            except ValueError:
                return _error("limit must be an integer", 400)

        status = request.args.get("status", "").strip() or None
        sessions = session_repo.list_for_user(doctor_id, status=status, limit=limit)

        return jsonify({"sessions": _serialize_sessions(sessions)})

    @app.route(f"{API_PREFIX}/sessions/<session_id>", methods=["GET"])
    def get_session(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_row = session_repo.get_row(session_id)
        if not session_row:
            return _error("Session not found", 404)

        owner_row = session_repo.get_row_for_user(session_id, doctor_id)
        is_owner = owner_row is not None
        status = str(session_row.get("status") or "active")
        is_public = bool(session_row.get("is_public", False))
        is_completed = status.lower() in {"closed", "completed"}
        if not is_owner:
            if not (is_public and is_completed):
                return _error("Session not found", 404)

        all_flag = (request.args.get("all") or "").strip().lower() in {"1", "true", "yes"}
        n_raw = (request.args.get("n") or "").strip()
        n = 200
        if n_raw:
            try:
                n = max(1, min(5000, int(n_raw)))
            except ValueError:
                return _error("n must be an integer", 400)
        if all_flag or is_completed:
            messages = message_repo.list_all(session_id)
        else:
            messages = message_repo.list_last(session_id, n=n)

        user_id = str(session_row.get("user_id") or "")
        session_meta = {
            "session_id": str(session_row.get("session_id") or session_id),
            "user_id": user_id,
            "case_id": str(session_row.get("case_id") or ""),
            "status": "COMPLETED" if is_completed else "IN_PROGRESS",
            "is_public": is_public,
            "ended_at": session_row.get("ended_at"),
            "created_at": session_row.get("created_at"),
            "updated_at": session_row.get("updated_at"),
        }
        author_name = "You"
        author_username = None
        if not is_owner:
            names = doctor_repo.get_display_names([user_id] if user_id else [])
            author_name = names.get(user_id) or (f"User {user_id[:8]}" if user_id else "User")
            author_username = user_repo.get_usernames([user_id] if user_id else []).get(user_id) or (
                _synthetic_username(user_id) if user_id else None
            )
        return jsonify(
            {
                "session": _serialize_row(session_meta),
                "author_name": author_name,
                "author_username": author_username,
                "viewer_can_toggle_visibility": bool(is_owner and is_completed),
                "messages": _serialize_messages(messages),
            }
        )

    @app.route(f"{API_PREFIX}/sessions/<session_id>/send", methods=["POST"])
    def send_message(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        payload = _parse_json_body()
        if payload is None:
            return _error("Invalid JSON body", 400)
        message = str(payload.get("message", "")).strip()
        if not message:
            return _error("Missing message", 400)

        session_doc = session_repo.get_for_user(session_id, doctor_id)
        if not session_doc:
            return _error("Session not found", 404)
        if str(session_doc.get("status") or "active").lower() in {"closed", "completed"}:
            return _error("Session is read-only: already completed.", 409)

        state = graph_state_from_session(session_doc)
        state.last_doctor_message = message
        try:
            updated_values = visit_graph.invoke(state)
        except QuotaExhaustedError as exc:
            return _error(str(exc), 429)
        updated_state = _coerce_state(updated_values, state)

        response = {
            "patient_message": updated_state.patient_utterance,
            "updated_state": updated_state.model_dump(mode="json"),
            "retrieved_facts": _summarize_retrieved(updated_state.retrieved_context),
            "guardrails": {
                "rejected": updated_state.guardrail_rejected,
                "safety_flags": list(updated_state.safety_flags),
                "response_source": updated_state.response_source,
                "visit_end_recommendation": updated_state.visit_end_recommendation,
                "requested_clarifications": updated_state.requested_clarifications,
            },
        }
        return jsonify(response)

    @app.route(f"{API_PREFIX}/sessions/<session_id>/summarize", methods=["POST"])
    def summarize_session(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_doc = session_repo.get_for_user(session_id, doctor_id)
        if not session_doc:
            return _error("Session not found", 404)
        if str(session_doc.get("status") or "active").lower() in {"closed", "completed"}:
            return _error("Session is read-only: already completed.", 409)

        visit_no = int(session_doc.get("visit_no", 1))
        visit_messages = message_repo.list_by_visit(session_id, visit_no)
        if not visit_messages:
            return _error("No messages in this visit to summarize yet", 400)

        from summarize import summarize_visit_one_call

        try:
            summary_text = summarize_visit_one_call(
                db=db,
                session_id=session_id,
                visit_no=visit_no,
                messages_this_visit=visit_messages,
            )
        except QuotaExhaustedError as exc:
            return _error(str(exc), 429)
        return jsonify({"session_id": session_id, "visit_no": visit_no, "summary_text": summary_text})

    @app.route(f"{API_PREFIX}/sessions/<session_id>/endvisit", methods=["POST"])
    def end_visit(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_doc = session_repo.get_for_user(session_id, doctor_id)
        if not session_doc:
            return _error("Session not found", 404)
        if str(session_doc.get("status") or "active").lower() in {"closed", "completed"}:
            return _error("Session is read-only: already completed.", 409)

        doctor = doctor_repo.get_or_create(doctor_id, level=0)
        level = int(doctor.get("level", 0))
        cur_visit = int(session_doc.get("visit_no", 1))
        max_visit_count = max_visits(level)
        if cur_visit >= max_visit_count:
            return _error(f"Max visits reached for level {level}: {max_visit_count}", 400)

        summary_text = None
        visit_messages = message_repo.list_by_visit(session_id, cur_visit)
        if visit_messages:
            from summarize import summarize_visit_one_call

            try:
                summary_text = summarize_visit_one_call(
                    db=db,
                    session_id=session_id,
                    visit_no=cur_visit,
                    messages_this_visit=visit_messages,
                )
            except QuotaExhaustedError as exc:
                return _error(str(exc), 429)

        new_visit = cur_visit + 1
        session_repo.end_visit(session_id, new_visit_no=new_visit, reset_turn_no=True)
        updated_session = session_repo.get_for_user(session_id, doctor_id)
        if not updated_session:
            return _error("Session not found after update", 404)

        state = graph_state_from_session(updated_session)
        state.doctor_level = level
        return jsonify(
            {
                "session_id": session_id,
                "visit_no": new_visit,
                "summary_text": summary_text,
                "updated_state": state.model_dump(mode="json"),
            }
        )

    @app.route(f"{API_PREFIX}/sessions/<session_id>/history", methods=["GET"])
    def session_history(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_doc = session_repo.get_for_user(session_id, doctor_id)
        if not session_doc:
            return _error("Session not found", 404)

        n_raw = request.args.get("n", "").strip()
        n = 20
        if n_raw:
            try:
                n = max(1, int(n_raw))
            except ValueError:
                return _error("n must be an integer", 400)

        msgs = message_repo.list_last(session_id, n=n)
        return jsonify({"session_id": session_id, "messages": _serialize_messages(msgs)})

    @app.route(f"{API_PREFIX}/user_case_progress", methods=["GET"])
    def user_case_progress() -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        case_id = (request.args.get("case_id") or "").strip() or None
        try:
            if case_id:
                row = progress_repo.get_for_case(doctor_id, case_id)
                return jsonify({"item": _serialize_row(row) if row else None})
            rows = progress_repo.list_for_user(doctor_id)
            return jsonify({"items": [_serialize_row(r) for r in rows]})
        except Exception as exc:
            return _error(f"Unable to load progress: {exc}", 500)

    @app.route(f"{API_PREFIX}/cases/<case_id>/progress", methods=["GET"])
    def case_progress(case_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])
        case_id = (case_id or "").strip()
        if not case_id:
            return _error("Missing case_id", 400)

        row = None
        try:
            row = progress_repo.get_for_case(doctor_id, case_id)
        except Exception:
            row = None
        if not row:
            try:
                completed = session_repo.find_latest_completed_for_case(doctor_id, case_id)
            except Exception:
                completed = None

            if completed and completed.get("session_id"):
                session_id = str(completed["session_id"])
                try:
                    progress_repo.mark_solved(doctor_id, case_id, session_id=session_id)
                except Exception:
                    pass
                return jsonify(
                    {
                        "case_id": case_id,
                        "status": "SOLVED",
                        "solved_session_id": session_id,
                        "last_session_id": session_id,
                    }
                )

            session_doc = None
            try:
                session_doc = session_repo.find_latest_for_case(doctor_id, case_id)
            except Exception:
                session_doc = None

            if not session_doc or not session_doc.get("session_id"):
                return jsonify(
                    {"case_id": case_id, "status": "NOT_STARTED", "solved_session_id": None, "last_session_id": None}
                )

            session_id = str(session_doc["session_id"])
            return jsonify(
                {
                    "case_id": case_id,
                    "status": "IN_PROGRESS",
                    "solved_session_id": None,
                    "last_session_id": session_id,
                }
            )
        return jsonify(
            {
                "case_id": case_id,
                "status": str(row.get("status") or "NOT_STARTED"),
                "solved_session_id": row.get("solved_session_id"),
                "last_session_id": row.get("last_session_id"),
            }
        )

    @app.route(f"{API_PREFIX}/sessions/<session_id>/complete", methods=["POST"])
    def complete_session(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        payload = _parse_json_body()
        if payload is None:
            return _error("Invalid JSON body", 400)
        make_public_raw = payload.get("make_public", None)
        make_public: Optional[bool] = None
        if make_public_raw is not None:
            if not isinstance(make_public_raw, bool):
                return _error("make_public must be a boolean", 400)
            make_public = make_public_raw

        session_row = session_repo.get_row_for_user(session_id, doctor_id)
        if not session_row:
            return _error("Session not found", 404)

        case_id = str(session_row.get("case_id") or "").strip()
        user_id = str(session_row.get("user_id") or "").strip()
        if not case_id or not user_id:
            return _error("Session is missing case_id/user_id", 500)

        updated_session = session_repo.close_for_user(session_id, doctor_id, make_public=make_public)
        if not updated_session:
            return _error("Session not found", 404)

        try:
            progress_repo.mark_solved(doctor_id, case_id, session_id=session_id)
        except Exception:
            pass

        messages = []
        summaries = []
        try:
            messages = message_repo.list_all(session_id)
        except Exception:
            messages = message_repo.list_last(session_id, n=500)
        try:
            summaries = summary_repo.list_for_session(session_id)
        except Exception:
            summaries = []

        artifact = None
        try:
            artifact_payload = {
                "case_id": case_id,
                "session_id": session_id,
                "user_id": user_id,
                "ended_at": _coerce_datetime(updated_session.get("ended_at")),
                "messages": _serialize_messages(messages),
                "summaries": _serialize_sessions(summaries),
            }
            artifact = artifact_repo.create_if_missing(
                session_id=session_id,
                user_id=user_id,
                case_id=case_id,
                payload=artifact_payload,
            )
        except Exception:
            artifact = None

        progress_row = None
        try:
            progress_row = progress_repo.get_for_case(doctor_id, case_id)
        except Exception:
            progress_row = None

        return jsonify(
            {
                "session": _serialize_row(updated_session),
                "progress": _serialize_row(progress_row) if progress_row else None,
                "artifact": _serialize_row(artifact) if isinstance(artifact, dict) else None,
            }
        )

    @app.route(f"{API_PREFIX}/sessions/<session_id>/end", methods=["POST"])
    def end_session(session_id: str) -> Any:
        return complete_session(session_id)

    @app.route(f"{API_PREFIX}/sessions/<session_id>/visibility", methods=["POST"])
    def set_session_visibility(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        payload = _parse_json_body()
        if payload is None:
            return _error("Invalid JSON body", 400)
        if "is_public" not in payload:
            return _error("Missing is_public", 400)
        if not isinstance(payload.get("is_public"), bool):
            return _error("is_public must be a boolean", 400)
        is_public = bool(payload.get("is_public"))

        session_row = session_repo.get_row_for_user(session_id, doctor_id)
        if not session_row:
            return _error("Session not found", 404)
        if str(session_row.get("status") or "active") != "closed":
            return _error("Session must be completed before changing visibility.", 409)

        updated = session_repo.set_visibility_for_user(session_id, doctor_id, is_public=is_public)
        if not updated:
            return _error("Unable to update visibility", 500)
        return jsonify({"session": _serialize_row(updated)})

    @app.route(f"{API_PREFIX}/sessions/<session_id>/public", methods=["PATCH"])
    def patch_session_public(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        payload = _parse_json_body()
        if payload is None:
            return _error("Invalid JSON body", 400)
        if "is_public" not in payload:
            return _error("Missing is_public", 400)
        if not isinstance(payload.get("is_public"), bool):
            return _error("is_public must be a boolean", 400)
        is_public = bool(payload.get("is_public"))

        session_row = session_repo.get_row_for_user(session_id, doctor_id)
        if not session_row:
            return _error("Session not found", 404)
        if str(session_row.get("status") or "active").lower() not in {"closed", "completed"}:
            return _error("Session must be completed before changing visibility.", 409)

        updated = session_repo.set_visibility_for_user(session_id, doctor_id, is_public=is_public)
        if not updated:
            return _error("Unable to update visibility", 500)
        return jsonify({"session": _serialize_row(updated)})

    @app.route(f"{API_PREFIX}/cases/<case_id>/community_submissions", methods=["GET"])
    def community_submissions(case_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])
        case_id = (case_id or "").strip()
        if not case_id:
            return _error("Missing case_id", 400)

        limit_raw = (request.args.get("limit") or "").strip()
        limit = 50
        if limit_raw:
            try:
                limit = max(1, min(100, int(limit_raw)))
            except ValueError:
                return _error("limit must be an integer", 400)

        viewer_user_id = normalize_user_id(doctor_id)
        rows = session_repo.list_public_submissions_for_case(case_id, limit=limit, offset=0)
        author_ids = [str(row.get("user_id") or "") for row in rows if isinstance(row, dict)]
        display_names = doctor_repo.get_display_names(author_ids)
        usernames = user_repo.get_usernames(author_ids)
        items: List[Dict[str, Any]] = []
        for row in rows:
            user_id = str(row.get("user_id") or "")
            if user_id and user_id == viewer_user_id:
                continue
            author_name = display_names.get(user_id) or (f"User {user_id[:8]}" if user_id else "User")
            author_username = usernames.get(user_id) or (_synthetic_username(user_id) if user_id else "")
            items.append(
                {
                    "session_id": str(row.get("session_id")),
                    "created_at": row.get("created_at"),
                    "ended_at": row.get("ended_at"),
                    "author_display_name": author_name,
                    "author_username": author_username,
                }
            )
        return jsonify({"case_id": case_id, "items": _serialize_sessions(items)})

    @app.route(f"{API_PREFIX}/cases/<case_id>/submissions", methods=["GET"])
    def list_case_submissions(case_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])
        case_id = (case_id or "").strip()
        if not case_id:
            return _error("Missing case_id", 400)

        limit_raw = (request.args.get("limit") or "").strip()
        limit = 50
        if limit_raw:
            try:
                limit = max(1, min(100, int(limit_raw)))
            except ValueError:
                return _error("limit must be an integer", 400)

        viewer_user_id = normalize_user_id(doctor_id)
        rows = session_repo.list_public_submissions_for_case(case_id, limit=limit, offset=0)
        author_ids = [str(row.get("user_id") or "") for row in rows if isinstance(row, dict)]
        display_names = doctor_repo.get_display_names(author_ids)
        usernames = user_repo.get_usernames(author_ids)
        items: List[Dict[str, Any]] = []
        for row in rows:
            user_id = str(row.get("user_id") or "")
            if user_id and user_id == viewer_user_id:
                continue
            sid = str(row.get("session_id") or "")
            try:
                message_count = message_repo.count_for_session(sid) if sid else 0
            except Exception:
                message_count = 0
            items.append(
                {
                    "session_id": sid,
                    "author_name": display_names.get(user_id) or (f"User {user_id[:8]}" if user_id else "User"),
                    "author_username": usernames.get(user_id) or (_synthetic_username(user_id) if user_id else ""),
                    "ended_at": row.get("ended_at"),
                    "message_count": int(message_count),
                }
            )
        return jsonify({"case_id": case_id, "items": _serialize_sessions(items)})

    @app.route(f"{API_PREFIX}/submissions/<session_id>", methods=["GET"])
    def get_submission(session_id: str) -> Any:
        doctor_id, err = require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_row = session_repo.get_row(session_id)
        if not session_row:
            return _error("Submission not found", 404)

        owner_row = session_repo.get_row_for_user(session_id, doctor_id)
        is_owner = owner_row is not None
        status = str(session_row.get("status") or "active")
        is_public = bool(session_row.get("is_public", False))
        is_completed = status.lower() in {"closed", "completed"}
        if not is_owner:
            if (not is_completed) or (not is_public):
                return _error("Submission not found", 404)

        user_id = str(session_row.get("user_id") or "")
        case_id = str(session_row.get("case_id") or "")
        author_display_name = ""
        author_username = ""
        if not is_owner:
            names = doctor_repo.get_display_names([user_id] if user_id else [])
            author_display_name = names.get(user_id) or (f"User {user_id[:8]}" if user_id else "User")
            author_username = user_repo.get_usernames([user_id]).get(user_id) or (_synthetic_username(user_id) if user_id else "")
        session_doc = {
            "session_id": str(session_row.get("session_id") or session_id),
            "case_id": case_id,
            "status": status,
            "is_public": is_public,
            "ended_at": session_row.get("ended_at"),
            "created_at": session_row.get("created_at"),
            "updated_at": session_row.get("updated_at"),
        }
        messages = message_repo.list_all(session_id)
        summaries = summary_repo.list_for_session(session_id)

        return jsonify(
            {
                "session": _serialize_row(session_doc),
                "author_display_name": "You" if is_owner else author_display_name,
                "author_username": None if is_owner else author_username,
                "messages": _serialize_messages(messages),
                "summaries": _serialize_sessions(summaries),
                "viewer_can_toggle_visibility": bool(is_owner and is_completed),
                "case_id": case_id,
            }
        )

    @app.route(f"{API_PREFIX}/health", methods=["GET"])
    def health() -> Any:
        check = request.args.get("check", "").strip().lower()
        if check in {"supabase", "db"}:
            try:
                ping()
            except Exception as exc:
                return jsonify({"status": "error", "supabase": "error", "message": str(exc)}), 500
            return jsonify({"status": "ok", "supabase": "ok"})
        return jsonify({"status": "ok"})

    @app.route(f"{API_PREFIX}/cases", methods=["GET"])
    def list_cases() -> Any:
        search = request.args.get("search", "").strip() or None
        difficulty = request.args.get("difficulty", "").strip() or None
        tag = request.args.get("tag", "").strip() or None
        sort = request.args.get("sort", "").strip() or None

        page_raw = request.args.get("page", "").strip()
        limit_raw = request.args.get("limit", "").strip()
        page = 1
        limit = 20
        if page_raw:
            try:
                page = int(page_raw)
            except ValueError:
                return _error("page must be an integer", 400)
            if page < 1:
                return _error("page must be >= 1", 400)
        if limit_raw:
            try:
                limit = int(limit_raw)
            except ValueError:
                return _error("limit must be an integer", 400)
            if limit < 1:
                return _error("limit must be >= 1", 400)
            limit = min(limit, 100)

        if difficulty and difficulty not in _ALLOWED_DIFFICULTIES:
            return _error('difficulty must be one of "Easy","Medium","Hard"', 400)
        if sort and sort not in {"difficulty", "difficulty_asc", "difficulty_desc", "title"}:
            return _error('sort must be one of "title","difficulty","difficulty_asc","difficulty_desc"', 400)
        if search and len(search) > 200:
            return _error("search is too long (max 200 chars)", 400)
        if tag and len(tag) > 100:
            return _error("tag is too long (max 100 chars)", 400)
        if tag and any(ch in tag for ch in "{}(),"):
            return _error("tag contains unsupported characters", 400)

        try:
            result = case_repo.list_cases(
                search=search,
                difficulty=difficulty,
                tag=tag,
                page=page,
                limit=limit,
                sort=sort,
                published_only=True,
            )
            return jsonify(result)
        except SupabaseError:
            return _error("Backend database is unavailable. Try again shortly.", 503)
        except Exception as exc:
            return _error(f"Unable to load cases: {exc}", 500)

    @app.route(f"{API_PREFIX}/cases/<case_id>", methods=["GET"])
    def get_case(case_id: str) -> Any:
        case_id = (case_id or "").strip()
        if not case_id:
            return _error("Missing case_id", 400)

        try:
            rows = db.table("cases").select(
                filters={"case_id": case_id},
                columns="case_id,title,difficulty,tags,short_prompt,estimated_time_min,version,is_published,seed",
                limit=1,
            )
        except Exception as exc:
            return _error(f"Unable to load case: {exc}", 500)

        if not rows or not isinstance(rows[0], dict):
            return _error("Case not found", 404)
        row = rows[0]

        seed = row.get("seed") if isinstance(row.get("seed"), dict) else {}
        presentation = ""
        chunks = seed.get("chunks") if isinstance(seed, dict) else None
        if isinstance(chunks, list) and chunks:
            first = chunks[0] if isinstance(chunks[0], dict) else None
            if first and first.get("content"):
                presentation = str(first.get("content") or "").strip()
        if not presentation:
            presentation = str(row.get("short_prompt") or "").strip()

        return jsonify(
            {
                "case": _serialize_row(
                    {
                        "case_id": row.get("case_id"),
                        "title": row.get("title", ""),
                        "difficulty": row.get("difficulty", "Easy"),
                        "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
                        "short_prompt": row.get("short_prompt", ""),
                        "estimated_time_min": int(row.get("estimated_time_min") or 15),
                        "version": int(row.get("version") or 1),
                        "is_published": bool(row.get("is_published", True)),
                        "patient_presentation": presentation,
                    }
                )
            }
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
