from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GraphState(BaseModel):
    """
    Authoritative runtime snapshot for the LLM graph / CLI.

    If a value influences behavior, persistence, or QA decisions it belongs here.
    """

    # ---- Identity / session ----
    session_id: str
    doctor_id: str
    case_id: str
    case_type: Optional[str] = None

    # ---- Session progress ----
    visit_number: int = 1
    turn_in_visit: int = 0
    status: str = "active"
    doctor_turn_no: Optional[int] = None
    turn_id: Optional[str] = None

    # ---- Visit loop ----
    is_new_visit: bool = True
    last_visit_summary: Optional[str] = None
    should_call_llm: bool = True

    # ---- Ledger (authoritative) ----
    disclosed_fact_ids: List[str] = Field(default_factory=list)
    performed_exams: List[str] = Field(default_factory=list)
    performed_tests: List[str] = Field(default_factory=list)

    # ---- Inputs ----
    last_doctor_message: Optional[str] = None

    # ---- Derived / transient ----
    doctor_level: int = 0
    allowed_facts: List[Dict[str, str]] = Field(default_factory=list)
    retrieved_context: Dict[str, Any] = Field(default_factory=dict)

    # ---- LLM outputs ----
    patient_core_response: Optional[str] = None
    patient_utterance: Optional[str] = None
    response_source: str = "llm"
    new_disclosed_fact_ids: List[str] = Field(default_factory=list)
    safety_flags: List[str] = Field(default_factory=list)
    visit_end_recommendation: bool = False
    requested_clarifications: Optional[List[str]] = None

    # ---- Control / QA ----
    llm_attempts: int = 0
    should_end_visit: bool = False
    llm_usage: Optional[Dict[str, Any]] = None
    raw_llm_output: Optional[str] = None
    guardrail_rejected: bool = False

    def reset_turn_outputs(self) -> None:
        """
        Clear per-turn outputs + metadata so a new turn starts cleanly.
        """
        self.patient_core_response = None
        self.patient_utterance = None
        self.response_source = "llm"
        self.new_disclosed_fact_ids.clear()
        self.safety_flags.clear()
        self.visit_end_recommendation = False
        self.requested_clarifications = None
        self.llm_attempts = 0
        self.should_end_visit = False
        self.llm_usage = None
        self.raw_llm_output = None
        self.guardrail_rejected = False
        self.retrieved_context.clear()
        self.doctor_turn_no = None
        self.turn_id = None
        self.should_call_llm = True
