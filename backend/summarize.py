from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from llm import call_summary_agent
from rag import store_visit_summary_embedding
from repos import SummaryRepo


def build_visit_summary_prompt(session_id: str, visit_no: int, messages: List[Dict[str, Any]]) -> str:
    """
    Build a concise prompt instructing the LLM to summarize the visit for retrieval.
    """

    brief = [{"role": m.get("role", ""), "content": m.get("content", "")} for m in messages][-30:]

    payload = {
        "session_id": session_id,
        "visit_no": int(visit_no),
        "messages": brief,
        "instructions": (
            "Write a concise clinical-visit summary for retrieval (5–8 bullets max). "
            "Include: presenting issue, key history, key tests/exam, treatments or medications prescribed "
            "(copy exact wording from the messages; do not substitute generic names), follow-up instructions, "
            "assessment direction, and open questions. "
            "Do NOT invent facts not present in the messages."
        ),
        "output_schema": {"summary_text": "string"},
    }

    return (
        "You are summarizing a patient-doctor visit for a training system.\n"
        "Rules:\n"
        "- Use only the provided messages.\n"
        "- Be concise and retrieval-friendly.\n"
        "- Output valid JSON with exactly one key: summary_text.\n"
        "\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
        "\n"
        "Return JSON now.\n"
    )


_MED_ADMIN_PAT = re.compile(
    r"\b(take|start|prescrib|rx|medication|tablet|pill|capsule|dose|dolo|ibuprofen|acetaminophen|paracetamol)\b",
    re.IGNORECASE,
)


def _extract_med_instruction(messages: List[Dict[str, Any]]) -> Optional[str]:
    last_match = None
    for msg in messages:
        if str(msg.get("role", "")).lower() != "doctor":
            continue
        text = str(msg.get("content", "")).strip()
        if not text:
            continue
        if _MED_ADMIN_PAT.search(text):
            last_match = text
    return last_match


def _ensure_med_in_summary(summary_text: str, med_text: Optional[str]) -> str:
    if not med_text:
        return summary_text
    if med_text.lower() in summary_text.lower():
        return summary_text
    cleaned_summary = summary_text.rstrip()
    if cleaned_summary:
        cleaned_summary = f"{cleaned_summary}\n- Treatments/Medications: {med_text}"
    else:
        cleaned_summary = f"- Treatments/Medications: {med_text}"
    return cleaned_summary


def summarize_visit_one_call(
    *,
    db: Any,
    session_id: str,
    visit_no: int,
    messages_this_visit: List[Dict[str, Any]],
    summary_repo: Optional[SummaryRepo] = None,
) -> str:
    """Generate, store, and embed a visit summary."""

    if not messages_this_visit:
        raise ValueError("No messages available for this visit to summarize.")

    prompt = build_visit_summary_prompt(session_id, visit_no, messages_this_visit)
    summary_res = call_summary_agent(prompt)
    summary_text = summary_res.summary_text
    med_text = _extract_med_instruction(messages_this_visit)
    summary_text = _ensure_med_in_summary(summary_text, med_text)

    repo = summary_repo or SummaryRepo(db)
    repo.upsert_summary(session_id=session_id, visit_no=visit_no, summary_text=summary_text)
    store_visit_summary_embedding(session_id, visit_no, summary_text, db=db)

    return summary_text


__all__ = ["build_visit_summary_prompt", "summarize_visit_one_call"]
