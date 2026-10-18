"""Deterministic selection for the active retirement report rule pack."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from retirement_readiness.core.rule_catalog import RuleCatalog

_REPORT_RULE_CANDIDATE_LIMIT_PER_OBJECT = 100
_MODEL_RECOMMENDATION_LIMIT_PER_OBJECT = 12
_AGGREGATE_SCORE_FACT_IDS = frozenset(
    {"cognition_low", "health_low", "activity_low", "care_plan_low"}
)


def select_report_rules(
    catalog: RuleCatalog,
    facts: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    candidates = report_rule_candidates(catalog, facts)
    return {
        "problems": candidates["problems"],
        "recommendations": candidates["fallback_recommendations"],
    }


def report_rule_candidates(
    catalog: RuleCatalog,
    facts: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    recommendations = filter_candidates(
        catalog.recommendations,
        facts,
        limit_per_object=_REPORT_RULE_CANDIDATE_LIMIT_PER_OBJECT,
    )
    problems = filter_candidates(
        catalog.problems,
        facts,
        limit_per_object=_REPORT_RULE_CANDIDATE_LIMIT_PER_OBJECT,
    )
    recommendation_ids = {item["rule"]["ruleId"] for item in recommendations}
    missing_primary_fix = [
        item
        for item in problems
        if not any(
            relation["problemId"] == item["rule"]["ruleId"]
            and relation["relationType"] == "primary_fix"
            and relation["recommendationId"] in recommendation_ids
            for relation in catalog.relationships
        )
    ]
    if missing_primary_fix:
        missing_ids = ",".join(item["rule"]["ruleId"] for item in missing_primary_fix)
        raise ValueError(f"PROBLEM_CANDIDATE_WITHOUT_ELIGIBLE_PRIMARY_FIX: {missing_ids}")
    selected_problems = _select_per_object(problems, limit=3)
    recommendation_pool = _recommendation_pool(
        selected_problems,
        recommendations,
        catalog.relationships,
    )
    fallback_recommendations = _cover_primary_fixes(
        selected_problems,
        recommendation_pool,
        catalog.relationships,
        limit=3,
    )
    return {
        "problems": selected_problems,
        "recommendation_candidates": recommendation_pool,
        "fallback_recommendations": fallback_recommendations,
    }


def validate_recommendation_choices(
    rule_ids: Any,
    *,
    problems: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    relationships: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Validate the model choice, then add only missing primary-fix coverage."""
    if not isinstance(rule_ids, list):
        raise ValueError("CALL_A_RECOMMENDATION_SELECTION_NOT_ARRAY")
    by_id = {item["rule"]["ruleId"]: item for item in candidates}
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for value in rule_ids:
        candidate = by_id.get(str(value))
        if not candidate or candidate in selected:
            raise ValueError(f"CALL_A_RECOMMENDATION_SELECTION_INVALID:{value}")
        object_code = candidate["rule"]["objectCode"]
        if counts[object_code] >= 3 or any(_conflict(candidate, current) for current in selected):
            raise ValueError(f"CALL_A_RECOMMENDATION_SELECTION_CONFLICT:{value}")
        counts[object_code] += 1
        selected.append(candidate)
    return _complete_primary_fixes(problems, candidates, relationships, selected=selected, limit=3)


