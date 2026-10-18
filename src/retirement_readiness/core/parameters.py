"""Current retirement calculation parameters and region resolution."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from retirement_readiness.core.parameter_data import (
    ASSET_RETURN_RATES,
    CARE_COSTS,
    CITY_TO_PROVINCE,
    ECONOMIC_ASSUMPTIONS,
    MEDICAL_BY_PROVINCE,
    PENSION_BY_PROVINCE,
)
from retirement_readiness.core.questionnaire import administrative_divisions

_DIRECT_MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}
_DEFAULT_CARE = {
    "region_key": "全国默认",
    "province": "全国默认",
    "monthly_mild": 2500,
    "monthly_moderate": 6000,
    "monthly_severe": 9000,
    "annual_24h_care": 74880,
}


def economic_assumptions() -> dict[str, float]:
    return dict(ECONOMIC_ASSUMPTIONS)


def asset_return_rates() -> dict[str, float]:
    return dict(ASSET_RETURN_RATES)


@lru_cache(maxsize=1)
def _region_indexes() -> dict[str, Any]:
    divisions = administrative_divisions()
    province_by_code = {
        str(item["code"]): _strip_suffix(str(item["label"])) for item in divisions["provinces"]
    }
    city_by_code: dict[str, str] = {}
    city_to_province: dict[str, str] = {}
    for province_code, cities in divisions["citiesByProvince"].items():
        province = province_by_code[province_code]
        for item in cities:
            city = _strip_suffix(str(item["label"]))
            if city in {"市辖区", "县"}:
                city = province
            city_by_code[str(item["code"])] = city
            city_to_province[city] = province
    city_to_province.update(
        {
            _strip_suffix(city): _strip_suffix(province)
            for city, province in CITY_TO_PROVINCE.items()
        }
    )
    return {
        "province_by_code": province_by_code,
        "city_by_code": city_by_code,
        "city_to_province": city_to_province,
    }


def region_from_composite(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value or "")
    indexes = _region_indexes()
    city = indexes["city_by_code"].get(str(value.get("city_code") or ""), "")
    province = indexes["province_by_code"].get(str(value.get("province_code") or ""), "")
    return city or province


def resolve_region(value: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = region_from_composite(value).strip()
    normalized = _strip_suffix(raw)
    pension_by_province = PENSION_BY_PROVINCE
    if normalized in pension_by_province:
        return _region(
            raw,
            normalized if normalized in _DIRECT_MUNICIPALITIES else "",
            normalized,
            "direct_municipality" if normalized in _DIRECT_MUNICIPALITIES else "direct_province",
        )
    indexes = _region_indexes()
    city = next(
        (
            item
            for item in sorted(indexes["city_to_province"], key=len, reverse=True)
            if item in raw
        ),
        "",
    )
    if city:
        return _region(raw, city, indexes["city_to_province"][city], "city_alias")
    province = next((item for item in pension_by_province if item in normalized), "")
    if province:
        return _region(
            raw,
            province if province in _DIRECT_MUNICIPALITIES else "",
            province,
            "text_alias",
        )
    if fallback:
        return {
            **fallback,
            "raw": raw,
            "confidence": "low",
            "source": "fallback",
            "note": "原始地区无法识别，沿用上级 fallback 地区。",
        }
    return {
        "raw": raw,
        "city": "",
        "province": "全国默认",
        "confidence": "low",
        "source": "fallback",
        "note": "无法识别地区，使用全国默认估算口径。",
    }


def resolve_calculation_regions(answers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    places = answers.get("Q7") if isinstance(answers.get("Q7"), dict) else {}

    def place(prefix: str) -> str:
        return region_from_composite(
            {key: places.get(prefix + key) for key in ("province_code", "city_code")}
        )

    current_city, hukou_city = place("current_"), place("hukou_")
    current = _first_resolved([current_city, hukou_city])
    pension_text = {
        "hukou_city": hukou_city,
        "current_city": current_city,
        "other_city": _supplement_text(answers, "PENSION_BENEFIT_CITY", "other_city"),
    }.get(str(answers.get("PENSION_BENEFIT_CITY")), current_city)
    pension = _first_resolved([pension_text], current)
    living_text = {
        "hukou_or_hometown": hukou_city,
        "current_city": current_city,
        "other_city": _supplement_text(answers, "Q34", "other_city"),
    }.get(str(answers.get("Q34")), pension_text)
    living = _first_resolved([living_text], pension)
    medical = living
    return {
        "current": current,
        "pension": pension,
        "living": living,
        "medical": medical,
        "care": living,
    }


def get_medical_policy(region: dict[str, Any]) -> dict[str, Any]:
    return dict(MEDICAL_BY_PROVINCE.get(region["province"]) or MEDICAL_BY_PROVINCE["北京"])


def get_care_cost(region: dict[str, Any]) -> dict[str, Any]:
    for key in (region.get("city"), region.get("province")):
        match = next(
            (
                item
                for item in CARE_COSTS
                if key
                in {_strip_suffix(str(item.get(field) or "")) for field in ("city", "region_key")}
            ),
            None,
        )
        if match:
            return dict(match)
    return dict(_DEFAULT_CARE)


def _region(raw: str, city: str, province: str, source: str) -> dict[str, Any]:
    policy = PENSION_BY_PROVINCE.get(province) or {}
    return {
        "raw": raw,
        "city": city,
        "province": province,
        "admin_region_code": policy.get("admin_region_code"),
        "confidence": "high",
        "source": source,
    }


def _first_resolved(values: list[Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    for value in values:
        resolved = resolve_region(value, fallback)
        if resolved["province"] != "全国默认" and resolved["confidence"] != "low":
            return resolved
    return fallback or resolve_region("")


def _strip_suffix(value: str) -> str:
    return re.sub(
        r"(?:特别行政区|维吾尔自治区|壮族自治区|回族自治区|自治区|省|市)$", "", value
    ).strip()


def _supplement_text(answers: dict[str, Any], question_id: str, code: str) -> str:
    prefix = f"{question_id}__{code}_text"
    return str(answers.get(prefix) or answers.get(f"{prefix}__blank_1") or "")
