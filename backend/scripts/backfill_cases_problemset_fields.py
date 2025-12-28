from __future__ import annotations

"""
One-time (idempotent) backfill for problemset metadata on case documents.

This repo stores cases in Supabase/Postgres. We keep the full case JSON in the
`cases.seed` JSONB column and (optionally) mirror problemset metadata into
dedicated columns when the DB schema has been updated.

Safe to run multiple times: only fills missing/invalid fields.
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(_HERE))  # allow `import db`, `import models`

from db import SupabaseError, get_db  # noqa: E402
from models import (  # noqa: E402
    COL_CASES,
    normalize_case_problemset_fields,
)

_PROBLEMSET_COLS = ("difficulty", "tags", "short_prompt", "estimated_time_min", "version", "is_published")
_ALLOWED_DIFFICULTIES = {"Easy", "Medium", "Hard"}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _is_valid_tags(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(tag, str) for tag in value)


def _is_valid_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def _seed_needs_backfill(seed: Dict[str, Any]) -> bool:
    if _is_missing(seed.get("case_id")):
        return True
    if _is_missing(seed.get("title")):
        return True
    if seed.get("difficulty") not in _ALLOWED_DIFFICULTIES:
        return True
    if not _is_valid_tags(seed.get("tags")):
        return True
    if _is_missing(seed.get("short_prompt")):
        return True
    if not _is_valid_int(seed.get("estimated_time_min")):
        return True
    if not _is_valid_int(seed.get("version")):
        return True
    if not isinstance(seed.get("is_published"), bool):
        return True
    return False


def _merge_seed_with_row(seed: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(seed)
    if _is_missing(merged.get("case_id")) and not _is_missing(row.get("case_id")):
        merged["case_id"] = row.get("case_id")
    if _is_missing(merged.get("title")) and not _is_missing(row.get("title")):
        merged["title"] = row.get("title")
    for key in _PROBLEMSET_COLS:
        if key not in merged and key in row and row.get(key) is not None:
            merged[key] = row.get(key)
    return merged


def _strip_unknown_columns(payload: Dict[str, Any], response_text: str) -> Dict[str, Any]:
    cleaned = dict(payload)
    lowered = (response_text or "").lower()
    for col in _PROBLEMSET_COLS:
        if col in cleaned and col.lower() in lowered:
            cleaned.pop(col, None)
    return cleaned


def _build_updates(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    seed = row.get("seed")
    if not isinstance(seed, dict):
        seed = {}

    merged = _merge_seed_with_row(seed, row)
    normalized = normalize_case_problemset_fields(merged, default_tags=[])

    seed_update: Dict[str, Any] = dict(seed)
    if _is_missing(seed_update.get("case_id")) and not _is_missing(row.get("case_id")):
        seed_update["case_id"] = row.get("case_id")

    if _is_missing(seed_update.get("title")):
        seed_update["title"] = normalized.get("title", "Untitled Case")
    if seed_update.get("difficulty") not in _ALLOWED_DIFFICULTIES:
        seed_update["difficulty"] = normalized.get("difficulty", "Easy")
    if not _is_valid_tags(seed_update.get("tags")):
        seed_update["tags"] = normalized.get("tags", [])
    if _is_missing(seed_update.get("short_prompt")):
        seed_update["short_prompt"] = normalized.get("short_prompt", "")
    if not _is_valid_int(seed_update.get("estimated_time_min")):
        seed_update["estimated_time_min"] = normalized.get("estimated_time_min", 15)
    if not _is_valid_int(seed_update.get("version")):
        seed_update["version"] = normalized.get("version", 1)
    if not isinstance(seed_update.get("is_published"), bool):
        seed_update["is_published"] = bool(normalized.get("is_published", True))

    row_update: Dict[str, Any] = {}

    # Only backfill top-level columns when they're present but empty; keep existing values.
    if _is_missing(row.get("title")):
        row_update["title"] = seed_update.get("title", "Untitled Case")
    if "difficulty" in row and row.get("difficulty") not in _ALLOWED_DIFFICULTIES:
        row_update["difficulty"] = seed_update.get("difficulty", "Easy")
    if "tags" in row and not _is_valid_tags(row.get("tags")):
        row_update["tags"] = seed_update.get("tags", [])
    if _is_missing(row.get("short_prompt")) and "short_prompt" in row:
        row_update["short_prompt"] = seed_update.get("short_prompt", "")
    if "estimated_time_min" in row and not _is_valid_int(row.get("estimated_time_min")):
        row_update["estimated_time_min"] = seed_update.get("estimated_time_min", 15)
    if "version" in row and not _is_valid_int(row.get("version")):
        row_update["version"] = seed_update.get("version", 1)
    if "is_published" in row and not isinstance(row.get("is_published"), bool):
        row_update["is_published"] = seed_update.get("is_published", True)

    # Always write the updated seed when any problemset field needs backfill.
    if _seed_needs_backfill(seed):
        row_update["seed"] = seed_update

    return seed_update, row_update


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill problemset fields on cases (idempotent).")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing to DB.")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to scan (useful for testing).")
    args = parser.parse_args()

    db = get_db()
    table = db.table(COL_CASES)
    rows: List[Dict[str, Any]] = table.select(limit=args.limit)

    updated = 0
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        case_id = row.get("case_id")
        if _is_missing(case_id):
            continue

        _, row_update = _build_updates(row)
        if not row_update:
            skipped += 1
            continue

        if args.dry_run:
            print(f"[DRY RUN] would update {case_id}: {sorted(row_update.keys())}")
            updated += 1
            continue

        try:
            table.update(row_update, filters={"case_id": case_id}, returning=False)
        except SupabaseError as exc:
            # Backwards compatibility: if the DB doesn't have the dedicated columns yet,
            # retry with just the seed/title fields.
            cleaned = _strip_unknown_columns(row_update, exc.response_text or "")
            cleaned = {k: v for k, v in cleaned.items() if k in ("seed", "title")}
            if not cleaned:
                raise
            table.update(cleaned, filters={"case_id": case_id}, returning=False)

        updated += 1

    print(f"Done. updated={updated} skipped={skipped} scanned={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
