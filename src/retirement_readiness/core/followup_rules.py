"""Current-questionnaire fallback rules for AI dynamic follow-ups."""

from __future__ import annotations

import json
import re
from typing import Any

from retirement_readiness.core.followup_modules import (
    active_module_questions,
    get_followup_module,
)


def _question_followups(
    parent_id: str, answers: dict[str, Any]
) -> tuple[list[dict[str, Any]], str, str]:
    """Return reference-equivalent deterministic candidates for one display question."""
    current = answers.get(parent_id)
    candidates: list[dict[str, Any]] = []
    trigger = "当前答案显示存在评估框架关键字段缺口。"
    priority = "medium"

    if (parent_id == "Q8" and current == "flexible") or (
        parent_id == "B8_1"
        and current in {"platform_worker", "self_employed_business", "part_time_temp", "other"}
    ):
        trigger = "灵活就业类型提示收入波动可能影响连续缴费和养老储蓄。"
        candidates.append(
            _choice(
                "occupation_income_volatility",
                "过去一年，您的主要收入是否出现过连续2个月以上明显低于平时？",
                ("yes_over_50_drop", "有，低于平时一半以上", "income_volatility_high"),
                (
                    "yes_moderate_drop",
                    "有，但下降幅度可承受",
                    "income_volatility_medium",
                ),
                ("no_stable", "没有，收入相对稳定", "income_volatility_low"),
                ("not_sure", "说不清", "income_volatility_unclear"),
            )
        )

    if parent_id in {"PENSION_BENEFIT_CITY", "SP_PENSION_BENEFIT_CITY"} and (
        current == "not_sure"
        or re.search(r"不清楚|未确定|不知道|待定", json.dumps(current, ensure_ascii=False))
    ):
        trigger = "养老金领取城市尚未确定或可能与参保路径不一致。"
        priority = "high"
        candidates.append(
            _choice(
                "benefit_city_eligibility",
                "您是否已核实计划领取养老金城市的条件是否大致匹配？",
                ("verified_match", "已核实，条件基本匹配", "benefit_city_verified"),
                ("verified_not_match", "核实过，可能不匹配", "benefit_city_mismatch"),
                ("not_verified", "还没有核实", "benefit_city_not_verified"),
                ("not_sure_how", "不清楚怎么核实", "benefit_city_verify_unclear"),
            )
        )

    saving_intent = answers.get("Q16") in {
        "probably_yes",
        "definitely_yes",
    } or answers.get("Q18") in {"important", "very_important"}
    if (parent_id == "Q10" and current == "no" and saving_intent) or (
        parent_id == "Q10_1" and current in {"none", "lt_1000", "not_sure"}
    ):
        trigger = "个人养老金参与或缴存较弱，需要识别可执行障碍。"
        candidates.append(
            _choice(
                "personal_pension_barrier",
                "影响您持续缴存个人养老金的主要原因更接近哪一项？",
                (
                    "cashflow_pressure",
                    "收入波动或现金流紧张",
                    "personal_pension_barrier_cashflow",
                ),
                ("policy_unclear", "规则不够清楚", "personal_pension_barrier_policy"),
                (
                    "product_concern",
                    "担心收益或锁定期",
                    "personal_pension_barrier_product",
                ),
                ("not_priority", "目前优先级不高", "personal_pension_barrier_priority"),
            )
        )

    if parent_id == "Q27":
        values = _inline_numbers(answers, "Q27", 10)
        liquid = sum(values[:6])
        volatile = values[2] + values[3]
        if (liquid >= 50 and volatile / max(1, liquid) >= 0.3) or values[7] >= 150:
            trigger = "资产结构较重要，但基础问卷未确认可动用比例和变现意愿。"
            candidates.append(
                _choice(
                    "asset_liquidity_for_retirement",
                    "如遇医疗或照护大额支出，您愿意动用金融资产的大致比例是多少？",
                    ("lt_20", "不到20%", "retirement_asset_buffer_low"),
                    ("20_50", "约20%-50%", "retirement_asset_buffer_medium"),
                    ("gt_50", "超过50%", "retirement_asset_buffer_high"),
                    ("not_sure", "还没有划分用途", "retirement_asset_buffer_unclear"),
                )
            )

    if parent_id == "Q29_1" and sum(_inline_numbers(answers, "Q29_1", 3)) >= 30:
        trigger = "未付贷款余额较高，可能挤出退休储备。"
        priority = "high"
        candidates.append(
            _choice(
                "debt_repayment_schedule",
                "按当前计划，主要债务能否在退休前基本还清？",
                (
                    "yes_before_retirement",
                    "可以在退休前还清",
                    "debt_clear_before_retirement",
                ),
                (
                    "partly_after_retirement",
                    "可能有一部分延续到退休后",
                    "debt_after_retirement",
                ),
                ("depends_income", "取决于未来收入", "debt_clear_uncertain_income"),
                ("not_sure", "不清楚", "debt_schedule_unclear"),
            )
        )

    if parent_id == "Q31" and current in {"none", "not_sure"}:
        trigger = "基本医保缺失或不确定会显著影响医疗支出测算。"
        priority = "high"
        candidates.append(
            _choice(
                "medical_reimbursement_certainty",
                "当前医保不确定或未参保的主要原因是哪一项？",
                ("affordability", "缴费压力", "medical_barrier_affordability"),
                ("policy_unclear", "不了解参保政策", "medical_barrier_policy"),
                (
                    "location_gap",
                    "参保地、户籍地或工作地衔接问题",
                    "medical_barrier_location",
                ),
                ("not_needed", "觉得暂时不需要", "medical_barrier_not_needed"),
            )
        )

    if parent_id == "Q33" and isinstance(current, list) and "never_happened" not in current:
        trigger = "医保使用障碍会影响实际报销比例和自付压力。"
        candidates.append(_medical_path_question(answers, cross_region=False))

    if parent_id == "Q34" and current in {"other_city", "no_fixed"}:
        trigger = "计划养老地可能与医保参保地不同，需要确认异地就医路径。"
        candidates.append(
            _choice(
                "medical_reimbursement_certainty",
                "若未来不在医保参保地长期养老，异地就医备案是否已确认？",
                ("confirmed", "已确认可备案和结算", "cross_region_medical_confirmed"),
                ("only_heard", "只听说过，还没核实", "cross_region_medical_partly"),
                ("not_confirmed", "还没有确认", "cross_region_medical_unconfirmed"),
                ("not_sure_place", "养老地还没定", "cross_region_medical_unclear"),
            )
        )

    if parent_id == "Q35" and isinstance(current, list) and "none" not in current:
        trigger = "慢病信息显示常规医疗支出需要控制水平解释变量。"
        candidates.append(
            _choice(
                "chronic_disease_control",
                "对已诊断疾病，您目前的控制情况更接近哪一项？",
                (
                    "stable_followup",
                    "规律复诊用药，指标基本稳定",
                    "chronic_control_stable",
                ),
                (
                    "sometimes_irregular",
                    "偶尔漏诊漏药或指标波动",
                    "chronic_control_irregular",
                ),
                ("poor_control", "控制不理想或已有并发症", "chronic_control_poor"),
                ("not_sure", "不清楚控制情况", "chronic_control_unclear"),
            )
        )

    if parent_id == "Q36" and current == "gte_2":
        trigger = "近一年多次住院会影响医疗现金流，需要补充出院后复查与自付压力。"
        candidates.append(
            _choice(
                "chronic_disease_control",
                "最近一次出院后，复查安排和自付费用压力更接近哪一项？",
                ("stable_followup", "复查安排清楚，费用可承受", "post_hospital_stable"),
                (
                    "cost_pressure",
                    "复查清楚，但自付费用有压力",
                    "post_hospital_cost_pressure",
                ),
                ("followup_unclear", "复查安排不太清楚", "post_hospital_unclear"),
                ("not_sure", "不清楚或还没整理", "post_hospital_not_sure"),
            )
        )

    health_gap = (
        (parent_id == "Q37" and current in {"daily_lt_10", "daily_gte_10"})
        or (parent_id == "Q38" and current == "weekly_gte_4")
        or (parent_id == "Q39" and current == "no")
        or (parent_id == "Q40" and current in {"never", "rare_no_regular"})
        or (parent_id == "Q41" and current in {"only_uncomfortable", "never"})
        or (parent_id == "Q42" and current in {"0", "1_2"})
    )
    if health_gap:
        trigger = "健康行为存在可干预短板，需要识别最现实的执行障碍。"
        candidates.append(
            _choice(
                "health_behavior_barrier",
                "如果要改善运动、体检或生活习惯，当前最大障碍是哪一项？",
                ("time_workload", "工作时间或照护负担", "health_barrier_time"),
                ("motivation", "动力不足或难坚持", "health_barrier_motivation"),
                ("physical_limit", "身体条件限制", "health_barrier_physical"),
                ("no_barrier", "没有明显障碍", "health_barrier_none"),
            )
        )

    if (parent_id == "KIN_SUPPORT" and current in {"0", "1_2"}) or (
        parent_id == "Q45" and current == "0"
    ):
        trigger = "社会支持网络较弱，需要明确紧急联系人和代理决策安排。"
        candidates.append(
            _choice(
                "social_support_gap",
                "若突发住院或失能，谁能第一时间协助您联系医院并处理费用？",
                ("family_clear", "家人，责任比较明确", "support_agent_family_clear"),
                ("friend_or_neighbor", "朋友/邻居，可临时协助", "support_agent_friend"),
                ("paid_service", "计划依靠付费服务", "support_agent_paid_service"),
                ("no_clear_agent", "目前没有明确人选", "support_agent_none"),
            )
        )

    if parent_id == "Q46" and current is not None:
        trigger = (
            "尚未考虑失能照护方式，需要先确认可承受费用边界。"
            if current == "not_considered"
            else "已选择照护方式，但基础问卷缺少可持续月预算上限。"
        )
        priority = "high" if current == "not_considered" else "medium"
        candidates.append(
            _choice(
                "care_budget_capacity",
                "如需长期照护，您或家庭可持续承担的月费用上限更接近哪一档？",
                ("lt_3000", "3000元以内/月", "care_budget_low"),
                ("3000_6000", "3000-6000元/月", "care_budget_mid"),
                ("6000_10000", "6000-10000元/月", "care_budget_high"),
                (
                    "gt_10000_or_unclear",
                    "10000元以上或还没概念",
                    "care_budget_high_or_unclear",
                ),
            )
        )

    if parent_id in {"Q46_1", "Q46_2", "Q46_3", "Q46_4"} and (
        (isinstance(current, list) and ("none" in current or len(current) <= 1))
        or current == "none"
    ):
        trigger = "照护资源或家庭照护能力了解不足，需要补充可核验资源。"
        candidates.append(
            _choice(
                "care_resource_readiness",
                "下一步最容易核验的照护资源是哪一类？",
                (
                    "specific_institution",
                    "具体机构或社区服务",
                    "care_resource_institution",
                ),
                ("caregiver_channel", "护工渠道和报价", "care_resource_caregiver"),
                ("family_skill", "家人照护时间和技能", "care_resource_family_skill"),
                ("no_resource", "目前没有可核验资源", "care_resource_none"),
            )
        )

    if parent_id in {
        "PENSION_BENEFIT_CITY",
        "SP_PENSION_BENEFIT_CITY",
    } and _contains_overseas_signal(current):
        trigger = "答案显示存在海外经历，需要确认境外养老资源是否影响退休准备。"
        priority = "high"
        candidates.append(
            _choice(
                "overseas_retirement_account",
                "您是否保留境外养老金、雇主年金、退休账户或可投资账户？",
                (
                    "pension_or_social_security",
                    "有境外养老金或社保记录",
                    "overseas_pension_resource",
                ),
                (
                    "retirement_or_investment",
                    "有退休或投资账户",
                    "overseas_account_resource",
                ),
                ("none", "没有", "no_overseas_resource"),
                ("not_sure", "不清楚", "overseas_resource_unclear"),
            )
        )
    return candidates, trigger, priority


