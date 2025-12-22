from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from llm_models import PatientResponse


@dataclass(frozen=True)
class GuardrailDecision:
    patient_utterance: str
    new_disclosed_fact_ids: List[str]
    safety_flags: List[str]
    rejected: bool = False


def validate_fact_ids(
    proposed_ids: List[str],
    allowed_ids: Set[str],
    already_disclosed_ids: Set[str],
) -> Tuple[List[str], List[str]]:
    """
    Returns (safe_ids, flags)
    - safe_ids are within allowed_ids and not already disclosed.
    """
    flags: List[str] = []
    safe: List[str] = []

    for fid in proposed_ids:
        if fid not in allowed_ids:
            flags.append("fact_id_not_allowed")
            continue
        if fid in already_disclosed_ids:
            flags.append("fact_id_repeated")
            continue
        safe.append(fid)

    return safe, flags


def strip_unsafe_mentions(text: str, allowed_fact_texts: List[str]) -> Tuple[str, List[str]]:
    """
    Prototype-friendly heuristic:
    - If the patient utterance contains lines/sentences that look like 'new facts'
      not in the allowed facts, we can't perfectly detect that without an LLM.
    - But we can still apply conservative hygiene:
        * remove obvious 'diagnosis' declarations (e.g., "I think it's X") if you want
        * remove enumerations with numbers/dates if they don't appear in allowed facts
    In practice, *your true policy enforcement is via fact IDs*, not text stripping.
    """
    flags: List[str] = []
    t = text.strip()

    # Optional: strip explicit diagnosis patterns (tune as you like)
    diag_pat = re.compile(r"\b(diagnosis|i have|it is|it's)\b.*\b(cancer|tumor|appendicitis|ulcer)\b", re.I)
    if diag_pat.search(t):
        t = diag_pat.sub("[redacted]", t)
        flags.append("utterance_redacted_possible_dx")

    # If utterance is empty after redaction, replace with safe fallback
    if not t or t == "[redacted]":
        t = "I can share what I’m experiencing, but I’m not sure about specifics beyond what we’ve discussed."
        flags.append("utterance_replaced_safe_fallback")

    return t, flags


def apply_guardrails(
    resp: PatientResponse,
    allowed_facts: List[Dict[str, str]],   # [{"id":..., "text":..., "kind":...}, ...]
    already_disclosed_fact_ids: List[str],
    mode: str = "reject_once_else_strip",  # or "strip_only"
) -> GuardrailDecision:
    allowed_ids = {f["id"] for f in allowed_facts}
    already = set(already_disclosed_fact_ids)

    safe_ids, id_flags = validate_fact_ids(resp.new_disclosed_fact_ids, allowed_ids, already)

    # Always hard-filter IDs (never trust the model)
    safety_flags = list(resp.safety_flags) + id_flags

    utterance = resp.patient_utterance
    utterance, utterance_flags = strip_unsafe_mentions(
        utterance,
        allowed_fact_texts=[f["text"] for f in allowed_facts],
    )
    safety_flags += utterance_flags

    # Decide if we must regenerate:
    # - If model tried to disclose disallowed IDs, treat as policy violation
    tried_disallowed = "fact_id_not_allowed" in id_flags
    if tried_disallowed and mode.startswith("reject_once"):
        return GuardrailDecision(
            patient_utterance=utterance,
            new_disclosed_fact_ids=safe_ids,
            safety_flags=safety_flags + ["guardrail_reject_regenerate"],
            rejected=True,
        )

    # Otherwise, keep safe response
    return GuardrailDecision(
        patient_utterance=utterance,
        new_disclosed_fact_ids=safe_ids,
        safety_flags=safety_flags,
        rejected=False,
    )
