"""Bundled pension wage and contribution references, with explicit model proxies."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

_CODES = {
    "index_denominator": ("annual_wage_denominator",),
    "benefit_base": ("benefit_calculation_base",),
    "contribution_reference": (
        "contribution_base_limit_reference_wage",
        "social_insurance_contribution_reference_base",
        "contribution_base_upper",
    ),
}
_ENTERPRISE = {
    "enterprise_employee",
    "enterprise_employees",
    "enterprise_employee_pension_participants",
}


@lru_cache(maxsize=1)
def reference_data() -> dict[str, Any]:
    return json.loads(
        (Path(__file__).with_name("data") / "pension-parameters.json").read_text(encoding="utf-8")
    )


def _name(value: Any) -> str:
    return re.sub(r"(?:壮族|回族|维吾尔)?自治区$|省$|市$", "", str(value or "")).strip()


def reference_point(row: dict[str, Any]) -> float | None:
    def positive(key: str) -> float | None:
        try:
            value = float(row.get(key))
            return value if math.isfinite(value) and value > 0 else None
        except (ValueError, TypeError):
            return None

    value, lower, upper = (positive(key) for key in ("value_numeric", "lower_bound", "upper_bound"))
    if (
        lower is not None
        and upper is not None
        and (row.get("identification_status") == "interval_identified" or value is None)
    ):
        return (lower + upper) / 2
    return value or lower or upper


def _scope(items: list[dict[str, Any]], city: str) -> str | None:
    unique = {f"{r['regionCode']}:{r['scopeCode']}": r for r in items}
    exact = [
        key
        for key, row in unique.items()
        if city
        and not re.search("excluding|province_direct", row["scopeCode"])
        and (
            _name(re.sub(r"(?:企业职工|企业|普通).*$", "", row["scopeLabel"])) == city
            or row["scopeLabel"].startswith(city + "市")
        )
    ]
    excluded = {
        "province_excluding_shenzhen": {"深圳"},
        "province_excluding_shenyang_dalian": {"沈阳", "大连"},
    }
    remainder = [
        key
        for key, row in unique.items()
        if city and row["scopeCode"] in excluded and city not in excluded[row["scopeCode"]]
    ]
    if len(exact) == 1:
        return exact[0]
    if len(remainder) == 1:
        return remainder[0]
    if len(unique) == 1:
        key, row = next(iter(unique.items()))
        if row["scopeCode"] == "province_default" and row["regionCode"].endswith("0000"):
            return key
    return None


def _official(region: dict[str, Any], purpose: str, moment: float) -> dict[str, Any] | None:
    data, year = reference_data(), math.floor(moment)
    code = next(
        (r["regionCode"] for r in data["scopes"] if _name(r["region"]) == region["province"]),
        "",
    )
    required = [
        r
        for r in data["requirements"]
        if code
        and r["regionCode"].startswith(code[:2])
        and r["parameterCode"] in _CODES[purpose]
        and r["personnelScope"] in _ENTERPRISE
    ]
    if not required or year < min(r["policyYear"] for r in required):
        return None
    scope_year = min(year, max(r["policyYear"] for r in required))
    required = [r for r in required if r["policyYear"] == scope_year]
    scope = _scope(required, region.get("city", ""))
    regimes = {r["regime"] for r in required if f"{r['regionCode']}:{r['scopeCode']}" == scope}
    rows = [
        r
        for r in data["official"]
        if r["parameterCode"] in _CODES[purpose]
        and f"{r['regionCode']}:{r['scopeCode']}" == scope
        and r["personnelScope"] in _ENTERPRISE
        and r["admission"] == "source_audited"
        and r["monthlyAmount"] > 0
        and (r["policyYear"] == scope_year or r["regime"] in regimes)
    ]
    if not rows:
        return None
    anchor = min(rows, key=lambda r: (abs(r["policyYear"] - year), -r["policyYear"]))["policyYear"]
    day = f"{anchor}-{min(11, math.floor((moment - year) * 12 + 1e-7)) + 1:02d}-15"
    rows = [
        r
        for r in rows
        if r["policyYear"] == anchor
        and (r.get("effectiveFrom") or f"{anchor}-01-01")
        <= day
        <= (r.get("effectiveTo") or f"{anchor}-12-31")
    ]
    rows = [r for r in rows if r["parameterCode"] != "contribution_base_upper"] or rows
    if not rows:
        return None

    def duration(row: dict[str, Any]) -> int:
        return (
            date.fromisoformat(row.get("effectiveTo") or f"{anchor}-12-31")
            - date.fromisoformat(row.get("effectiveFrom") or f"{anchor}-01-01")
        ).days

    shortest = min(map(duration, rows))
    rows = [r for r in rows if duration(r) == shortest]
    amounts = {
        r["monthlyAmount"] / (3 if r["parameterCode"] == "contribution_base_upper" else 1)
        for r in rows
    }
    if len(amounts) != 1:
        return None
    return {
        "amount": amounts.pop() * 1.03 ** (year - anchor),
        "anchor_year": anchor,
        "source": rows[0]["sourceUrl"],
        "status": "official_reference"
        if year == anchor and rows[0]["parameterCode"] != "contribution_base_upper"
        else "model_proxy",
    }


@lru_cache(maxsize=1)
def _candidates() -> list[dict[str, Any]]:
    data = reference_data()
    result = []
    corrected = {
        r["evidenceId"] for r in data["official"] if r["admission"] == "excluded_role_mismatch"
    }
    codes = {code for group in _CODES.values() for code in group} | {"contribution_base_lower"}
    for row in data["estimates"]:
        code, amount = row.get("parameter_code"), reference_point(row)
        if (
            code not in codes
            or not amount
            or not re.fullmatch(r"cny_per_(month|year)|元/(月|年)", row.get("unit", ""))
        ):
            continue
        amount /= 12 if re.search("year|年", row["unit"]) else 1
        amount /= (
            3
            if code == "contribution_base_upper"
            else 0.6
            if code == "contribution_base_lower"
            else 1
        )
        result.append(
            {
                "region": _name(row.get("region_name")),
                "city": _name(row.get("scope_label")),
                "year": row["policy_year"],
                "amount": amount,
                "code": "social_insurance_contribution_reference_base"
                if row.get("evidence_id") in corrected
                else code,
                "source": row.get("source_url", ""),
                "tier": row.get("source_tier", ""),
            }
        )
    for row in data["regional_wages"]:
        amount = (
            row.get("monthly_social_wage_cny")
            or (row.get("annual_social_wage_wan") or 0) * 10000 / 12
        )
        if amount:
            result.append(
                {
                    "region": _name(row.get("province")),
                    "city": _name(row.get("city")),
                    "year": row.get("effective_year") or 2025,
                    "amount": amount,
                    "code": "regional_wage",
                    "source": row.get("official_source_url") or row.get("source_ref", ""),
                    "tier": row.get("closure_status", ""),
                }
            )
    return result


def wage_reference(region: dict[str, Any], purpose: str, moment: float) -> dict[str, Any]:
    year = math.floor(moment)
    official = _official(region, purpose, moment)
    series = None
    if (
        region["province"] == "重庆"
        and purpose in {"contribution_reference", "index_denominator"}
        and year >= 2019
    ):
        series = {2019: 5469, 2025: 88065 / 12}
    elif region["province"] == "北京" and year >= 2019:
        if purpose == "contribution_reference":
            series = dict(enumerate((7855, 8847, 9407, 10628, 11297, 11761, 11937, 12116), 2019))
        elif purpose == "benefit_base":
            series = {2024: 11883, 2025: 12049}
    elif region["province"] == "上海" and purpose == "contribution_reference" and year >= 2019:
        series = {2025: 12434}
    if official and (
        official["status"] == "official_reference" or not series or year not in series
    ):
        return official
    if series:
        anchor = min(series, key=lambda value: (abs(value - year), -value))
        return {
            "amount": series[anchor] * 1.03 ** (year - anchor),
            "anchor_year": anchor,
            "source": "地方工资参考序列",
            "status": "official_reference" if anchor == year else "model_proxy",
        }
    # Original municipal series cover periods predating the bundled audited registry.
    beijing = [
        8144,
        9579,
        11019,
        12285,
        13778,
        15726,
        18092,
        20728,
        24045,
        28348,
        32808,
        36097,
        39867,
        44715,
        48444,
        50415,
        56061,
        62677,
        69521,
        77560,
        85038,
        92477,
        101599,
    ]
    if (
        region["province"] == "北京"
        and purpose in {"contribution_reference", "index_denominator"}
        and 1996 <= year <= 2018
    ):
        return {
            "amount": beijing[year - 1996] / 12,
            "anchor_year": year,
            "source": "https://rsj.beijing.gov.cn/bm/ywml/201912/t20191206_873811.html",
            "status": "official_reference",
        }
    candidates = _candidates()
    local = [r for r in candidates if r["region"] == region["province"]]
    codes = _CODES[purpose] + (
        ("contribution_base_lower",) if purpose == "contribution_reference" else ()
    )
    specific = [r for r in local if r["code"] in codes]
    pool = (
        [r for r in specific if r["year"] == year]
        or [r for r in local if r["year"] == year]
        or specific
        or local
    )
    if pool:
        city = region.get("city", "")
        row = min(
            pool,
            key=lambda r: (
                0 if city and (r["city"] == city or r["city"].startswith(city + "市")) else 1,
                abs(r["year"] - year),
                not r["tier"].startswith("official"),
                -r["year"],
            ),
        )
        amount, anchor, source = row["amount"], row["year"], row["source"]
    else:
        provinces: dict[str, list[float]] = {}
        for row in candidates:
            if row["code"] == "regional_wage":
                provinces.setdefault(row["region"], []).append(
                    row["amount"] * 1.03 ** (2025 - row["year"])
                )
        amount, anchor, source = (
            median([median(v) for v in provinces.values()]),
            2025,
            "库内省级工资中位数",
        )
    return {
        "amount": amount * 1.03 ** (year - anchor),
        "anchor_year": anchor,
        "source": source,
        "status": "model_proxy",
    }


def current_limits(region: dict[str, Any], moment: float) -> dict[str, Any]:
    year = math.floor(moment)
    day = f"{year}-{math.floor((moment - year) * 12 + 0.5) + 1:02d}-01"
    if region["province"] == "北京" and 2025.5 <= moment < 2027.5:
        wage, lower = (12116, 7270) if moment >= 2026.5 else (11937, 7162)
        return {
            "wage": wage,
            "lower": lower,
            "upper": wage * 3,
            "period": "2026-07/2027-06" if moment >= 2026.5 else "2025-07/2026-06",
        }
    rows = reference_data()["estimates"]
    upper_rows = [
        r
        for r in rows
        if r.get("region_name") == region["province"]
        and r.get("personnel_scope") == "enterprise_employee"
        and r.get("scope_code") == "province_default"
        and r.get("parameter_code") == "contribution_base_upper"
        and (r.get("value_numeric") or 0) > 0
        and (r.get("effective_from") or "") <= day <= (r.get("effective_to") or "9999")
    ]
    upper_rows.sort(
        key=lambda r: (
            r.get("effective_from") or "",
            bool(re.search(r"gov\.cn", r.get("source_url") or "")),
        ),
        reverse=True,
    )
    for upper in upper_rows:
        lower = next(
            (
                r
                for r in rows
                if r.get("parameter_code") == "contribution_base_lower"
                and all(
                    r.get(k) == upper.get(k)
                    for k in (
                        "region_name",
                        "policy_year",
                        "personnel_scope",
                        "scope_code",
                        "effective_from",
                        "effective_to",
                        "source_url",
                    )
                )
                and 0 < (r.get("value_numeric") or 0) <= upper["value_numeric"]
            ),
            None,
        )
        if lower:
            return {
                "wage": upper["value_numeric"] / 3,
                "lower": lower["value_numeric"],
                "upper": upper["value_numeric"],
                "period": f"{upper['effective_from']}/{upper['effective_to']}",
            }
    wage = wage_reference(region, "contribution_reference", moment)["amount"]
    return {
        "wage": wage,
        "lower": wage * 0.6,
        "upper": wage * 3,
        "period": f"{year}年规划代理",
    }


def historical_limits(region: dict[str, Any], moment: float) -> tuple[float, float]:
    data = reference_data()
    year = math.floor(moment)
    day = f"{year}-{min(11, math.floor((moment - year) * 12 + 1e-6)) + 1:02d}-15"
    rows = [
        r
        for r in data["estimates"]
        if r.get("parameter_code") in {"contribution_base_lower", "contribution_base_upper"}
        and not (r.get("region_name") == "北京" and r.get("policy_year") == 2025)
    ] + data["bound_overrides"]

    def rank(row: dict[str, Any]) -> int:
        if row.get("region_code") == "CN":
            return 1
        if row.get("region_name") and _name(row["region_name"]) != region["province"]:
            return 0
        if (
            not row.get("region_name")
            and str(row.get("region_code", ""))[:2] != str(region.get("admin_region_code", ""))[:2]
        ):
            return 0
        scope, label = row.get("scope_code", ""), row.get("scope_label", "")
        if re.match(r"province_default(?:_|$)|municipality_default$|tibet_region_all$", scope):
            return 2
        city = region.get("city", "")
        if not city or label == scope or not re.search(r"[\u4e00-\u9fff]", label):
            return 0
        if re.search("excluding|except", scope):
            return 0 if city in label else 3
        return 4 if city in label else 0

    def select(code: str) -> dict[str, Any] | None:
        pool = [
            r
            for r in rows
            if r.get("parameter_code") == code
            and r.get("personnel_scope") in _ENTERPRISE
            and rank(r) > 0
            and (r.get("effective_from") or f"{r['policy_year']}-01-01")
            <= day
            <= (r.get("effective_to") or "9999-12-31")
            and reference_point(r)
        ]

        def key(r: dict[str, Any]) -> tuple:
            start = date.fromisoformat(r.get("effective_from") or f"{r['policy_year']}-01-01")
            end = date.fromisoformat(r.get("effective_to") or "9999-12-31")
            return (
                -rank(r),
                (end - start).days,
                not str(r.get("source_tier", "")).startswith("official"),
                -start.toordinal(),
            )

        return min(pool, key=key) if pool else None

    def money(row: dict[str, Any] | None) -> float | None:
        if not row or not re.fullmatch(r"cny_per_(month|year)|元/(月|年)", row.get("unit", "")):
            return None
        return reference_point(row) / (12 if re.search("year|年", row["unit"]) else 1)

    lower, upper = (
        money(select("contribution_base_lower")),
        money(select("contribution_base_upper")),
    )
    wage = (
        upper / 3 if upper else wage_reference(region, "contribution_reference", moment)["amount"]
    )

    def ratio(code: str, fallback: float) -> float:
        row = select(code)
        return (
            reference_point(row) / (100 if row["unit"] == "percent" else 1)
            if row and row.get("unit") in {"percent", "ratio"}
            else fallback
        )

    return lower or wage * ratio("contribution_base_min_ratio", 0.6), upper or wage * ratio(
        "contribution_base_max_ratio", 3
    )


def wage_boundaries(region: dict[str, Any]) -> list[float]:
    rows = sorted(
        [r for r in reference_data()["scopes"] if _name(r["region"]) == region["province"]],
        key=lambda r: r["effectiveFrom"],
    )
    result = []
    previous = None
    for row in rows:
        signature = (row["scopeCodes"], row["wageConcept"], row["formulaVersion"])
        if signature != previous:
            result.append(int(row["effectiveFrom"][:4]))
        previous = signature
    return result


def resident_base(region: dict[str, Any], year: int) -> float:
    data = reference_data()["resident"]
    rows = [
        r
        for r in data["rows"]
        if r.get("usable_for_estimation_engine")
        and isinstance(r.get("local_basic_pension_cny_month"), (int, float))
        and _name(r.get("province")) == region["province"]
        and (not r.get("city") or _name(r["city"]) in {region.get("city"), region["province"]})
        and r.get("effective_from")
        and int(r["effective_from"][:4]) <= year
    ]
    rows.sort(key=lambda r: (bool(r.get("city")), r["effective_from"]), reverse=True)
    floor = data["floor"]["amount_cny_month"] if year >= data["floor"]["effective_year"] else 0
    return max(floor, rows[0]["local_basic_pension_cny_month"] if rows else 0)
