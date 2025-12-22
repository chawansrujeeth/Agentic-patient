from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeInterrupt
from langgraph.graph import END, StateGraph

from graph_state import GraphState
from guardrails import apply_guardrails
from llm import call_patient_agent
from policy import allowed_tools, max_detail_depth
from prompting import build_allowed_facts, build_prompt_with_retrieval
from rag import retrieve_context, store_message_embedding
from repos import CaseRepo, DoctorRepo, MessageRepo, SessionRepo, SummaryRepo, TurnRepo
from summarize import summarize_visit_one_call

K_FACTS = int(os.getenv("CONTEXT_MAX_FACTS", "25"))
K_RECENT_MSGS = int(os.getenv("CONTEXT_LAST_K_MSGS", "20"))
REGEN_ON_REJECT = os.getenv("LLM_REGEN_ON_REJECT", "1").lower() not in ("0", "false", "no")
_TEST_REQUEST_PATTERNS = (
    r"\btest(s)?\b",
    r"\blab(s)?\b",
    r"\bblood\s*work\b",
    r"\bx[- ]?ray\b",
    r"\bmri\b",
    r"\bct\b",
    r"\bultrasound\b",
    r"\bculture\b",
    r"\bpanel\b",
    r"\bswab\b",
    r"\brapid\b",
)
_EXAM_REQUEST_PATTERNS = (
    r"\bexam\b",
    r"\bphysical\b",
    r"\bexamine\b",
)
_MED_REQUEST_PATTERNS = (
    r"\bmed(s)?\b",
    r"\bmedication(s)?\b",
    r"\bprescrib(e|ing|ed)?\b",
    r"\brx\b",
)
_FOLLOWUP_REQUEST_PATTERNS = (
    r"\bfollow[- ]?up\b",
    r"\bcome back\b",
    r"\breturn\b",
    r"\bsee you\b",
    r"\bcheck in\b",
    r"\bfollow up visit\b",
)


def _empty_context() -> Dict[str, Any]:
    return {"summaries": [], "messages": [], "case_chunks": []}


def _build_prompt(state: GraphState) -> str:
    recent_conversation = state.retrieved_context.get("recent_conversation") if state.retrieved_context else []
    if not isinstance(recent_conversation, list):
        recent_conversation = []
    return build_prompt_with_retrieval(
        visit_no=state.visit_number,
        level=state.doctor_level,
        doctor_message=state.last_doctor_message or "",
        allowed_facts=state.allowed_facts,
        recent_conversation=recent_conversation,
        already_disclosed_fact_ids=state.disclosed_fact_ids,
        retrieved=state.retrieved_context,
        case_type=state.case_type,
        last_visit_summary=state.last_visit_summary,
    )


