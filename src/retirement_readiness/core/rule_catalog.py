"""Load and validate the single active retirement report rule pack."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).with_name("data")


@dataclass(frozen=True)
class RuleCatalog:
    rule_pack_id: str
    problems: tuple[dict[str, Any], ...]
    recommendations: tuple[dict[str, Any], ...]
    relationships: tuple[dict[str, Any], ...]
    fact_ids: frozenset[str]
    limits: dict[str, int]


@lru_cache(maxsize=1)
def active_rule_catalog() -> RuleCatalog:
    release = _read("report-rule-release.json")
    problems = tuple(_read("existing-problems.json")["rules"])
    recommendations = tuple(_read("recommendations.json")["rules"])
    relationships = tuple(_read("relationships.json")["relations"])
    fact_ids = frozenset(item["factId"] for item in _read("facts.json")["facts"])
    expected = release["expectedCounts"]
    actual = {
        "problems": len(problems),
        "recommendations": len(recommendations),
        "relationships": len(relationships),
        "facts": len(fact_ids),
    }
    if actual != expected:
        raise RuntimeError(f"退休报告规则包数量不匹配: expected={expected}, actual={actual}")
    _validate_references(problems, recommendations, relationships, fact_ids)
    return RuleCatalog(
        rule_pack_id=release["rulePackId"],
        problems=problems,
        recommendations=recommendations,
        relationships=relationships,
        fact_ids=fact_ids,
        limits=dict(release["limits"]),
    )


def _read(name: str) -> dict[str, Any]:
    return json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))


def _validate_references(
    problems: tuple[dict[str, Any], ...],
    recommendations: tuple[dict[str, Any], ...],
    relationships: tuple[dict[str, Any], ...],
    fact_ids: frozenset[str],
) -> None:
    referenced_facts: set[str] = set()
    for rule in (*problems, *recommendations):
        referenced_facts.update(rule["keyData"])
        for condition in ("appliesWhen", "evidenceWhen", "disabledWhen"):
            for values in rule[condition].values():
                referenced_facts.update(values)
    if referenced_facts - fact_ids or fact_ids - referenced_facts - {
        "care_resource_followup_none",
        "public_ltc_knowledge_response",
        "public_ltc_status_response",
    }:
        raise RuntimeError(
            "退休报告事实契约不匹配: "
            f"missing={sorted(referenced_facts - fact_ids)}, unused={sorted(fact_ids - referenced_facts)}"
        )

    problem_ids = {rule["ruleId"] for rule in problems}
    recommendation_ids = {rule["ruleId"] for rule in recommendations}
    invalid = [
        relation["relationId"]
        for relation in relationships
        if relation["problemId"] not in problem_ids
        or relation["recommendationId"] not in recommendation_ids
    ]
    if invalid:
        raise RuntimeError(f"退休报告规则关系引用无效: {invalid}")
    primary_problem_ids = {
        relation["problemId"]
        for relation in relationships
        if relation["relationType"] == "primary_fix"
    }
    if missing := problem_ids - primary_problem_ids:
        raise RuntimeError(f"退休报告问题缺少主要建议: {sorted(missing)}")
