from __future__ import annotations

from typing import Set


def max_detail_depth(level: int, visit_no: int) -> int:
    """
    Determine how granular the patient disclosure can be.
    Level is ignored; only visit number gates disclosure depth.
    """
    visit_no = max(1, int(visit_no))
    if visit_no == 1:
        return 1
    if visit_no == 2:
        return 2
    return 3


def max_visits(level: int) -> int:
    """
    Cap the number of visits permitted for a given doctor level.
    """
    level = max(0, int(level))
    if level <= 1:
        return 2
    if level == 2:
        return 3
    if level == 3:
        return 4
    return 5


def allowed_tools(level: int, visit_no: int) -> Set[str]:
    """
    Whitelist doctor actions unlocked by visit.
    Level is ignored; visit number gates exams/tests.
    """
    visit_no = max(1, int(visit_no))
    allowed = {"history", "exam"}
    if visit_no >= 2:
        allowed.add("tests")
    return allowed
