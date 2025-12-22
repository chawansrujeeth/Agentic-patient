from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from policy import allowed_tools, max_detail_depth


def build_allowed_facts(
    *,
    case_doc: Dict[str, Any],
    level: int,
    visit_no: int,
    disclosed_fact_ids: List[str],
    max_facts: int,
) -> List[Dict[str, str]]:
    """
    Deterministically filter case chunks into prompt facts, respecting visit/depth/tool gating.
    """
    disclosed = set(disclosed_fact_ids)
    max_depth = max_detail_depth(level, visit_no)
    allowed_tool_set = allowed_tools(level, visit_no)

    chunks = case_doc.get("chunks", []) or []
    sorted_chunks = sorted(
        chunks,
        key=lambda ch: (
            int(ch.get("visit_no", 0)),
            int(ch.get("stage", 0)),
            str(ch.get("chunk_id", "")),
        ),
    )

    facts: List[Dict[str, str]] = []
    for ch in sorted_chunks:
        cid = ch.get("chunk_id")
        if not cid or cid in disclosed:
            continue
        if int(ch.get("visit_no", 0)) != int(visit_no):
            continue
        if int(ch.get("detail_depth", 1)) > int(max_depth):
            continue

        kind = str(ch.get("kind", "") or "")
        if kind == "exam" and "exam" not in allowed_tool_set:
            continue
        if kind == "tests" and "tests" not in allowed_tool_set:
            continue

        facts.append({"id": cid, "kind": kind, "text": ch.get("content", "")})
        if len(facts) >= max_facts:
            break

    return facts


def build_prompt_with_retrieval(
    visit_no: int,
    level: int,
    doctor_message: str,
    allowed_facts: List[Dict[str, str]],
    recent_conversation: List[Dict[str, str]],
    already_disclosed_fact_ids: List[str],
    retrieved: Dict[str, Any],
    case_type: Optional[str] = None,
    last_visit_summary: Optional[str] = None,
) -> str:
    """Construct the strict patient prompt using retrieved summaries/messages/chunks."""

    retrieved_summaries = [
        {"doc_id": s.get("doc_id"), "visit_no": s.get("visit_no"), "text": s.get("text", "")}
        for s in (retrieved.get("summaries") or [])
    ]
    retrieved_messages = [
        {
            "doc_id": m.get("doc_id"),
            "visit_no": m.get("visit_no"),
            "role": m.get("role"),
            "text": m.get("text", ""),
        }
        for m in (retrieved.get("messages") or [])
    ]
    retrieved_chunks = [
        {
            "doc_id": c.get("doc_id"),
            "visit_no": c.get("visit_no"),
            "kind": c.get("kind"),
            "text": c.get("text", ""),
        }
        for c in (retrieved.get("case_chunks") or [])
    ]

    payload = {
        "visit_no": int(visit_no),
        "doctor_level": int(level),
        "case_type": (case_type or "").strip(),
        "doctor_message": doctor_message,
        "recent_conversation": recent_conversation[-12:],
        "last_visit_summary": (last_visit_summary or "").strip(),
        "retrieved_context": {
            "summaries": retrieved_summaries,
            "prior_messages": retrieved_messages,
            "case_chunks": retrieved_chunks,
        },
        "allowed_facts": allowed_facts,
        "already_disclosed_fact_ids": already_disclosed_fact_ids,
        "output_schema": {
            "patient_utterance": "string",
            "new_disclosed_fact_ids": ["string"],
            "requested_clarifications": ["string (optional)"],
            "visit_end_recommendation": "boolean",
            "safety_flags": ["string"],
        },
    }

    extra_rules = ""
    case_type_norm = (case_type or "").strip().lower()
    if case_type_norm.startswith("viral"):
        extra_rules = (
            "Viral-case behavior:\n"
            "- If the doctor recommends or prescribes a treatment/medication, acknowledge and accept it.\n"
            "- If asked about medications already prescribed, reuse the exact wording from prior messages or the "
            "last_visit_summary. If it is not mentioned there, say you are not sure.\n"
            "- If the doctor asks you to return or follow up later, include a brief thanks and set "
            "visit_end_recommendation to true.\n"
            "- You may reference prior doctor-prescribed treatments from retrieved_context or last_visit_summary; "
            "do NOT add those as new_disclosed_fact_ids.\n"
            "\n"
        )

    return (
        "You are simulating a patient in a medical training CLI.\n"
        "\n"
        "Hard rules:\n"
        "1) You MUST NOT introduce any clinical facts that are not present in allowed_facts.\n"
        "2) You MUST ONLY disclose new facts by returning their IDs in new_disclosed_fact_ids.\n"
        "3) Every ID in new_disclosed_fact_ids MUST be from allowed_facts.\n"
        "4) Retrieved context is for continuity only; it does NOT expand what you can newly disclose.\n"
        "5) Output MUST be valid JSON and MUST match the PatientResponse schema exactly. No extra keys.\n"
        "6) Keep patient_utterance short (1–3 sentences).\n"
        "\n"
        f"{extra_rules}"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
        "\n"
        "Return JSON now.\n"
    )


__all__ = ["build_allowed_facts", "build_prompt_with_retrieval"]
