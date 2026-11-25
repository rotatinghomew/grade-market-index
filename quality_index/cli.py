from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import Project, clean_for_json
from .report import render_report, render_trace
from .dashboard import write_dashboard


def print_json(value) -> None:
    print(json.dumps(clean_for_json(value), ensure_ascii=False, indent=2, sort_keys=False))


def add_period_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--quarter", help="例如 2026-Q2")
    parser.add_argument("--months", help="逗号分隔月份，例如 2026-04,2026-05")


def resolve_months(project: Project, args: argparse.Namespace):
    months = args.months.split(",") if getattr(args, "months", None) else None
    period = project.resolve_period(months=months, quarter=getattr(args, "quarter", None))
    return period["months"], period


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="供货质量指数谈判核算工具")
    parser.add_argument("--data", default="data", help="交换资料目录，默认 ./data")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sampling", help="抽样政策与等级可见性变化")
    add_period_args(p)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("current", help="当前经营读数")
    add_period_args(p)
    p.add_argument("--edition", default="candidate")
    p.add_argument("--market")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("basket", help="固定篮子读数")
    p.add_argument("edition")
    add_period_args(p)
    p.add_argument("--market")
    p.add_argument("--line")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("compare", help="同一篮子两个期间的表现变化")
    p.add_argument("edition")
    p.add_argument("--from-months", required=True, help="逗号分隔")
    p.add_argument("--to-months", required=True, help="逗号分隔")
    p.add_argument("--market")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("composition", help="实际构成相对固定篮子的变化")
    p.add_argument("edition", nargs="?", default="candidate")
    add_period_args(p)
    p.add_argument("--market")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("operating", help="两期实际经营指数的精确差额桥")
    p.add_argument("--old-months", required=True)
    p.add_argument("--new-months", required=True)
    p.add_argument("--old-edition", default="published")
    p.add_argument("--new-edition", default="candidate")
    p.add_argument("--by-line", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("overlap", help="两版参照的保守重合/不重合权重")
    p.add_argument("--old-edition", default="published")
    p.add_argument("--new-edition", default="candidate")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("bridge", help="跨季参照/规格/双评桥")
    p.add_argument("--edition", default="published")
    add_period_args(p)
    p.add_argument("--baseline-months", help="逗号分隔；默认去年同月")
    p.add_argument("--market")
    p.add_argument("--candidate-edition")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("targets", help="目标单与地区分解")
    add_period_args(p)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("trace", help="从目标 ID 或地区+季度回溯解释")
    p.add_argument("target_id", nargs="?")
    p.add_argument("--market", help="不提供 target_id 时按地区回溯全部版本目标")
    add_period_args(p)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("report", help="生成完整 Markdown/JSON 报告")
    add_period_args(p)
    p.add_argument("--out", help="Markdown 输出路径；不填则打印")
    p.add_argument("--json-out", help="同时写出机器可读 JSON")
    p.add_argument("--edition", help="候选篮子版本，默认 candidate")

    p = sub.add_parser("dashboard", help="生成可切换视图的离线 HTML 仪表盘")
    add_period_args(p)
    p.add_argument("--out", default="dashboard.html")
    p.add_argument("--edition", help="候选篮子版本，默认 candidate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = Project.load(args.data)

    if args.command == "sampling":
        months, period = resolve_months(project, args)
        result = {"period": period, "sampling": project.sampling_register(months)}
        if args.json:
            print_json(result)
        else:
            print(result["sampling"]["warning"])
            for e in result["sampling"]["entries"]:
                if e["policy_changed"]:
                    print(e["key"], e["transitions"])
        return 0

    if args.command == "current":
        months, period = resolve_months(project, args)
        result = {
            "period": period,
            "reading": project.actual_reading(args.edition, months, market=args.market),
            "market_shares": project.market_shares(months),
        }
        if args.json:
            print_json(result)
        else:
            r = result["reading"]
            print(f"{period['quarter']} / {months}")
            print(f"实际件数加权指数({args.edition}): {r['index']:.4f}" if r["index"] is not None else "指数不可算")
            print(f"覆盖率: {r['coverage']:.2%}; unknown率: {r['unknown_rate']:.2%}")
        return 0

    if args.command == "basket":
        months, period = resolve_months(project, args)
        result = {"period": period, "basket": project.basket_view(args.edition, months, market=args.market, line=args.line)}
        if args.json:
            print_json(result)
        else:
            b = result["basket"]
            print(f"edition={args.edition} complete={b['complete']} index={b['index']}")
            print(f"observed={b['observed_weight']:.4f}/{b['scope_weight']:.4f}; missing={b['missing_weight']:.4f}")
            if b["partial_normalized_index_not_official"] is not None:
                print(f"partial-normalized (NOT official)={b['partial_normalized_index_not_official']:.4f}")
        return 0

    if args.command == "compare":
        result = project.same_basket_change(
            args.edition,
            args.from_months.split(","),
            args.to_months.split(","),
            market=args.market,
        )
        if args.json:
            print_json(result)
        else:
            print(
                f"common weight={result['common_weight']:.2f}, "
                f"delta={result['performance_delta']:.4f}, full={result['is_full_basket']}"
            )
        return 0

    if args.command == "composition":
        months, period = resolve_months(project, args)
        result = {"period": period, "composition": project.composition_view(args.edition, months, market=args.market)}
        if args.json:
            print_json(result)
        else:
            c = result["composition"]
            print(f"actual={c['actual_index']:.4f}, basket={c['basket_index']}, composition_gap={c['composition_gap_actual_ref_minus_basket']}")
        return 0

    if args.command == "operating":
        result = project.operating_bridge(
            args.old_months.split(","),
            args.new_months.split(","),
            old_edition=args.old_edition,
            new_edition=args.new_edition,
            group_fields=("line",) if args.by_line else ("line", "market"),
        )
        if args.json:
            print_json(result)
        else:
            for k in (
                "old_index",
                "new_index",
                "total_delta",
                "continuing_raw_score_effect",
                "continuing_mix_effect",
                "new_scope_effect",
                "retired_scope_effect",
                "reconciliation_residual",
                "exact",
            ):
                print(f"{k}: {result[k]}")
        return 0

    if args.command == "overlap":
        result = project.reference_overlap(args.old_edition, args.new_edition)
        if args.json:
            print_json(result)
        else:
            for k in (
                "mapping_supported_common_weight",
                "double_review_supported_common_weight",
                "old_reference_nonoverlap_weight",
                "new_reference_nonoverlap_weight",
            ):
                print(f"{k}: {result[k]:.4f}")
        return 0

    if args.command == "bridge":
        months, period = resolve_months(project, args)
        baseline = args.baseline_months.split(",") if args.baseline_months else None
        if baseline is None:
            from .core import previous_year_months
            baseline = previous_year_months(months)
        result = {
            "period": period,
            "bridge": project.reference_bridge(
                args.edition,
                months,
                baseline,
                market=args.market,
                candidate_edition=args.candidate_edition,
            ),
        }
        if args.json:
            print_json(result)
        else:
            b = result["bridge"]
            for k in (
                "baseline_index",
                "certified_weight",
                "caliber_blocked_weight",
                "observation_blocked_weight",
                "mapping_residual_weight",
                "proxy_if_unresolved_unchanged",
                "absolute_lower_index",
                "absolute_upper_index",
            ):
                print(f"{k}: {b[k]}")
        return 0

    if args.command == "targets":
        months, period = resolve_months(project, args)
        q = args.quarter or period["quarter"]
        result = {
            "period": period,
            "targets": [project.evaluate_target(r["id"], quarter=q) for r in project.targets],
            "allocation_T03": project.target_allocation("T03", quarter=q),
            "evidence": project.evidence_register(),
            "separation": project.commitment_separation(q),
        }
        if args.json:
            print_json(result)
        else:
            for t in result["targets"]:
                print(t["id"], t["status"], "index=", t.get("index"), "proxy=", t.get("proxy_index"), "gap=", t.get("gap"), t.get("proxy_gap"))
        return 0

    if args.command == "trace":
        if not args.target_id and not args.market:
            parser.error("trace 需要 TARGET_ID 或 --market")
        period = project.resolve_period(months=args.months.split(",") if args.months else None, quarter=args.quarter)
        q = args.quarter or period["quarter"]
        if args.target_id:
            months_arg = period["months"] if not args.months else args.months.split(",")
            result = project.evaluate_target(args.target_id, quarter=q, months=months_arg)
            if args.json:
                print_json(result)
            else:
                print(render_trace(project, args.target_id, q))
        else:
            result = project.trace_region(args.market, q)
            if args.json:
                print_json(result)
            else:
                for tid in result["target_ids"]:
                    print(render_trace(project, tid, q))
        return 0

    if args.command == "report":
        period = project.resolve_period(months=args.months.split(",") if args.months else None, quarter=args.quarter)
        q = args.quarter or period["quarter"]
        markdown = render_report(project, q, edition=args.edition)
        if args.out:
            Path(args.out).write_text(markdown, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(markdown)
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(project.full_report(q, candidate_edition=args.edition), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"wrote {args.json_out}", file=sys.stderr)
        return 0

    if args.command == "dashboard":
        period = project.resolve_period(months=args.months.split(",") if args.months else None, quarter=args.quarter)
        q = args.quarter or period["quarter"]
        path = write_dashboard(project, args.out, q)
        print(f"wrote {path.resolve()}")
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
