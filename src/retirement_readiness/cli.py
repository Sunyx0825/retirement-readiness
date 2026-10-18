"""Run the survey assessment core on a JSON household snapshot."""

import argparse
import asyncio
import json
from datetime import date
from html import escape
from pathlib import Path

from .pipeline import assess
from .runtime import ChatModel, model_session
from .tools import ask_followup, validate_questionnaire


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON with answers and optional followups")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 1))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument(
        "--llm", action="store_true", help="Enable live AI with LLM_* environment variables"
    )
    parser.add_argument("--followup-module", help="Generate a follow-up bundle instead of a report")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    answers = snapshot.get("answers", snapshot)
    followups = snapshot.get("followups", [])
    args.output.mkdir(parents=True, exist_ok=True)
    validation = validate_questionnaire(answers)
    write_json(args.output / "validation.json", validation)
    if args.validate_only:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return
    model = ChatModel.from_environment() if args.llm else None
    if args.followup_module:
        with model_session(model):
            bundle = asyncio.run(
                ask_followup(
                    answers,
                    module_id=args.followup_module,
                    followups=followups,
                    events=snapshot.get("followup_events", []),
                )
            )
        write_json(args.output / "followup.json", bundle)
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    calculations, report = asyncio.run(
        assess(
            answers,
            as_of=args.as_of,
            followups=followups,
            model=model,
        )
    )
    write_json(args.output / "calculations.json", calculations)
    write_json(args.output / "report.json", report)
    (args.output / "report.md").write_text(report["markdown"], encoding="utf-8")
    html = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>家庭退休准备评估</title><style>"
        "body{max-width:940px;margin:48px auto;padding:0 24px;color:#172b4d;"
        'font-family:system-ui,"Microsoft YaHei",sans-serif;line-height:1.8}'
        "pre{white-space:pre-wrap;font:inherit}small{color:#52677c}"
        "</style><h1>家庭退休准备评估</h1><small>"
        f"评估月份：{args.as_of:%Y-%m} · 运行模式：{escape(report['mode'])}"
        "</small><pre>" + escape(report["markdown"]) + "</pre></html>"
    )
    (args.output / "report.html").write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "coverage": calculations["financial_indices"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["mode"] == "offline_fallback":
        parser.exit(2, "AI narration failed; the deterministic assessment was saved.\n")


if __name__ == "__main__":
    main()
