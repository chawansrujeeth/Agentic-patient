from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class PatientResponse(BaseModel):
    patient_utterance: str = Field(min_length=1)

    # must be subset of allowed_fact_ids you provide in the prompt
    new_disclosed_fact_ids: List[str] = Field(default_factory=list)

    requested_clarifications: Optional[List[str]] = None

    visit_end_recommendation: bool = False

    safety_flags: List[str] = Field(default_factory=list)
