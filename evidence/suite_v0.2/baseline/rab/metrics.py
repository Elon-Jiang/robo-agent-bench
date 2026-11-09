"""Shared, dependency-free suite metric definitions."""

import re


def parse_self_report(text):
    # Exactly one unambiguous structured label; conflicting labels are missing.
    labels = re.findall(
        r"(?:^|\n)\s*(?:#\s*)?SELF_REPORT:\s*(success|partial|failure)\s*(?=\n|$)", text
    )
    return labels[0] if len(labels) == 1 else None


def report_correct(record):
    label = record.get("self_report")
    if label is None:
        return None
    strict = bool(record["success_strict"])
    score = record["score_0_100"]
    if label == "success":
        return strict
    if label == "partial":
        return not strict and 0 < score < 100
    if label == "failure":
        return score < 30 or bool(record.get("critical_errors"))
    return None
