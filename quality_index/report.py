"""把核算结果渲染成谈判人员可读的 Markdown。"""

from __future__ import annotations

from typing import Any

from .core import Project, clean_for_json


def fnum(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def fpct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.{digits}f}%"


def status_zh(status: str | None) -> str:
    mapping = {
        "met": "达到",
        "missed": "未达到",
        "provisional_period_not_quarter_complete": "已有观测/季度未齐，暂不能作季度结论",
        "future_or_inactive_target_baseline": "目标尚未生效：以下为新基线试算",
        "committed_definition_not_certifiable": "原承诺口径不能完整验证",
        "no_observations": "无观测",
        "certified_full": "全份额认证衔接",
        "certified_firm_share_only": "仅最低映射份额可衔接",
        "line_wide_continuous": "整线连续",
        "partial_continuous_evidence_limited_scope": "仅限定范围有连续证据",
        "new_baseline_only": "仅新基线",
        "caliber_blocked": "口径/双评不足，阻断",
        "observation_blocked": "缺少当期观测，阻断",
        "mapping_unresolved": "映射未证实，保留差额",
        "continuous_full_chain": "全链连续",
        "continuous_supported_share_only": "仅可证实份额连续",
        "mapping_known_but_double_review_insufficient": "规格有关但双评不足",
        "new_baseline_only": "仅可从新基线起算",
    }
    return mapping.get(status or "", status or "—")


