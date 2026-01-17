from __future__ import annotations

from typing import Set


def max_detail_depth(level: int, visit_no: int) -> int:
    """
    Determine how granular the patient disclosure can be.
    The current default workflow is single-visit, so allow full detail.
    """
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
    Whitelist doctor actions available during chat.
    The current default workflow is single-visit, so tests are always available.
    """
    return {"history", "exam", "tests"}