def _complete_primary_fixes(
    problems: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    relationships: tuple[dict[str, Any], ...],
    *,
    selected: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    completed = list(selected)
    for object_code in dict.fromkeys(item["rule"]["objectCode"] for item in problems):
        object_problem_ids = {
            item["rule"]["ruleId"] for item in problems if item["rule"]["objectCode"] == object_code
        }
        selected_ids = {item["rule"]["ruleId"] for item in completed}
        uncovered = {
            problem_id
            for problem_id in object_problem_ids
            if not any(
                relation["problemId"] == problem_id
                and relation["relationType"] == "primary_fix"
                and relation["recommendationId"] in selected_ids
                for relation in relationships
            )
        }
        candidates = [item for item in pool if item["rule"]["objectCode"] == object_code]
        while (
            uncovered
            and sum(item["rule"]["objectCode"] == object_code for item in completed) < limit
        ):
            ranked = []
            for index, candidate in enumerate(candidates):
                if candidate in completed or any(_conflict(candidate, item) for item in completed):
                    continue
                covered = {
                    relation["problemId"]
                    for relation in relationships
                    if relation["recommendationId"] == candidate["rule"]["ruleId"]
                    and relation["relationType"] == "primary_fix"
                    and relation["problemId"] in uncovered
                }
                if covered:
                    ranked.append(
                        (
                            -len(covered),
                            -max(
                                relation["priority"]
                                for relation in relationships
                                if relation["recommendationId"] == candidate["rule"]["ruleId"]
                                and relation["problemId"] in covered
                            ),
                            index,
                            candidate,
                            covered,
                        )
                    )
            if not ranked:
                break
            chosen = min(ranked, key=lambda item: item[:3])
            completed.append(chosen[3])
            uncovered.difference_update(chosen[4])
        if uncovered:
            raise ValueError(
                "RULE_SELECTION_COVERAGE_COMPLETION_FAILED: " + ",".join(sorted(uncovered))
            )
    return completed


def filter_candidates(
    rules: tuple[dict[str, Any], ...],
    facts: dict[str, dict[str, Any]],
    *,
    limit_per_object: int = 12,
) -> list[dict[str, Any]]:
    candidates = []
    for rule in rules:
        if _disabled(rule, facts) or not _evidence_satisfied(rule, facts):
            continue
        evidence_ids = dict.fromkeys(
            [
                *rule["keyData"],
                *rule["evidenceWhen"]["all"],
                *rule["evidenceWhen"]["any"],
            ]
        )
        evidence = [
            facts[fact_id]
            for fact_id in evidence_ids
            if _true(facts, fact_id)
            and (rule["kind"] != "problem" or fact_id not in _AGGREGATE_SCORE_FACT_IDS)
        ]
        if not evidence:
            continue
        candidates.append(
            {
                "rule": rule,
                "evidence": evidence,
                "applies_score": sum(
                    2 for fact_id in rule["appliesWhen"]["all"] if _true(facts, fact_id)
                )
                + sum(1 for fact_id in rule["appliesWhen"]["any"] if _true(facts, fact_id)),
            }
        )
    candidates.sort(
        key=lambda item: (
            -item["applies_score"],
            -item["rule"]["priority"],
            item["rule"]["sourceOrder"],
        )
    )
    counts: dict[str, int] = defaultdict(int)
    result = []
    for candidate in candidates:
        object_code = candidate["rule"]["objectCode"]
        if counts[object_code] >= limit_per_object:
            continue
        counts[object_code] += 1
        result.append(candidate)
    return result


def _disabled(rule: dict[str, Any], facts: dict[str, dict[str, Any]]) -> bool:
    conditions = rule["disabledWhen"]
    if any(_true(facts, fact_id) for fact_id in conditions["any"]):
        return True
    if conditions["all"] and all(_true(facts, fact_id) for fact_id in conditions["all"]):
        return True
    return bool(
        conditions["none"] and all(not _true(facts, fact_id) for fact_id in conditions["none"])
    )


def _evidence_satisfied(rule: dict[str, Any], facts: dict[str, dict[str, Any]]) -> bool:
    conditions = rule["evidenceWhen"]
    return all(_true(facts, fact_id) for fact_id in conditions["all"]) and (
        not conditions["any"] or any(_true(facts, fact_id) for fact_id in conditions["any"])
    )


def _select_per_object(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    dedupe: set[str] = set()
    for candidate in candidates:
        rule = candidate["rule"]
        if counts[rule["objectCode"]] >= limit or rule["dedupeKey"] in dedupe:
            continue
        if any(_conflict(candidate, current) for current in selected):
            continue
        selected.append(candidate)
        counts[rule["objectCode"]] += 1
        dedupe.add(rule["dedupeKey"])
    return selected


def _recommendation_pool(
    problems: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    relationships: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    problem_ids = {item["rule"]["ruleId"] for item in problems}
    linked: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relation in relationships:
        if relation["problemId"] in problem_ids:
            linked[relation["recommendationId"]].append(relation)
    weights = {"primary_fix": 3, "supporting_fix": 2, "risk_mitigation": 1}

    def rank(candidate: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
        relations = linked.get(candidate["rule"]["ruleId"], [])
        primary = len(
            {item["problemId"] for item in relations if item["relationType"] == "primary_fix"}
        )
        coverage = len({item["problemId"] for item in relations})
        relation_score = max(
            (weights[item["relationType"]] * 100 + item["priority"] for item in relations),
            default=0,
        )
        return (
            -primary,
            -coverage,
            -relation_score,
            -candidate["applies_score"],
            -candidate["rule"]["priority"],
            candidate["rule"]["sourceOrder"],
        )

    ranked = sorted(recommendations, key=rank)
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for candidate in ranked:
        object_code = candidate["rule"]["objectCode"]
        linked_to_problem = candidate["rule"]["ruleId"] in linked
        if not linked_to_problem and counts[object_code] >= _MODEL_RECOMMENDATION_LIMIT_PER_OBJECT:
            continue
        selected.append(candidate)
        counts[object_code] += 1
    return selected


def _cover_primary_fixes(
    problems: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    relationships: tuple[dict[str, Any], ...],
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for problem in problems:
        by_object[problem["rule"]["objectCode"]].append(problem)
    for object_code, object_problems in by_object.items():
        uncovered = {item["rule"]["ruleId"] for item in object_problems}
        candidates = [item for item in pool if item["rule"]["objectCode"] == object_code]
        while (
            uncovered
            and len([item for item in selected if item["rule"]["objectCode"] == object_code])
            < limit
        ):
            ranked = []
            for index, candidate in enumerate(candidates):
                if candidate in selected or any(_conflict(candidate, item) for item in selected):
                    continue
                relations = [
                    item
                    for item in relationships
                    if item["recommendationId"] == candidate["rule"]["ruleId"]
                    and item["relationType"] == "primary_fix"
                    and item["problemId"] in uncovered
                ]
                coverage = len({item["problemId"] for item in relations})
                if coverage:
                    ranked.append(
                        (
                            -coverage,
                            -max(item["priority"] for item in relations),
                            index,
                            candidate,
                        )
                    )
            if not ranked:
                break
            chosen = min(ranked, key=lambda item: item[:3])[3]
            selected.append(chosen)
            uncovered.difference_update(
                relation["problemId"]
                for relation in relationships
                if relation["recommendationId"] == chosen["rule"]["ruleId"]
                and relation["relationType"] == "primary_fix"
            )
        if uncovered:
            raise ValueError(
                "RULE_SELECTION_COVERAGE_COMPLETION_FAILED: " + ",".join(sorted(uncovered))
            )
    return selected


def _conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_rule, right_rule = left["rule"], right["rule"]
    return (
        left_rule["dedupeKey"] == right_rule["dedupeKey"]
        or right_rule["ruleId"] in left_rule["conflictsWith"]
        or left_rule["ruleId"] in right_rule["conflictsWith"]
    )


def _true(facts: dict[str, dict[str, Any]], fact_id: str) -> bool:
    return facts.get(fact_id, {}).get("state") == "true"