def render_trace(project: Project, target_id: str, quarter: str | None = None) -> str:
    trace = clean_for_json(project.evaluate_target(target_id, quarter=quarter))
    t = trace["target"]
    lines = [
        f"# 目标回溯 {target_id} · {trace['quarter']}",
        "",
        f"- 层级/地区：{t['level']} / {t['market'] or '全部'}",
        f"- 参照版本：`{t['edition']}`；目标 **{fnum(trace['target_value'])}**；"
        f"固定合同：{'是' if trace['fixed'] else '否'}",
        f"- 生效季度：{t['effective_from']} 至 {t['effective_until']}（含端点）；"
        f"{trace['quarter']} 是否生效：{'是' if trace['active_in_quarter'] else '否'}",
        f"- 状态：**{status_zh(trace['status'])}**",
    ]
    if trace.get("index") is not None:
        lines.append(f"- 采用指数：**{fnum(trace['index'])}**；与目标差额 {fnum(trace['gap'])}")
    if trace.get("proxy_index") is not None:
        lines.append(
            "- 证据桥接代理值（仅已认证分量替换，未衔接部分假设维持旧表现；unknown 按约定排除在等级分母外）："
            f"**{fnum(trace['proxy_index'])}**；距目标 {fnum(trace.get('proxy_gap'))}。"
            "这不是达标结论。"
        )
    if trace.get("proxy_conservative_zero_earned") is not None:
        lines.append(
            "- 可见性审慎代理值（双评显示会落入旧口径无法定级的部分按零收益计，而非任意等级）："
            f"**{fnum(trace['proxy_conservative_zero_earned'])}**；"
            f"距目标 {fnum(trace.get('proxy_conservative_gap'))}。"
        )
    lines += ["", "## 采用的指数解释", ""]
    lines += [f"- {x}" for x in trace["adopted_index_explanation"]]

    bv = trace.get("basket_view")
    if bv:
        lines += [
            "",
            "## 篮子核算",
            "",
            f"- 权重合计：{fnum(bv['scope_weight'], 4)}；已观测权重：{fnum(bv['observed_weight'], 4)}；"
            f"缺失权重：{fnum(bv['missing_weight'], 4)}；完整：{'是' if bv['complete'] else '否'}",
            f"- 官方指数：{fnum(bv['index'])}；不完整时的临时归一化值（不得用于承诺）："
            f"{fnum(bv['partial_normalized_index_not_official'])}",
            f"-  pooled 覆盖率：{fpct(bv['coverage'])}；unknown 率：{fpct(bv['unknown_rate'])}",
            f"- 抽样模式：{', '.join(bv['sampling_modes']) or '—'}；"
            f"含非概率抽样：{'是' if bv['non_probability_sample'] else '否'}",
            "",
            "| 线 | 市场 | 规格 | 权重 | 得分 | unknown零收益界 | 覆盖率 | unknown率 | 抽样 | 状态 |",
            "|---|---|---|---:|---:|---:|---:|---:|---|---|",
        ]
        for i in bv["items"]:
            reasons = "; ".join(f"{k}:{v}" for k, v in (i.get("unknown_reasons") or {}).items())
            sampling = ",".join(i.get("sampling_modes", []))
            if reasons:
                sampling += f"（原因: {reasons}）"
            lines.append(
                f"| {i['line']} | {i['market']} | {i['sku']} | {fnum(i['weight'], 2)} | "
                f"{fnum(i.get('score'))} | {fnum(i.get('zero_earned_visibility_score'))} | "
                f"{fpct(i.get('coverage'))} | "
                f"{fpct(i.get('unknown_rate'))} | {sampling} | "
                f"{status_zh(i['status'])} |"
            )

    bridge = trace.get("bridge")
    if bridge:
        lines += [
            "",
            "## 不能衔接的参照量",
            "",
            f"- 可认证权重：**{fpct(bridge['certified_weight'])}**",
            f"- 双评/口径阻断：**{fpct(bridge['caliber_blocked_weight'])}**",
            f"- 缺当期观测阻断：**{fpct(bridge['observation_blocked_weight'])}**",
            f"- 规格映射未证实：**{fpct(bridge['mapping_residual_weight'])}**",
            f"- 权重台账合计：{fpct(bridge['weight_accounting_total'])}；"
            f"精确闭合：{'是' if bridge['weight_accounting_exact'] else '否'}",
            f"- 最宽可能指数区间（未衔接量按 0–100 包络）："
            f"[{fnum(bridge['absolute_lower_index'])}, {fnum(bridge['absolute_upper_index'])}]",
        ]

    lines += ["", "## 不能纳入本地区目标的市场份额", "",
              "| 市场 | 可定级件数份额 | 抽检件数 | 覆盖率 |", "|---|---:|---:|---:|"]
    for s in trace.get("excluded_market_shares", []):
        lines.append(
            f"| {s['market']} | {fpct(s['known_share'])} | {s['inspected_pieces']} | "
            f"{fpct(s['coverage'])} |"
        )
    if not trace.get("excluded_market_shares"):
        lines.append("| （无地区外份额；all 目标覆盖全部市场） | | | |")

    lines += ["", "## 仍约束下一轮的原承诺", "",
              "| ID | 市场 | 版本 | 目标 | 固定 | 有效期 | 本季生效 |",
              "|---|---|---|---:|---|---|---|"]
    for c in trace["original_commitments"]:
        lines.append(
            f"| {c['id']} | {c['market'] or '全部'} | {c['edition']} | {fnum(c['target'])} | "
            f"{'是' if c['fixed'] else '否'} | {c['effective_from']}–{c['effective_until']} | "
            f"{('是' if c['active_in_quarter'] else '否') if c['active_in_quarter'] is not None else '—'} |"
        )
    if trace.get("effective_overlaps"):
        lines += ["", "### 生效重叠警告"]
        for o in trace["effective_overlaps"]:
            lines.append(f"- {o['quarter']}：{', '.join(o['target_ids'])}（{', '.join(o['editions'])}）。{o['instruction']}")
    return "\n".join(lines) + "\n"


