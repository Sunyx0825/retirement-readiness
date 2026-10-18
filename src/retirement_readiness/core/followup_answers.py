"""Validate and preserve coded follow-ups, including required free text."""

import re
from typing import Any

EXCLUSIVE_CODES = frozenset({"none", "not_sure", "not_confirmed", "not_applicable"})


def requires_text(option: dict[str, Any]) -> bool:
    return bool(
        option.get("requires_text")
        or re.match(r"other(?:_|$)", str(option.get("code")), re.I)
        or str(option.get("label", "")).strip().startswith("其他")
    )


def normalize_answer(question: dict[str, Any], value: Any) -> tuple[Any, str, dict[str, str]]:
    kind = question.get("question_type")
    if kind == "short_text_with_tags":
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
            raise ValueError("请填写追问回答（不超过500字）")
        return value.strip(), value.strip(), {}
    codes = value.get("codes") if isinstance(value, dict) else value
    text = value.get("other_text", {}) if isinstance(value, dict) else {}
    if (kind == "single_choice" and not isinstance(codes, str)) or (
        kind == "multiple_choice" and not isinstance(codes, list)
    ):
        raise ValueError("追问回答类型不匹配")
    selected = codes if isinstance(codes, list) else [codes]
    options = {item["code"]: item for item in question.get("options") or []}
    if (
        not selected
        or any(not isinstance(code, str) or code not in options for code in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("追问选项无效或重复")
    if len(selected) > 1 and EXCLUSIVE_CODES.intersection(selected):
        raise ValueError("都没有、不清楚等选项不能与其他选项同时选择")
    extra = {}
    labels = []
    for code in selected:
        label = str(options[code]["label"])
        if requires_text(options[code]):
            answer = text.get(code) if isinstance(text, dict) else None
            if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 500:
                raise ValueError("请补充所选“其他”的内容（不超过500字）")
            extra[code] = answer.strip()
            label += "：" + extra[code]
        labels.append(label)
    return codes, "；".join(labels), extra
