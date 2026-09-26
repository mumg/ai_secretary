"""Compare model proposals against the user's review of exported messages."""

from __future__ import annotations

import re
from collections import defaultdict

from engine import suggestions_from_result


def normalize(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def expected_lines(value: object) -> tuple[str, ...]:
    lines = []
    for line in str(value or "").splitlines():
        item = normalize(re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line))
        if item and item not in ("нет", "none", "—", "-"):
            lines.append(item)
    return tuple(sorted(lines))


def proposed(result: dict, kind: str, *, titles_only: bool = False) -> tuple:
    items = []
    for item in suggestions_from_result(result).get(kind, []):
        title = normalize(item.get("title"))
        if titles_only:
            items.append(title)
        elif kind == "tasks":
            items.append((title, normalize(item.get("assignee")),
                          normalize(item.get("assignee_address"))))
        else:
            items.append((title, normalize(item.get("assignee_email") or item.get("assignee_name"))))
    return tuple(sorted(items))


def expected_matches(result: dict, reviewed: dict) -> bool:
    review = reviewed["review"]
    if review["verdict"] == "agree":
        return all(proposed(result, kind) == proposed(reviewed, kind)
                   for kind in ("tasks", "delegations"))
    return all(proposed(result, kind, titles_only=True) == expected_lines(review.get(field))
               for kind, field in (("tasks", "expected_tasks"),
                                   ("delegations", "expected_delegations")))


def annotate(entries: list[tuple[dict, dict]]) -> list[dict]:
    """Return UI summaries for (saved result, base metadata) pairs."""
    groups = defaultdict(list)
    for result, meta in entries:
        groups[(meta["case"], result.get("input_sha256"))].append((result, meta))
    output = []
    for group in groups.values():
        reviewed = None
        for result, meta in sorted(group, key=lambda pair: pair[1]["run_id"]):
            suggestions = suggestions_from_result(result)
            meta["task_count"] = len(suggestions.get("tasks", []))
            meta["delegation_count"] = len(suggestions.get("delegations", []))
            review = result.get("review") or {}
            valid_review = review.get("scope") == "suggestions_v1" and review.get("verdict") in ("agree", "disagree")
            meta["review"] = review.get("verdict") if valid_review else None
            meta["legacy_review"] = bool(review.get("verdict") and not valid_review)
            if valid_review:
                meta["comparison"] = "matched" if review["verdict"] == "agree" else "diverged"
                meta["reference_run_id"] = meta["run_id"]
                reviewed = result
            elif reviewed is not None:
                meta["comparison"] = "matched" if expected_matches(result, reviewed) else "diverged"
                meta["reference_run_id"] = reviewed["run_id"]
            else:
                meta["comparison"] = "unchecked"
                meta["reference_run_id"] = None
            output.append(meta)
    return sorted(output, key=lambda item: item["run_id"], reverse=True)