def render_report(project: Project, quarter: str | None = None, edition: str | None = None) -> str:
    report = project.full_report(quarter, candidate_edition=edition)
    q = report["quarter"]
    period = report["period"]
    months = period["months"]
    edition = report.get("candidate_edition", "candidate")
    latest_two = project.available_months[-2:]
    change = project.same_basket_change(edition, [latest_two[0]], [latest_two[1]]) if len(latest_two) >= 2 else None
    comp = project.composition_view(edition, months) if months else None

    lines = [
        "# 季度供货质量指数谈判核算",
        "",
        f"- 核算季度：**{q}**；使用月份：{', '.join(months) or '无'}",
        f"- 季度日历完整：{'是' if period['quarter_calendar_complete'] else '否（缺 ' + ', '.join(period['absent_months']) + '；不得把缺月当零）'}",
        f"- 同比基线月份：{', '.join(report['baseline_months'])}",
        "- unknown 不赋等级分；缺篮子单元不重新归一化；定向抽检只作观测，不作概率纠偏。",
        "",
        "## 1. 当前经营读数与固定篮子读数",
        "",
    ]
    cur = report["current_reading_candidate"]
    cb = report["candidate_basket_current"]
    lines += [
        f"- 实际件数加权经营指数（candidate 分值卡）：**{fnum(cur['index'])}**",
        f"- candidate 固定篮子指数：**{fnum(cb['index'])}**；覆盖率 {fpct(cb['coverage'])}；unknown 率 {fpct(cb['unknown_rate'])}",
        f"- 两者差额（实际构成相对篮子）：{fnum(cur['index'] - cb['index'] if cur['index'] is not None and cb['index'] is not None else None)}",
        f"- 篮子含非例行/定向抽样：{'是' if cb['non_probability_sample'] else '否'}；抽样政策变化：{', '.join(cb['sampling_policy_changes']) or '无'}",
        f"- 抽样可见性台账：{report['sampling']['warning']}（跨月政策变化 {report['sampling']['policy_change_count']} 组）",
        "",
        "| 市场 | 可定级件数份额 | 覆盖率 |",
        "|---|---:|---:|",
    ]
    for s in report["market_shares"]:
        lines.append(f"| {s['market']} | {fpct(s['known_share'])} | {fpct(s['coverage'])} |")

    yoy = report.get("yoy_operating_delta_by_line_market")
    if yoy:
        structure_net = (
            yoy["continuing_mix_effect"]
            + yoy["new_scope_effect"]
            + yoy["retired_scope_effect"]
        )
        lines += [
            "",
            "## 2. 同比总差额先按经营口径闭合",
            "",
            f"实际经营指数 {fnum(yoy['old_index'])} → {fnum(yoy['new_index'])}，"
            f"总差额 **{fnum(yoy['total_delta'])}**；分解残差 {fnum(yoy['reconciliation_residual'], 6)}。",
            f"- 持续线/市场组的 raw 得分效应：**{fnum(yoy['continuing_raw_score_effect'])}**（两期分值卡/边界不同，不能直接称为质量变化）",
            f"- 持续组内构成效应：{fnum(yoy['continuing_mix_effect'])}；新市场 scope：{fnum(yoy['new_scope_effect'])}；"
            f"退出 scope：{fnum(yoy['retired_scope_effect'])}",
            f"- 构成+新市场的净结构解释：**{fnum(structure_net)}**。因此不能把抬头表述为同类产品质量普遍提高；"
            "新市场按要求保留在当前指数中，但在同篮/原承诺比较里单独列示。",
            "",
            "| 线/市场 | 旧权重 | 新权重 | 旧分 | 新分 | raw得分效应 | 构成/scope效应 | 对总差额贡献 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in yoy["rows"]:
            structure = r.get("mix_effect", r.get("scope_effect", 0.0))
            score_effect = r.get("score_effect", 0.0)
            lines.append(
                f"| {r['key'][0]}/{r['key'][1]} | {fpct(r['old_weight'])} | {fpct(r['new_weight'])} | "
                f"{fnum(r['old_score'])} | {fnum(r['new_score'])} | {fnum(score_effect)} | "
                f"{fnum(structure)} | {fnum(r['contribution_delta'])} |"
            )

    if change:
        lines += [
            "",
            "## 3. 同篮子表现变化（不把缺单元后的临时归一化当改善）",
            "",
            f"比较 {latest_two[0]} → {latest_two[1]}；共同篮子权重 {fnum(change['common_weight'], 2)} / "
            f"{fnum(change['full_basket_weight'], 2)}；完整篮子：{'是' if change['is_full_basket'] else '否'}。",
            f"- 共同规格同篮子指数：{fnum(change['same_basket_index_before'])} → "
            f"{fnum(change['same_basket_index_after'])}，变化 **{fnum(change['performance_delta'])}**",
            f"- 后一月缺权重：{fnum(change['missing_weight_after'], 2)}；"
            f"若擅自归一化会得到 {fnum(change['naive_partial_after_index'])}，该数不是官方篮子指数。",
            "",
            "| 规格 | 共同内相对权重 | 前值 | 后值 | 变化 | 加权变化 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for c in change["changes"]:
            k = c["key"]
            lines.append(
                f"| {k[0]}/{k[1]}/{k[2]} | {fpct(c['common_relative_weight'])} | "
                f"{fnum(c['score_before'])} | {fnum(c['score_after'])} | "
                f"{fnum(c['score_delta'])} | {fnum(c['weighted_delta'])} |"
            )

    lines += [
        "",
        "## 4. 规格/市场构成变化",
        "",
    ]
    if comp:
        lines += [
            f"- 参照内实际构成指数 − 固定篮子指数：**{fnum(comp['composition_gap_actual_ref_minus_basket'])}**",
            f"- 参照外件数份额：{fpct(comp['outside_reference_share'])}",
            "",
            "| 线/市场/规格 | 篮权重 | 实际全局权重 | 参照内实际权重 | 权重差 | 对构成差额贡献 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for r in comp["rows"]:
            key = r["key"]
            lines.append(
                f"| {key[0]}/{key[1]}/{key[2]} | {fnum(r['basket_weight'])} | "
                f"{fpct(r['actual_global_weight'])} | {fpct(r['actual_reference_weight'])} | "
                f"{fpct(r['mix_weight_delta'])} | {fnum(r['mix_effect_contribution'])} |"
            )

    bridge = report["yoy_published_bridge"]
    overlap = report.get("reference_overlap")
    lines += [
        "",
        "## 5. 跨季参照桥与不能分解的差额",
        "",
        f"published 旧篮子基线指数：**{fnum(bridge['baseline_index'])}**。",
    ]
    if overlap:
        lines += [
            "",
            "两版篮子按最低可证实份额建立保守重合台账：",
            f"- 规格映射可支持的共同权重：**{fpct(overlap['mapping_supported_common_weight'])}**；"
            f"其中双评也支持的共同权重：**{fpct(overlap['double_review_supported_common_weight'])}**。",
            f"- 旧篮侧参照不重合/超出共同量：**{fpct(overlap['old_reference_nonoverlap_weight'])}**；"
            f"新篮侧参照不重合/超出共同量：**{fpct(overlap['new_reference_nonoverlap_weight'])}**（含全新市场 {fpct(overlap['new_unmapped_weight'])}）。",
            f"- 两侧不重合量分别基于各自篮子权重，不是同一个可相减的量；合同未指定共同权重时，跨版本总差额不存在唯一的质量/构成分解。",
        ]
    lines += [
        "",
        "| 分量 | 占 published 总篮权重 | 含义 |",
        "|---|---:|---|",
        f"| 可认证衔接 | {fpct(bridge['certified_weight'])} | 最低映射份额、当期观测和足够双评均具备 |",
        f"| 口径/双评阻断 | {fpct(bridge['caliber_blocked_weight'])} | 映射可指认但双评不足，不能带入新边界 |",
        f"| 缺观测阻断 | {fpct(bridge['observation_blocked_weight'])} | 映射存在但期间无计数 |",
        f"| 映射未证实 residual | {fpct(bridge['mapping_residual_weight'])} | 不把同一批老件重复借给两个新规格 |",
        "",
        f"权重台账合计 {fpct(bridge['weight_accounting_total'])}，精确闭合：{'是' if bridge['weight_accounting_exact'] else '否'}。",
        f"若假设未衔接部分维持旧表现，代理指数为 **{fnum(bridge['proxy_if_unresolved_unchanged'])}**；"
        f"把双评揭示的旧口径无法定级量按零收益处理时为 **{fnum(bridge['proxy_conservative_zero_earned'])}**。"
        "两者都不是可承诺的完成度。",
        "",
        "### 逐映射台账",
        "",
        "| Mapping | 老规格→新规格 | 市场 | 最低份额 | 台账权重 | 双评 | 状态 | 旧口径当期分 |",
        "|---|---|---|---:|---:|---:|---|---:|",
    ]
    for e in bridge["entries"]:
        if e.get("mapping_id"):
            lines.append(
                f"| {e['mapping_id']} | {e['previous']}→{e['current']} | {e.get('mapping_market')} | "
                f"{fpct(e.get('firm_share'))} | {fpct(e['normalized_reference_weight'])} | "
                f"{e.get('pair_count')}/{e.get('min_pairs')} | {status_zh(e['status'])} | "
                f"{fnum(e.get('old_basis_current_score'))} |"
            )
        else:
            lines.append(
                f"| — | {e.get('previous', e.get('basket_key', ['', '', ''])[2])} residual | "
                f"{e.get('basket_key', ['', '', ''])[1]} | — | "
                f"{fpct(e['normalized_reference_weight'])} | — | {status_zh(e['status'])} | — |"
            )

    lines += [
        "",
        "## 6. 目标单：原承诺、新基线与地区分解",
        "",
        "| ID | 地区 | 版本 | 目标 | 固定 | 本季状态 | 指数/代理 | 差额 |",
        "|---|---|---|---:|---|---|---:|---:|",
    ]
    for t in report["targets"]:
        shown_index = t.get("index")
        gap = t.get("gap")
        if shown_index is None and t.get("proxy_index") is not None:
            shown_index = f"代理 {fnum(t['proxy_index'])}"
            gap = f"代理 {fnum(t.get('proxy_gap'))}"
        else:
            shown_index = fnum(shown_index)
            gap = fnum(gap)
        lines.append(
            f"| {t['id']} | {t['market'] or 'all'} | {t['edition']} | {fnum(t['target_value'])} | "
            f"{'是' if t['fixed'] else '否'} | {status_zh(t['status'])} | {shown_index} | {gap} |"
        )

    alloc = report["allocation"]
    if alloc:
        h = alloc["hard_contract_arithmetic"]
        env = alloc["floor_cap_envelope"]
        lines += [
            "",
            "### T03 地区分解可行性",
            "",
            f"- candidate 地区篮权重：" + "，".join(f"{k}={fpct(v)}" for k, v in alloc["region_weights"].items()),
            f"- 新结构基线总指数：**{fnum(alloc['baseline_index'])}**；距 T03={fnum(alloc['total_target'])} 的实际基线差额：**{fnum(alloc['baseline_gap_to_total'])}**。",
            f"- 固定合同值必须精确成立时，加权目标只能落在 [{fnum(h['achievable_min'])}, {fnum(h['achievable_max'])}]；"
            f"按 T01=68、T02=77、T04=79 的已拟地区值加权仅 {fnum(alloc['proposed_weighted_target'])}，距 T03 差 "
            f"**{fnum(alloc['proposed_gap_above_total'])}**；硬包络上限距总目标缺口 **{fnum(h['gap_above_max'])}**。"
            "把 published 固定值与 candidate 总目标放入同一加权式仅为暴露冲突；口径不同，不能变成一张合法合并承诺。",
            f"- 即使允许各地区在 floor/cap 内重谈，权重包络上限为 **{fnum(env['weighted_max'])}**，"
            f"距 T03 仍差 **{fnum(env['gap_above_max'])}**；这是固定地区权重+边界造成的结构不可行，不能靠重分篮子解决。",
            "",
            "| 市场 | candidate权重 | 基线指数 | floor | cap |",
            "|---|---:|---:|---:|---:|",
        ]
        for d in env["details"]:
            lines.append(
                f"| {d['market']} | {fpct(d['weight'])} | "
                f"{fnum(alloc['regional_baseline_indices'].get(d['market']))} | "
                f"{fnum(d['floor'])} | {fnum(d['cap'])} |"
            )

    ev = report["evidence"]
    lines += ["", "## 7. 哪些产品线可沿用、哪些只能从新基线起算", ""]
    for l in ev["lines"]:
        lines.append(
            f"- **{l['line']}**：{status_zh(l['status'])}；"
            f"全链={', '.join(l['full_chain_scopes']) or '无'}；"
            f"限定份额={', '.join(l['partial_scopes']) or '无'}；"
            f"双评阻断={', '.join(l['blocked_scopes']) or '无'}；"
            f"新基线={', '.join(l['new_baseline_scopes']) or '无'}。"
        )

    lines += ["", "## 8. 哪些上下游/跨口径指标尚不可合在同一张承诺里", ""]
    for x in report["commitment_separation"]:
        lines.append(
            f"- **[{x['kind']}]** 上游指标“{x['upstream_indicator']}” → "
            f"下游指标“{x['downstream_indicator']}”：{x['reason']}"
        )

    if report.get("effective_overlaps"):
        lines += ["", "## 9. 生效季度重叠"]
        for o in report["effective_overlaps"]:
            lines.append(f"- {o['quarter']}：{', '.join(o['target_ids'])}。{o['instruction']}")

    lines += [
        "",
        "## 10. 回溯方式",
        "",
        "机器可读：`python3 analyze.py trace T01 --quarter 2026-Q2 --json`；"
        "后续月份加入 `data/counts.csv` 或新增 candidate2 edition 后，用同一命令重算。",
        "",
    ]
    return "\n".join(lines)
