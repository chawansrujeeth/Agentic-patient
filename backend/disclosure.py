from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _tokenize(s: str) -> Set[str]:
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    toks = {t for t in s.split() if len(t) >= 3}
    return toks


@dataclass(frozen=True)
class DisclosureContext:
    level: int
    visit_no: int
    max_depth: int
    allowed_tools: Set[str]


def _eligible_chunks(case_doc: Dict[str, Any], ctx: DisclosureContext) -> List[Dict[str, Any]]:
    chunks = case_doc.get("chunks", [])
    out = []
    for ch in chunks:
        if int(ch.get("visit_no", 0)) != int(ctx.visit_no):
            continue
        if int(ch.get("detail_depth", 1)) > int(ctx.max_depth):
            continue
        kind = ch.get("kind", "symptoms")
        if kind == "tests" and "tests" not in ctx.allowed_tools:
            continue
        if kind == "exam" and "exam" not in ctx.allowed_tools:
            continue
        out.append(ch)
    out.sort(key=lambda x: (int(x.get("stage", 0)), str(x.get("chunk_id", ""))))
    return out


def _pick_new_facts(
    doctor_text: str,
    eligible: List[Dict[str, Any]],
    disclosed_fact_ids: Set[str],
    k: int = 2,
) -> List[Dict[str, Any]]:
    q_tokens = _tokenize(doctor_text)
    scored = []
    for ch in eligible:
        cid = ch.get("chunk_id")
        if not cid or cid in disclosed_fact_ids:
            continue
        tags = {t.lower() for t in (ch.get("tags") or [])}
        content_tokens = _tokenize(ch.get("content", ""))
        score = len(q_tokens & tags) * 5 + len(q_tokens & content_tokens)
        scored.append((score, int(ch.get("stage", 0)), cid, ch))

    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    picked = [item[3] for item in scored[:k]]

    if not picked:
        for ch in eligible:
            cid = ch.get("chunk_id")
            if cid and cid not in disclosed_fact_ids:
                picked.append(ch)
            if len(picked) >= k:
                break
    return picked


def _handle_exam_or_test(
    kind: str,
    request: str,
    eligible: List[Dict[str, Any]],
    disclosed_fact_ids: Set[str],
) -> Tuple[str, List[Dict[str, Any]]]:
    req_tokens = _tokenize(request)
    candidates = [ch for ch in eligible if ch.get("kind") == kind]

    best = None
    best_score = -1
    for ch in candidates:
        cid = ch.get("chunk_id")
        if not cid or cid in disclosed_fact_ids:
            continue
        tags = {t.lower() for t in (ch.get("tags") or [])}
        content_tokens = _tokenize(ch.get("content", ""))
        score = len(req_tokens & tags) * 5 + len(req_tokens & content_tokens)
        if score > best_score:
            best_score = score
            best = ch

    if best is None:
        if not candidates:
            return f"I can’t provide {kind} findings right now.", []
        return f"No additional {kind} findings beyond what I've already shared.", []

    return best.get("content", ""), [best]


def generate_patient_response(
    doctor_text: str,
    case_doc: Dict[str, Any],
    ctx: DisclosureContext,
    disclosed_fact_ids: List[str],
) -> Tuple[str, List[str], List[str], List[str]]:
    disclosed_set = set(disclosed_fact_ids)
    eligible = _eligible_chunks(case_doc, ctx)

    raw = doctor_text.strip()
    lower = raw.lower()

    performed_exams_add: List[str] = []
    performed_tests_add: List[str] = []
    new_fact_ids: List[str] = []

    if lower.startswith("exam:"):
        req = _normalize(raw[len("exam:") :])
        if "exam" not in ctx.allowed_tools:
            return "I’d prefer not to do an examination right now.", [], [], []
        findings, chunks = _handle_exam_or_test("exam", req, eligible, disclosed_set)
        performed_exams_add.append(req if req else "exam")
        for ch in chunks:
            cid = ch.get("chunk_id")
            if cid and cid not in disclosed_set:
                new_fact_ids.append(cid)
        return findings, new_fact_ids, performed_exams_add, []

    if lower.startswith("test:"):
        req = _normalize(raw[len("test:") :])
        if "tests" not in ctx.allowed_tools:
            return "I don’t think tests are available at this stage.", [], [], []
        findings, chunks = _handle_exam_or_test("tests", req, eligible, disclosed_set)
        performed_tests_add.append(req if req else "test")
        for ch in chunks:
            cid = ch.get("chunk_id")
            if cid and cid not in disclosed_set:
                new_fact_ids.append(cid)
        return findings, new_fact_ids, [], performed_tests_add

    narrative_eligible = [ch for ch in eligible if ch.get("kind") not in ("exam", "tests")]
    picked = _pick_new_facts(raw, narrative_eligible, disclosed_set, k=2)

    if not picked:
        return "I’m not sure what else to add right now.", [], [], []

    parts = []
    for ch in picked:
        content = ch.get("content", "")
        if content:
            parts.append(content)
        cid = ch.get("chunk_id")
        if cid and cid not in disclosed_set:
            new_fact_ids.append(cid)

    patient_text = " ".join(parts)
    return patient_text, new_fact_ids, [], []