def module_fallback_candidates(
    module_id: str, answers: dict[str, Any]
) -> tuple[list[dict[str, Any]], str, str]:
    """Return deterministic candidates only for a completed module."""
    module = get_followup_module(module_id)
    if not module:
        return [], "本模块现有信息已足够，不需要补充问题。", "low"
    allowed = set(module["allowedFrameworkFields"])
    candidates: list[dict[str, Any]] = []
    trigger = "本模块现有信息已足够，不需要补充问题。"
    priority = "low"
    ranks = {"low": 1, "medium": 2, "high": 3}
    for question in active_module_questions(module_id, answers):
        items, candidate_trigger, candidate_priority = _question_followups(
            str(question["id"]), answers
        )
        if ranks[candidate_priority] > ranks[priority]:
            priority = candidate_priority
            trigger = candidate_trigger
        candidates.extend(
            item
            for item in items
            if all(field in allowed for field in item.get("framework_fields") or [])
        )
    return candidates, trigger, priority


def followup_needs(
    answers: dict[str, Any], followups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    used_fields = {
        str(field) for item in followups for field in item.get("target_framework_fields") or []
    }
    needs: list[dict[str, Any]] = []
    for parent_id in answers:
        candidates, trigger, priority = _question_followups(parent_id, answers)
        for candidate in candidates:
            field = candidate["framework_fields"][0]
            if field in used_fields or any(item["field_code"] == field for item in needs):
                continue
            needs.append(
                {
                    "field_code": field,
                    "priority": priority,
                    "reason": trigger,
                    "related_question_ids": [parent_id],
                }
            )
    return needs


def _choice(field: str, text: str, *options: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "question_text": text,
        "question_type": "single_choice",
        "framework_fields": [field],
        "options": [{"code": code, "label": label, "tags": [tag]} for code, label, tag in options],
    }


def _medical_path_question(answers: dict[str, Any], *, cross_region: bool) -> dict[str, Any]:
    has_chronic = isinstance(answers.get("Q35"), list) and "none" not in answers["Q35"]
    text = (
        "下一次异地就医或慢病购药前，您是否已有明确报销路径？"
        if has_chronic or cross_region
        else "下一次就医或购药前，您是否已有明确医保报销路径？"
    )
    return _choice(
        "medical_reimbursement_certainty",
        text,
        ("clear_path", "有，备案和报销路径清楚", "medical_path_clear"),
        ("partly_clear", "大致知道，但细节不清楚", "medical_path_partly"),
        ("no_path", "还没有明确路径", "medical_path_gap"),
        ("not_applicable", "近期不涉及异地或慢病购药", "medical_path_not_applicable"),
    )


def _inline_numbers(answers: dict[str, Any], question_id: str, count: int) -> list[float]:
    values: list[float] = []
    for index in range(1, count + 1):
        raw = answers.get(f"{question_id}__blank_{index}")
        try:
            values.append(float(str(raw).replace(",", "")) if raw not in {None, "", "/"} else 0)
        except (TypeError, ValueError):
            values.append(0)
    return values


def _contains_overseas_signal(value: Any) -> bool:
    return bool(
        re.search(
            r"海外|境外|国外|港澳台|香港|澳门|台湾|新加坡|日本|韩国|美国|加拿大|澳大利亚|英国|德国|法国|移民|外籍",
            json.dumps(value, ensure_ascii=False),
        )
    )
