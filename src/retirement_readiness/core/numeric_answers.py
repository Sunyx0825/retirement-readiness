"""Preserve unknown/ranged answers while selecting explicit model values."""

import math
import re
import unicodedata
from typing import Any

UNKNOWN = frozenset(
    {
        "不清楚",
        "不知道",
        "不确定",
        "尚不明确",
        "尚未确定",
        "未确定",
        "未知",
        "unknown",
        "not_sure",
        "uncertain",
        "not_confirmed",
    }
)
_NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"


def numeric_answer(raw: Any) -> tuple[str, float | None, float | None]:
    """Return status and bounds in the field's declared unit, without changing raw."""
    if raw is None or isinstance(raw, str) and not raw.strip():
        return "missing", None, None
    if not isinstance(raw, (int, float, str)) or isinstance(raw, bool):
        return "invalid", None, None
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if text.lower() in UNKNOWN:
        return "unknown", None, None
    text = re.sub(r"[,，\s]", "", text)
    if text == "/":
        return "known", 0.0, 0.0
    text = re.sub(r"(?:万元|元)(?:/(?:月|年))?|周岁|个月|岁|年|套|辆|位|人|%", "", text, count=1)
    if re.fullmatch(_NUMBER, text):
        value = float(text)
        return ("known", value, value) if math.isfinite(value) else ("invalid", None, None)
    interval = re.fullmatch(f"({_NUMBER})(?:—|–|~|～|至|到|- )({_NUMBER})".replace("- ", "-"), text)
    if interval:
        lower, upper = map(float, interval.groups())
        return ("range", lower, upper) if lower <= upper else ("invalid", None, None)
    for side, pattern in (
        (
            "lower",
            f"(?:至少|不低于|≥|>=|超过|高于|>)({_NUMBER})|({_NUMBER})(?:以上|及以上|起)",
        ),
        (
            "upper",
            f"(?:至多|不超过|≤|<=|少于|低于|<)({_NUMBER})|({_NUMBER})(?:以下|及以下|以内)",
        ),
    ):
        match = re.fullmatch(pattern, text)
        if match:
            value = float(next(part for part in match.groups() if part is not None))
            return (
                "range",
                value if side == "lower" else None,
                value if side == "upper" else None,
            )
    return "invalid", None, None


def model_value(raw: Any, default: float = 0) -> float:
    status, lower, upper = numeric_answer(raw)
    if status == "missing":
        return 0
    values = [value for value in (lower, upper) if value is not None]
    return sum(values) / len(values) if values else default
