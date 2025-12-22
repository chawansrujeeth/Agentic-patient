from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request

from db import get_db, ping
from graph_state import GraphState
from llm import QuotaExhaustedError
from models import ensure_indexes, seed_cases_if_missing
from policy import max_visits
from repos import CaseRepo, DoctorRepo, MessageRepo, SessionRepo
from session_utils import graph_state_from_session

K_MSGS = int(os.getenv("CONTEXT_LAST_K_MSGS", "12"))
DEFAULT_SESSION_LIST_LIMIT = int(os.getenv("SESSIONS_LIST_LIMIT", "50"))
API_PREFIX = "/api"


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

    # Boot hooks similar to CLI startup.
    db = get_db()
    if init_db:
        ping()
        ensure_indexes(db)
    if seed_cases:
        seed_cases_if_missing(db)

    case_repo = CaseRepo(db)
    doctor_repo = DoctorRepo(db)
    session_repo = SessionRepo(db)
    message_repo = MessageRepo(db)
    from graph import build_visit_graph, compose_visit_intro, make_persist_patient_intro

    visit_graph = build_visit_graph(db, use_checkpointer=False)

    @app.route(f"{API_PREFIX}/sessions", methods=["POST"])
    def create_session() -> Any:
        doctor_id, err = _require_doctor_id()
        if err:
            return _error(err[0], err[1])

        payload = _parse_json_body()
        if payload is None:
            return _error("Invalid JSON body", 400)
        case_id = str(payload.get("case_id", "")).strip()
        if not case_id:
            return _error("Missing case_id", 400)

        case_doc = case_repo.get(case_id)
        if not case_doc:
            return _error(f"Case not found: {case_id}", 404)

        session_doc = session_repo.create(doctor_id=doctor_id, case_id=case_id)
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
                    "state": state.model_dump(mode="json"),
                    "last_messages": _serialize_messages(last_messages),
                }
            ),
            201,
        )

    @app.route(f"{API_PREFIX}/sessions", methods=["GET"])
    def list_sessions() -> Any:
        doctor_id, err = _require_doctor_id()
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
        doctor_id, err = _require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_doc = session_repo.get_for_user(session_id, doctor_id)
        if not session_doc:
            return _error("Session not found", 404)

        state = graph_state_from_session(session_doc)
        doctor = doctor_repo.get_or_create(doctor_id, level=0)
        state.doctor_level = int(doctor.get("level", 0))
        last_messages = message_repo.list_last(session_id, n=K_MSGS)

        return jsonify(
            {
                "session_id": session_doc["session_id"],
                "state": state.model_dump(mode="json"),
                "last_messages": _serialize_messages(last_messages),
            }
        )

    @app.route(f"{API_PREFIX}/sessions/<session_id>/send", methods=["POST"])
    def send_message(session_id: str) -> Any:
        doctor_id, err = _require_doctor_id()
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
        doctor_id, err = _require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_doc = session_repo.get_for_user(session_id, doctor_id)
        if not session_doc:
            return _error("Session not found", 404)

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
        doctor_id, err = _require_doctor_id()
        if err:
            return _error(err[0], err[1])

        session_doc = session_repo.get_for_user(session_id, doctor_id)
        if not session_doc:
            return _error("Session not found", 404)

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
        doctor_id, err = _require_doctor_id()
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

    @app.route(f"{API_PREFIX}/health", methods=["GET"])
    def health() -> Any:
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
