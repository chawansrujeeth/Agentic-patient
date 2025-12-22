from __future__ import annotations

import os
import shlex
from typing import Optional, Dict, Any, List

from db import ping, get_db
from graph import build_visit_graph
from graph_state import GraphState
from models import ensure_indexes, seed_cases_if_missing
from policy import max_visits
from repos import DoctorRepo, CaseRepo, SessionRepo, MessageRepo, SummaryRepo
from session_utils import graph_state_from_session
from summarize import summarize_visit_one_call

K_MSGS = int(os.getenv("CONTEXT_LAST_K_MSGS", "12"))


def print_help() -> None:
    print(
        "\nCommands:\n"
        "  new <doctor_id> <case_id>\n"
        "  resume <doctor_id> [session_id]\n"
        "  send <message text...>\n"
        "  summarize              # summarize current visit\n"
        "  endvisit\n"
        "  history [n]\n"
        "  quit\n"
    )


def format_msgs(msgs: List[Dict[str, Any]]) -> None:
    for m in msgs:
        print(f"[v{m['visit_no']} t{m['turn_no']:03d}] {m['role']}: {m['content']}")


def main() -> None:
    # Boot
    print("Starting docs-ai CLI (Checkpoint 1)...")
    ping()

    db = get_db()
    ensure_indexes(db)
    inserted = seed_cases_if_missing(db)
    if inserted:
        print(f"Seeded {inserted} case(s).")

    doctor_repo = DoctorRepo(db)
    case_repo = CaseRepo(db)
    session_repo = SessionRepo(db)
    message_repo = MessageRepo(db)
    summary_repo = SummaryRepo(db)

    # Current context
    current_state: Optional[GraphState] = None
    current_thread_id: Optional[str] = None
    visit_graph = build_visit_graph(db)

    def _visit_thread_id(state: GraphState) -> str:
        return f"{state.session_id}:v{state.visit_number}"

    def _coerce_state(values: Any) -> GraphState:
        if isinstance(values, GraphState):
            return values
        if isinstance(values, dict):
            try:
                return GraphState.model_validate(values)
            except Exception:
                if current_state:
                    return current_state.model_copy(update=values)
        if current_state:
            return current_state
        raise RuntimeError("Unable to load graph state from checkpoint")

    def _run_visit_graph(input_value: Any, *, print_output: bool = True) -> bool:
        nonlocal current_state, current_thread_id
        if not current_state:
            return False
        if not current_thread_id:
            current_thread_id = _visit_thread_id(current_state)
        config = {"configurable": {"thread_id": current_thread_id}}
        visit_graph.invoke(input_value, config)
        snapshot = visit_graph.get_state(config)
        current_state = _coerce_state(snapshot.values)
        if print_output and current_state.patient_utterance:
            print(f"Patient: {current_state.patient_utterance}")
        ended = not snapshot.next
        if ended:
            current_thread_id = None
        return ended

    print_help()

    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return

        if not raw:
            continue

        parts = shlex.split(raw)
        cmd = parts[0].lower()

        if cmd in ("quit", "exit"):
            print("Bye.")
            return

        if cmd in ("help", "?"):
            print_help()
            continue

        if cmd == "new":
            if len(parts) < 3:
                print("Usage: new <doctor_id> <case_id>")
                continue
            doctor_id, case_id = parts[1], parts[2]

            doctor = doctor_repo.get_or_create(doctor_id, level=0)
            case = case_repo.get(case_id)
            if not case:
                print(f"Case not found: {case_id}. Use seed_cases.json or insert it first.")
                continue

            session_doc = session_repo.create(doctor_id=doctor_id, case_id=case_id)
            current_state = graph_state_from_session(session_doc)
            print(f"Created session: {current_state.session_id} (doctor={doctor_id}, case={case_id})")
            current_thread_id = None
            visit_ended = _run_visit_graph(current_state)
            if visit_ended:
                print("Visit ended.")
            continue

        if cmd == "resume":
            if len(parts) < 2:
                print("Usage: resume <doctor_id> [session_id]")
                continue
            doctor_id = parts[1]
            session_id = parts[2] if len(parts) >= 3 else None

            doctor = doctor_repo.get_or_create(doctor_id, level=0)

            sess = session_repo.get(session_id) if session_id else session_repo.find_active(doctor_id)
            if not sess:
                print("No session found to resume.")
                continue
            if sess["doctor_id"] != doctor_id:
                print("Session doctor_id does not match.")
                continue

            current_state = graph_state_from_session(sess)
            current_state.doctor_level = int(doctor.get("level", 0))
            msgs = message_repo.list_last(current_state.session_id, n=K_MSGS)

            print(f"Resumed session: {current_state.session_id} (case={current_state.case_id})")
            print(
                "State: visit_number="
                f"{current_state.visit_number}, "
                f"turn_in_visit={current_state.turn_in_visit}, "
                f"status={current_state.status}"
            )
            if msgs:
                print(f"\nLast {len(msgs)} message(s):")
                format_msgs(msgs)
            else:
                print("\nNo messages yet.")
            current_thread_id = None
            visit_ended = _run_visit_graph(current_state)
            if visit_ended:
                print("Visit ended.")
            continue

        if cmd == "send":
            if not current_state:
                print("No active session. Use: new ... or resume ...")
                continue
            if len(parts) < 2:
                print("Usage: send <message text...>")
                continue

            text = raw[len("send") :].strip()
            if not current_thread_id:
                current_state.is_new_visit = True
                current_state.last_doctor_message = None
                visit_ended = _run_visit_graph(current_state)
                if visit_ended:
                    print("Visit ended.")
                    continue

            visit_ended = _run_visit_graph({"last_doctor_message": text})
            if visit_ended:
                print("Visit ended.")
            continue

        if cmd == "summarize":
            if not current_state:
                print("No active session.")
                continue

            session_id = current_state.session_id
            visit_no = int(current_state.visit_number)
            visit_messages = message_repo.list_by_visit(session_id, visit_no)
            if not visit_messages:
                print("No messages in this visit to summarize yet.")
                continue

            try:
                summary_text = summarize_visit_one_call(
                    db=db,
                    session_id=session_id,
                    visit_no=visit_no,
                    messages_this_visit=visit_messages,
                    summary_repo=summary_repo,
                )
                print("Visit summary stored and embedded:\n")
                print(summary_text)
            except Exception as exc:
                print(f"Summarization failed: {exc}")
            continue

        if cmd == "endvisit":
            if not current_state:
                print("No active session.")
                continue

            state = current_state
            doctor = doctor_repo.get_or_create(state.doctor_id, level=0)
            level = int(doctor.get("level", 0))

            cur_visit = int(state.visit_number)
            maxv = max_visits(level)
            if cur_visit >= maxv:
                print(f"Max visits reached for level {level}: {maxv}. Consider closing the session later.")
                continue

            visit_messages = message_repo.list_by_visit(state.session_id, cur_visit)
            if visit_messages:
                try:
                    summary_text = summarize_visit_one_call(
                        db=db,
                        session_id=state.session_id,
                        visit_no=cur_visit,
                        messages_this_visit=visit_messages,
                        summary_repo=summary_repo,
                    )
                    state.last_visit_summary = summary_text
                except Exception as exc:
                    print(f"[warn] Summarization failed during endvisit: {exc}")

            new_visit = cur_visit + 1
            session_repo.end_visit(state.session_id, new_visit_no=new_visit, reset_turn_no=True)
            updated_session = session_repo.get(state.session_id)
            if updated_session:
                state.visit_number = int(updated_session.get("visit_no", new_visit))
                stored_turn_no = int(updated_session.get("turn_no", 0))
                state.turn_in_visit = max(0, stored_turn_no // 2)
                state.status = str(updated_session.get("status", state.status))
                state.disclosed_fact_ids = list(updated_session.get("disclosed_fact_ids", state.disclosed_fact_ids))
                state.performed_exams = list(updated_session.get("performed_exams", state.performed_exams))
                state.performed_tests = list(updated_session.get("performed_tests", state.performed_tests))
            else:
                state.visit_number = new_visit
                state.turn_in_visit = 0
            state.is_new_visit = True
            current_thread_id = None
            print(
                f"Visit ended. Now at visit_number={state.visit_number}, "
                f"turn_in_visit={state.turn_in_visit}."
            )
            continue

        if cmd == "history":
            if not current_state:
                print("No active session.")
                continue
            n = 20
            if len(parts) >= 2:
                try:
                    n = int(parts[1])
                except ValueError:
                    print("history [n] where n is an integer")
                    continue

            msgs = message_repo.list_last(current_state.session_id, n=n)
            if not msgs:
                print("(no messages)")
            else:
                format_msgs(msgs)
            continue

        print("Unknown command.")
        print_help()


if __name__ == "__main__":
    main()
