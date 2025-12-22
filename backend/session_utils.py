from __future__ import annotations

from typing import Any, Dict

from graph_state import GraphState


def graph_state_from_session(session_doc: Dict[str, Any]) -> GraphState:
    """
    Initialize GraphState from a DB session document.
    """
    stored_turn_no = int(session_doc.get("turn_no", 0))
    return GraphState(
        session_id=session_doc["session_id"],
        doctor_id=session_doc["doctor_id"],
        case_id=session_doc["case_id"],
        visit_number=int(session_doc.get("visit_no", 1)),
        turn_in_visit=max(0, stored_turn_no // 2),
        is_new_visit=stored_turn_no == 0,
        status=str(session_doc.get("status", "active")),
        disclosed_fact_ids=list(session_doc.get("disclosed_fact_ids", [])),
        performed_exams=list(session_doc.get("performed_exams", [])),
        performed_tests=list(session_doc.get("performed_tests", [])),
    )