def load_state(state: GraphState, db) -> GraphState:
    doctor_repo = DoctorRepo(db)
    case_repo = CaseRepo(db)
    session_repo = SessionRepo(db)
    summary_repo = SummaryRepo(db)

    session = session_repo.get(state.session_id)
    if not session:
        raise RuntimeError(f"Session not found: {state.session_id}")
    doctor = doctor_repo.get_or_create(state.doctor_id, level=0)
    case = case_repo.get(state.case_id)
    if not case:
        raise RuntimeError(f"Case not found: {state.case_id}")
    state.case_type = str(case.get("case_type", "") or "")

    state.visit_number = int(session.get("visit_no", state.visit_number))
    stored_turn_no = int(session.get("turn_no", 0))
    state.turn_in_visit = max(0, stored_turn_no // 2)
    if stored_turn_no > 0:
        state.is_new_visit = False
    state.status = str(session.get("status", state.status))
    state.disclosed_fact_ids = list(session.get("disclosed_fact_ids", []))
    state.performed_exams = list(session.get("performed_exams", []))
    state.performed_tests = list(session.get("performed_tests", []))
    state.doctor_level = int(doctor.get("level", 0))
    state.allowed_facts = build_allowed_facts(
        case_doc=case,
        level=state.doctor_level,
        visit_no=state.visit_number,
        disclosed_fact_ids=state.disclosed_fact_ids,
        max_facts=K_FACTS,
    )
    if state.visit_number > 1:
        prior_summary = summary_repo.get_visit(state.session_id, state.visit_number - 1)
        if prior_summary:
            summary_text = str(prior_summary.get("summary_text", "")).strip()
            state.last_visit_summary = summary_text or None
    state.retrieved_context = _empty_context()
    state.llm_attempts = 0
    state.doctor_turn_no = None
    state.turn_id = None
    return state


def load_state_for_visit(state: GraphState, db) -> GraphState:
    return load_state(state, db)


def retrieve_context_node(state: GraphState, db) -> GraphState:
    if not state.last_doctor_message:
        state.retrieved_context = _empty_context()
        return state

    try:
        ctx = retrieve_context(
            session_id=state.session_id,
            query=state.last_doctor_message,
            db=db,
            visit_no=state.visit_number,
        )
    except Exception:
        ctx = _empty_context()
    try:
        message_repo = MessageRepo(db)
        recent_msgs = message_repo.list_last(state.session_id, n=K_RECENT_MSGS)
        ctx["recent_conversation"] = [
            {"role": m.get("role"), "text": m.get("content", "")} for m in recent_msgs
        ]
    except Exception:
        ctx["recent_conversation"] = []
    state.retrieved_context = ctx
    return state


def generate_patient_response(state: GraphState) -> GraphState:
    if not state.last_doctor_message:
        return state

    prompt = _build_prompt(state)
    res = call_patient_agent(prompt)
    state.llm_attempts = 1
    state.patient_utterance = res.parsed.patient_utterance
    state.new_disclosed_fact_ids = list(res.parsed.new_disclosed_fact_ids)
    state.safety_flags = list(res.parsed.safety_flags)
    state.visit_end_recommendation = bool(res.parsed.visit_end_recommendation)
    state.requested_clarifications = res.parsed.requested_clarifications
    state.llm_usage = res.usage
    state.raw_llm_output = res.raw_text
    state.guardrail_rejected = False
    return state


def validate_response(state: GraphState) -> GraphState:
    if not state.last_doctor_message:
        return state

    decision = apply_guardrails(
        resp=state,
        allowed_facts=state.allowed_facts,
        already_disclosed_fact_ids=state.disclosed_fact_ids,
        mode="reject_once_else_strip",
    )

    state.patient_utterance = decision.patient_utterance
    state.new_disclosed_fact_ids = decision.new_disclosed_fact_ids
    state.safety_flags = decision.safety_flags
    state.guardrail_rejected = decision.rejected

    if decision.rejected and REGEN_ON_REJECT and state.llm_attempts < 2:
        regen_prompt = _build_prompt(state) + (
            "\n\nSYSTEM: Your prior output violated policy (disallowed fact IDs). Regenerate strictly."
        )
        res2 = call_patient_agent(regen_prompt)
        prior_usage = state.llm_usage
        state.llm_attempts += 1
        state.patient_utterance = res2.parsed.patient_utterance
        state.new_disclosed_fact_ids = list(res2.parsed.new_disclosed_fact_ids)
        state.safety_flags = list(res2.parsed.safety_flags)
        state.visit_end_recommendation = bool(res2.parsed.visit_end_recommendation)
        state.requested_clarifications = res2.parsed.requested_clarifications
        state.raw_llm_output = res2.raw_text
        state.llm_usage = {"attempt1": prior_usage, "attempt2": res2.usage}
        second_decision = apply_guardrails(
            resp=state,
            allowed_facts=state.allowed_facts,
            already_disclosed_fact_ids=state.disclosed_fact_ids,
            mode="strip_only",
        )
        state.patient_utterance = second_decision.patient_utterance
        state.new_disclosed_fact_ids = second_decision.new_disclosed_fact_ids
        state.safety_flags = second_decision.safety_flags
        state.guardrail_rejected = second_decision.rejected

    if _is_viral_case(state.case_type) and _looks_like_followup_request(state.last_doctor_message):
        if not state.visit_end_recommendation:
            state.visit_end_recommendation = True
        if state.patient_utterance and "thank" not in state.patient_utterance.lower():
            state.patient_utterance = _append_thanks(state.patient_utterance)

    return state


def _looks_like_test_request(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(re.search(pat, lowered) for pat in _TEST_REQUEST_PATTERNS)


def _looks_like_exam_request(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(re.search(pat, lowered) for pat in _EXAM_REQUEST_PATTERNS)


def _looks_like_medicine_request(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(re.search(pat, lowered) for pat in _MED_REQUEST_PATTERNS)


def _looks_like_followup_request(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(re.search(pat, lowered) for pat in _FOLLOWUP_REQUEST_PATTERNS)


def _is_viral_case(case_type: Optional[str]) -> bool:
    return bool(case_type and str(case_type).strip().lower().startswith("viral"))


def _append_thanks(text: str) -> str:
    sentence_count = len([s for s in re.split(r"[.!?]+", text) if s.strip()])
    if sentence_count >= 3:
        return text
    return f"{text.rstrip()} Thanks."


def _apply_test_tool_response(state: GraphState, allowed: bool) -> None:
    if allowed:
        state.patient_utterance = "OK, I'll get that test ordered and will get back with the results."
    else:
        state.patient_utterance = "I'm sorry, that test isn't permitted right now."
    state.new_disclosed_fact_ids = []
    state.requested_clarifications = None
    state.visit_end_recommendation = False
    state.should_end_visit = False


def _apply_exam_tool_response(state: GraphState, allowed: bool) -> None:
    if allowed:
        state.patient_utterance = "OK, we can do that exam now."
    else:
        state.patient_utterance = "I'm sorry, that exam isn't available right now."
    state.new_disclosed_fact_ids = []
    state.requested_clarifications = None
    state.visit_end_recommendation = False
    state.should_end_visit = False


def _apply_medicine_tool_response(state: GraphState) -> None:
    state.patient_utterance = "I'm not able to discuss medications or prescriptions right now."
    state.new_disclosed_fact_ids = []
    state.requested_clarifications = None
    state.visit_end_recommendation = False
    state.should_end_visit = False


def tool_check_tests(state: GraphState, db) -> GraphState:
    if not _looks_like_test_request(state.last_doctor_message):
        return state

    case_repo = CaseRepo(db)
    case_doc = case_repo.get(state.case_id)
    if not case_doc:
        _apply_test_tool_response(state, allowed=False)
        return state

    allowed_facts = build_allowed_facts(
        case_doc=case_doc,
        level=state.doctor_level,
        visit_no=state.visit_number,
        disclosed_fact_ids=state.disclosed_fact_ids,
        max_facts=K_FACTS,
    )
    allowed_tests = [fact for fact in allowed_facts if fact.get("kind") == "tests"]
    _apply_test_tool_response(state, allowed=bool(allowed_tests))
    return state


def tool_precheck(state: GraphState, db) -> GraphState:
    text = state.last_doctor_message
    if not text:
        state.should_call_llm = True
        return state

    handled = False
    allowed = False
    kind = None

    if _looks_like_test_request(text):
        kind = "tests"
        handled = True
    elif _looks_like_exam_request(text):
        kind = "exam"
        handled = True
    elif _looks_like_medicine_request(text):
        if _is_viral_case(state.case_type):
            state.should_call_llm = True
            return state
        handled = True

    if not handled:
        state.should_call_llm = True
        return state

    if kind:
        tool_set = allowed_tools(state.doctor_level, state.visit_number)
        if kind in tool_set:
            allowed_facts = state.allowed_facts
            if not allowed_facts:
                case_repo = CaseRepo(db)
                case_doc = case_repo.get(state.case_id)
                if case_doc:
                    allowed_facts = build_allowed_facts(
                        case_doc=case_doc,
                        level=state.doctor_level,
                        visit_no=state.visit_number,
                        disclosed_fact_ids=state.disclosed_fact_ids,
                        max_facts=K_FACTS,
                    )
            if allowed_facts:
                allowed = any(fact.get("kind") == kind for fact in allowed_facts)

        if kind == "tests":
            _apply_test_tool_response(state, allowed=allowed)
        elif kind == "exam":
            _apply_exam_tool_response(state, allowed=allowed)
    else:
        _apply_medicine_tool_response(state)

    state.patient_core_response = state.patient_utterance
    state.response_source = "tool"
    state.should_call_llm = False
    state.llm_attempts = 0
    state.llm_usage = None
    state.raw_llm_output = None
    state.guardrail_rejected = False
    state.safety_flags.clear()
    return state


def compose_visit_intro(state: GraphState) -> GraphState:
    if not state.is_new_visit:
        return state

    if int(state.visit_number) == 1:
        intro = (
            "Hi, thanks for seeing me today. "
            "I'm the patient for this case, and my main concern is that I've been feeling unwell lately. "
            "Please ask me any questions you need so I can give you the full picture."
        )
    else:
        summary = (state.last_visit_summary or "").strip()
        if summary:
            if summary[-1] not in ".!?":
                summary = f"{summary}."
            intro = (
                "Welcome back. "
                f"Here's a brief recap from last time: {summary} "
                "Today I'd like to discuss what we should focus on next."
            )
        else:
            intro = (
                "Welcome back. "
                "I don't have a summary from our last visit, but this is a follow-up. "
                "Today I'd like to discuss what we should focus on next."
            )

    state.response_source = "system_intro"
    state.patient_core_response = intro
    state.patient_utterance = intro
    return state


def make_persist_patient_intro(db):
    def persist_patient_intro(state: GraphState) -> GraphState:
        if not state.is_new_visit:
            return state
        if not (state.patient_utterance and state.patient_utterance.strip()):
            raise ValueError("persist_patient_intro called without a patient intro utterance")

        message_repo = MessageRepo(db)
        turn_repo = TurnRepo(db)

        turn_id = f"{state.session_id}:v{state.visit_number}:t0"
        turn_doc = turn_repo.start_turn(
            turn_id=turn_id,
            session_id=state.session_id,
            visit_no=state.visit_number,
            doctor_turn_no=0,
        )
        if turn_doc.get("status") == "persisted":
            state.turn_in_visit = 0
            state.is_new_visit = False
            return state

        try:
            message_repo.upsert_turn_message(
                turn_id=turn_id,
                session_id=state.session_id,
                visit_no=state.visit_number,
                turn_no=0,
                role="patient",
                content=state.patient_utterance or "",
                meta={"response_source": "system_intro", "is_visit_intro": True},
            )
        except Exception:
            turn_repo.mark_status(turn_id, "failed")
            raise

        turn_repo.mark_status(turn_id, "persisted")
        state.turn_in_visit = 0
        state.is_new_visit = False
        return state

    return persist_patient_intro


def await_doctor_message(state: GraphState) -> GraphState:
    if state.last_doctor_message and state.last_doctor_message.strip():
        return state
    raise NodeInterrupt({"type": "await_doctor_message"})


def reset_turn_outputs(state: GraphState) -> GraphState:
    state.reset_turn_outputs()
    return state


def greeting_node(state: GraphState) -> GraphState:
    """
    Prepend a one-time greeting + introduction at the start of the first visit.
    """

    # First session/visit may be encoded as 0 or 1 depending on the caller/DB defaults.
    if int(state.visit_number) not in (0, 1):
        return state
    if int(state.turn_in_visit) != 0:
        return state

    greeting = (
        "Hi — thanks for seeing me today. I'm your patient for this case; I'll answer your questions as best I can."
    )
    if state.patient_utterance:
        state.patient_utterance = f"{greeting}\n\n{state.patient_utterance}"
    else:
        state.patient_utterance = greeting
    return state


def _collect_doc_ids(values: Optional[List[Dict[str, Any]]]) -> List[str]:
    if not values:
        return []
    return [str(v.get("doc_id")) for v in values if v.get("doc_id")]


def persist_turn(state: GraphState, db) -> GraphState:
    if not state.last_doctor_message:
        return state

    session_repo = SessionRepo(db)
    message_repo = MessageRepo(db)
    case_repo = CaseRepo(db)
    turn_repo = TurnRepo(db)

    current_turn = int(state.turn_in_visit or 0)
    doctor_turn_no: Optional[int] = None
    session_doc = session_repo.get(state.session_id)
    if session_doc:
        stored_turn_no = int(session_doc.get("turn_no", current_turn * 2))
        current_turn = max(current_turn, stored_turn_no // 2)
        doctor_turn_no = stored_turn_no + 1
    if state.doctor_turn_no is not None:
        if doctor_turn_no is None:
            doctor_turn_no = int(state.doctor_turn_no)
        else:
            doctor_turn_no = max(int(state.doctor_turn_no), doctor_turn_no)
    if doctor_turn_no is None or doctor_turn_no <= 0:
        doctor_turn_no = current_turn * 2 + 1
    patient_turn_no = doctor_turn_no + 1
    turn_id = state.turn_id or f"{state.session_id}:v{state.visit_number}:t{doctor_turn_no}"
    state.doctor_turn_no = doctor_turn_no
    state.turn_id = turn_id

    turn_doc = turn_repo.start_turn(
        turn_id=turn_id,
        session_id=state.session_id,
        visit_no=state.visit_number,
        doctor_turn_no=doctor_turn_no,
    )
    if turn_doc.get("status") == "persisted":
        refreshed = session_repo.get(state.session_id)
        if refreshed:
            stored_turn_no = int(refreshed.get("turn_no", current_turn * 2))
            state.turn_in_visit = max(0, stored_turn_no // 2)
            state.visit_number = int(refreshed.get("visit_no", state.visit_number))
            state.disclosed_fact_ids = list(refreshed.get("disclosed_fact_ids", state.disclosed_fact_ids))
            state.performed_exams = list(refreshed.get("performed_exams", state.performed_exams))
            state.performed_tests = list(refreshed.get("performed_tests", state.performed_tests))
        state.last_doctor_message = ""
        return state

    case_doc = case_repo.get(state.case_id) or {"chunks": []}
    chunk_index = {ch.get("chunk_id"): ch for ch in case_doc.get("chunks", []) if ch.get("chunk_id")}

    try:
        message_repo.upsert_turn_message(
            turn_id=turn_id,
            session_id=state.session_id,
            visit_no=state.visit_number,
            turn_no=doctor_turn_no,
            role="doctor",
            content=state.last_doctor_message or "",
            meta={"turn_id": turn_id},
        )
        try:
            if state.last_doctor_message:
                store_message_embedding(
                    session_id=state.session_id,
                    visit_no=state.visit_number,
                    turn_no=doctor_turn_no,
                    role="doctor",
                    text=state.last_doctor_message,
                    db=db,
                )
        except Exception:
            pass

        retrieval_doc_ids = {
            "summaries": _collect_doc_ids(state.retrieved_context.get("summaries")),
            "messages": _collect_doc_ids(state.retrieved_context.get("messages")),
            "case_chunks": _collect_doc_ids(state.retrieved_context.get("case_chunks")),
        }
        message_repo.upsert_turn_message(
            turn_id=turn_id,
            session_id=state.session_id,
            visit_no=state.visit_number,
            turn_no=patient_turn_no,
            role="patient",
            content=state.patient_utterance or "",
            meta={
                "turn_id": turn_id,
                "new_fact_ids": list(state.new_disclosed_fact_ids),
                "allowed_tools": sorted(list(allowed_tools(state.doctor_level, state.visit_number))),
                "max_depth": max_detail_depth(state.doctor_level, state.visit_number),
                "requested_clarifications": state.requested_clarifications,
                "visit_end_recommendation": state.visit_end_recommendation,
                "safety_flags": list(state.safety_flags),
                "guardrail_rejected": state.guardrail_rejected,
                "llm_usage": state.llm_usage,
                "raw_llm_output": state.raw_llm_output,
                "retrieved_doc_ids": retrieval_doc_ids,
                "retrieved_summary_ids": retrieval_doc_ids["summaries"],
            },
        )
        try:
            if state.patient_utterance:
                store_message_embedding(
                    session_id=state.session_id,
                    visit_no=state.visit_number,
                    turn_no=patient_turn_no,
                    role="patient",
                    text=state.patient_utterance,
                    db=db,
                )
        except Exception:
            pass

        for fid in state.new_disclosed_fact_ids:
            if fid not in state.disclosed_fact_ids:
                state.disclosed_fact_ids.append(fid)
            chunk = chunk_index.get(fid)
            if not chunk:
                continue
            kind = chunk.get("kind")
            if kind == "exam" and fid not in state.performed_exams:
                state.performed_exams.append(fid)
            if kind == "tests" and fid not in state.performed_tests:
                state.performed_tests.append(fid)

        session_repo.update_ledger(
            state.session_id,
            state.disclosed_fact_ids,
            state.performed_exams,
            state.performed_tests,
        )
        session_repo.bump_turn(state.session_id, state.visit_number, patient_turn_no)
        state.turn_in_visit = current_turn + 1
    except Exception:
        turn_repo.mark_status(turn_id, "failed")
        raise

    turn_repo.mark_status(turn_id, "persisted")
    state.last_doctor_message = ""
    return state


def maybe_end_visit(state: GraphState) -> GraphState:
    # Auto-ending is disabled; visit ends only via the CLI endvisit command.
    state.should_end_visit = False
    return state


def summarize_and_embed(state: GraphState, db) -> GraphState:
    if not state.should_end_visit:
        return state

    message_repo = MessageRepo(db)
    summary_repo = SummaryRepo(db)
    session_repo = SessionRepo(db)
    visit_messages = message_repo.list_by_visit(state.session_id, state.visit_number)
    if visit_messages:
        summary_text = summarize_visit_one_call(
            db=db,
            session_id=state.session_id,
            visit_no=state.visit_number,
            messages_this_visit=visit_messages,
            summary_repo=summary_repo,
        )
        state.last_visit_summary = summary_text

    session_repo.end_visit(state.session_id, new_visit_no=state.visit_number + 1, reset_turn_no=True)
    refreshed = session_repo.get(state.session_id)
    if refreshed:
        state.visit_number = int(refreshed.get("visit_no", state.visit_number + 1))
        stored_turn_no = int(refreshed.get("turn_no", 0))
        state.turn_in_visit = max(0, stored_turn_no // 2)
        state.status = str(refreshed.get("status", state.status))
        state.disclosed_fact_ids = list(refreshed.get("disclosed_fact_ids", state.disclosed_fact_ids))
        state.performed_exams = list(refreshed.get("performed_exams", state.performed_exams))
        state.performed_tests = list(refreshed.get("performed_tests", state.performed_tests))
    else:
        state.visit_number += 1
        state.turn_in_visit = 0
    state.should_end_visit = False
    state.visit_end_recommendation = False
    return state


def summarize_and_embed_visit(state: GraphState, db) -> GraphState:
    return summarize_and_embed(state, db)


def persist_visit_summary(state: GraphState) -> GraphState:
    # No-op placeholder to avoid crashing the graph when auto-end is disabled.
    return state


def build_visit_graph(db, *, use_checkpointer: bool = True):
    g = StateGraph(GraphState)

    g.add_node("load_state_for_visit", lambda s: load_state_for_visit(s, db))
    g.add_node("compose_visit_intro", compose_visit_intro)
    g.add_node("persist_patient_intro", make_persist_patient_intro(db))
    g.add_node("await_doctor_message", await_doctor_message)
    g.add_node("reset_turn_outputs", reset_turn_outputs)
    g.add_node("tool_precheck", lambda s: tool_precheck(s, db))
    g.add_node("retrieve_context", lambda s: retrieve_context_node(s, db))
    g.add_node("generate_patient_response", generate_patient_response)
    g.add_node("validate_response", validate_response)
    g.add_node("persist_turn", lambda s: persist_turn(s, db))
    g.add_node("maybe_end_visit", maybe_end_visit)
    g.add_node("summarize_and_embed_visit", lambda s: summarize_and_embed_visit(s, db))
    g.add_node("persist_visit_summary", persist_visit_summary)

    g.set_entry_point("load_state_for_visit")

    g.add_edge("load_state_for_visit", "compose_visit_intro")
    g.add_edge("compose_visit_intro", "persist_patient_intro")
    g.add_edge("persist_patient_intro", "await_doctor_message")
    g.add_edge("await_doctor_message", "reset_turn_outputs")
    g.add_edge("reset_turn_outputs", "tool_precheck")

    g.add_conditional_edges(
        "tool_precheck",
        lambda s: "retrieve_context" if s.should_call_llm else "persist_turn",
        {
            "retrieve_context": "retrieve_context",
            "persist_turn": "persist_turn",
        },
    )
    g.add_edge("retrieve_context", "generate_patient_response")
    g.add_edge("generate_patient_response", "validate_response")
    g.add_edge("validate_response", "persist_turn")
    g.add_edge("persist_turn", "maybe_end_visit")

    g.add_conditional_edges(
        "maybe_end_visit",
        lambda s: "summarize_and_embed_visit" if s.should_end_visit else "await_doctor_message",
        {
            "summarize_and_embed_visit": "summarize_and_embed_visit",
            "await_doctor_message": "await_doctor_message",
        },
    )

    g.add_edge("summarize_and_embed_visit", "persist_visit_summary")
    g.add_edge("persist_visit_summary", END)

    checkpointer = MemorySaver() if use_checkpointer else None
    return g.compile(checkpointer=checkpointer)


def build_graph(db):
    return build_visit_graph(db)
