"""供货质量指数的可追溯核算与目标桥接。

本模块只做资料中可支持的计算：
- unknown 不赋等级分；
- 篮子权重不因为缺月而悄悄重新归一化；
- 规格映射按最低可指认份额建立非重叠台账；
- 双评记录不足时不做等级口径衔接；
- 已公布目标与候选目标分别保留，不合并成同一张承诺。
"""

from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

# 兼容直接把本仓库加入 sys.path 的调用方式。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from check_sources import load_sources  # noqa: E402

GRADES = ("high", "middle", "low")
COUNT_NUMERIC = (
    "eligible",
    "inspected",
    "high",
    "middle",
    "low",
    "unknown",
)
EPS = 1e-9


def qround(value: Any, digits: int = 4) -> Any:
    """输出用四舍五入；None 保持 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isclose(value, 0.0, abs_tol=1e-12):
            value = 0.0
        return round(float(value), digits)
    return value


def clean_for_json(value: Any) -> Any:
    """把 set/Counter 等转换成 JSON 可序列化结构。"""
    if isinstance(value, dict):
        return {str(k): clean_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_for_json(v) for v in value]
    if isinstance(value, set):
        return sorted(clean_for_json(v) for v in value)
    if isinstance(value, Counter):
        return dict(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def quarter_months(quarter: str) -> list[str]:
    try:
        year_text, q_text = quarter.split("-Q")
        year = int(year_text)
        q = int(q_text)
        start = (q - 1) * 3 + 1
        return [f"{year:04d}-{month:02d}" for month in range(start, start + 3)]
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"季度格式应为 YYYY-Qn，例如 2026-Q2；收到 {quarter!r}") from exc


def quarter_of_month(month: str) -> str:
    year, m = month.split("-")[:2]
    q = (int(m) - 1) // 3 + 1
    return f"{year}-Q{q}"


def quarter_key(quarter: str) -> tuple[int, int]:
    year_text, q_text = quarter.split("-Q")
    return int(year_text), int(q_text)


def quarter_active(quarter: str | None, effective_from: str, effective_until: str) -> bool:
    if quarter is None:
        return True
    return quarter_key(effective_from) <= quarter_key(quarter) <= quarter_key(effective_until)


def previous_year_months(months: Iterable[str]) -> list[str]:
    result = []
    for month in months:
        year, m = month.split("-")[:2]
        result.append(f"{int(year) - 1:04d}-{m}")
    return result


class Project:
    """一个交换资料目录及其全部核算结果。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.tables = load_sources(self.directory)
        self.counts = self.tables["counts"]
        self.mappings = self.tables["mapping"]
        self.pairs = self.tables["paired"]
        self.baskets = self.tables["baskets"]
        self.targets = self.tables["targets"]
        self._agg_cache: dict[tuple[str, ...], dict[tuple, dict]] = {}
        self._validate_business_records()

    @classmethod
    def load(cls, directory: str | Path | None = None) -> "Project":
        return cls(Path(directory) if directory else _ROOT / "data")

    # ------------------------------------------------------------------
    # 基础读取与期间
    # ------------------------------------------------------------------
    def _validate_business_records(self) -> None:
        for i, row in enumerate(self.counts, 2):
            vals = {k: int(row[k]) for k in COUNT_NUMERIC}
            grade_sum = sum(vals[g] for g in ("high", "middle", "low", "unknown"))
            if grade_sum != vals["inspected"]:
                raise ValueError(f"counts 第 {i} 行等级计数之和不等于 inspected")
            if vals["inspected"] > vals["eligible"]:
                raise ValueError(f"counts 第 {i} 行 inspected 大于 eligible")
            if vals["inspected"] < 0 or vals["eligible"] < 0:
                raise ValueError(f"counts 第 {i} 行计数不能为负")

        pair_keys = {(r["line"], r["market"], r["sku"]) for r in self.pairs}
        for key in pair_keys:
            eligible = {int(r["eligible"]) for r in self.pairs if (r["line"], r["market"], r["sku"]) == key}
            if len(eligible) > 1:
                raise ValueError(f"paired {key} 的同一评分组有多个 eligible；该数跨格共享，不能重复解释")

    @property
    def editions(self) -> list[str]:
        return sorted({r["edition"] for r in self.baskets})

    @property
    def available_months(self) -> list[str]:
        return sorted({r["month"] for r in self.counts})

    def resolve_period(
        self,
        *,
        months: Iterable[str] | None = None,
        quarter: str | None = None,
    ) -> dict[str, Any]:
        available = set(self.available_months)
        if months is not None:
            requested = list(months)
        elif quarter is not None:
            requested = quarter_months(quarter)
        else:
            latest = max(self.available_months)
            quarter = quarter_of_month(latest)
            requested = quarter_months(quarter)

        present = [m for m in requested if m in available]
        absent = [m for m in requested if m not in available]
        q = quarter or quarter_of_month(present[-1] if present else requested[-1])
        expected = quarter_months(q)
        return {
            "quarter": q,
            "requested_months": requested,
            "months": present,
            "absent_months": absent,
            "quarter_calendar_complete": set(expected).issubset(available),
            "has_observations": bool(present),
        }

    def aggregate(self, months: Iterable[str] | None = None) -> dict[tuple, dict[str, Any]]:
        month_list = tuple(sorted(months)) if months is not None else tuple(self.available_months)
        if month_list in self._agg_cache:
            return self._agg_cache[month_list]

        month_set = set(month_list)
        agg: dict[tuple, dict[str, Any]] = {}
        for row in self.counts:
            if month_set and row["month"] not in month_set:
                continue
            key = (row["line"], row["market"], row["sku"], row["scale"])
            item = agg.setdefault(
                key,
                {
                    "line": row["line"],
                    "market": row["market"],
                    "sku": row["sku"],
                    "scale": row["scale"],
                    **{k: 0 for k in COUNT_NUMERIC},
                    "months": set(),
                    "sampling_by_month": {},
                    "unknown_reasons": Counter(),
                },
            )
            for field in COUNT_NUMERIC:
                item[field] += int(row[field])
            item["months"].add(row["month"])
            item["sampling_by_month"][row["month"]] = row["sampling"]
            if row["unknown_reason"]:
                item["unknown_reasons"][row["unknown_reason"]] += int(row["unknown"])

        self._agg_cache[month_list] = agg
        return agg

    # ------------------------------------------------------------------
    # 分数和篮子
    # ------------------------------------------------------------------
    @staticmethod
    def grade_score(agg: dict[str, Any], points: tuple[float, float, float]) -> float | None:
        hp, mp, lp = points
        known = agg["high"] + agg["middle"] + agg["low"]
        if known <= 0:
            return None
        earned = agg["high"] * hp + agg["middle"] * mp + agg["low"] * lp
        return earned / known

    def basket_rows(
        self,
        edition: str,
        *,
        market: str | None = None,
        line: str | None = None,
    ) -> list[dict[str, str]]:
        rows = [r for r in self.baskets if r["edition"] == edition]
        if market is not None:
            rows = [r for r in rows if r["market"] == market]
        if line is not None:
            rows = [r for r in rows if r["line"] == line]
        return rows

    def basket_view(
        self,
        edition: str,
        months: Iterable[str] | None = None,
        *,
        market: str | None = None,
        line: str | None = None,
    ) -> dict[str, Any]:
        rows = self.basket_rows(edition, market=market, line=line)
        if not rows:
            raise ValueError(f"篮子版本 {edition!r} / market={market!r} / line={line!r} 不存在")
        agg = self.aggregate(months)
        scope_weight = sum(float(r["weight"]) for r in rows)
        items: list[dict[str, Any]] = []
        observed_weight = 0.0
        contribution_sum = 0.0
        totals = Counter()
        all_modes: set[str] = set()
        policy_changes: list[str] = []

        for b in rows:
            weight = float(b["weight"])
            key = (b["line"], b["market"], b["sku"], b["scale"])
            a = agg.get(key)
            item: dict[str, Any] = {
                "line": b["line"],
                "market": b["market"],
                "sku": b["sku"],
                "scale": b["scale"],
                "weight": weight,
                "min_pairs": int(b["min_pairs"]),
                "points": {
                    "high": float(b["high_points"]),
                    "middle": float(b["middle_points"]),
                    "low": float(b["low_points"]),
                },
                "observed": False,
            }
            if a is None:
                item["status"] = "missing_count"
                items.append(item)
                continue

            known = a["high"] + a["middle"] + a["low"]
            inspected = a["inspected"]
            points = (
                float(b["high_points"]),
                float(b["middle_points"]),
                float(b["low_points"]),
            )
            score = self.grade_score(a, points)
            earned = (
                a["high"] * points[0] + a["middle"] * points[1] + a["low"] * points[2]
            )
            zero_earned_score = earned / inspected if inspected else None
            modes = set(a["sampling_by_month"].values())
            all_modes |= modes
            changed = len(modes) > 1
            if changed:
                policy_changes.append(f"{b['line']}/{b['market']}/{b['sku']}")
            item.update(
                {
                    "observed": score is not None,
                    "status": "observed" if score is not None else "no_graded_pieces",
                    "eligible": a["eligible"],
                    "inspected": inspected,
                    "high": a["high"],
                    "middle": a["middle"],
                    "low": a["low"],
                    "unknown": a["unknown"],
                    "known": known,
                    "score": score,
                    "zero_earned_visibility_score": zero_earned_score,
                    "weighted_contribution": weight * score if score is not None else None,
                    "coverage": inspected / a["eligible"] if a["eligible"] else None,
                    "unknown_rate": a["unknown"] / inspected if inspected else None,
                    "sampling_modes": sorted(modes),
                    "sampling_policy_change": changed,
                    "unknown_reasons": dict(a["unknown_reasons"]),
                    "months": sorted(a["months"]),
                }
            )
            for f in ("eligible", "inspected", "high", "middle", "low", "unknown", "known"):
                totals[f] += item.get(f, 0)
            if score is not None:
                observed_weight += weight
                contribution_sum += weight * score
            items.append(item)

        complete = abs(observed_weight - scope_weight) <= 1e-7 and all(i["observed"] for i in items)
        partial_index = contribution_sum / observed_weight if observed_weight > EPS else None
        official_index = contribution_sum / scope_weight if complete else None
        coverage = totals["inspected"] / totals["eligible"] if totals["eligible"] else None
        unknown_rate = totals["unknown"] / totals["inspected"] if totals["inspected"] else None
        return {
            "edition": edition,
            "market": market,
            "line": line,
            "scope_weight": scope_weight,
            "observed_weight": observed_weight,
            "missing_weight": max(0.0, scope_weight - observed_weight),
            "complete": complete,
            "index": official_index,
            "partial_normalized_index_not_official": partial_index,
            "weighted_score_mass": contribution_sum,
            "coverage": coverage,
            "unknown_rate": unknown_rate,
            "totals": dict(totals),
            "sampling_modes": sorted(all_modes),
            "sampling_policy_changes": policy_changes,
            "non_probability_sample": any(m != "routine" for m in all_modes),
            "items": items,
        }

    def _point_card_resolver(self, edition: str):
        basket = self.basket_rows(edition)
        exact_card = {
            (r["line"], r["market"], r["sku"]): (
                float(r["high_points"]),
                float(r["middle_points"]),
                float(r["low_points"]),
            )
            for r in basket
        }
        scale_cards: dict[str, set[tuple[float, float, float]]] = defaultdict(set)
        for r in basket:
            scale_cards[r["scale"]].add(
                (float(r["high_points"]), float(r["middle_points"]), float(r["low_points"]))
            )

        def resolve(a: dict[str, Any]):
            card = exact_card.get((a["line"], a["market"], a["sku"]))
            source = "basket_cell"
            if card is None:
                cards = scale_cards.get(a["scale"], set())
                if len(cards) == 1:
                    card = next(iter(cards))
                    source = "scale_default"
                else:
                    source = "no_point_card"
            return card, source

        return resolve

    def actual_reading(
        self,
        edition: str,
        months: Iterable[str] | None = None,
        *,
        market: str | None = None,
    ) -> dict[str, Any]:
        """按某版本的分值卡计算实际件数加权的经营读数。"""
        agg = self.aggregate(months)
        resolve_card = self._point_card_resolver(edition)

        rows = []
        total_known = 0
        total_scorable_known = 0
        total_earned = 0.0
        total_inspected = 0
        total_eligible = 0
        total_unknown = 0
        for a in agg.values():
            if market is not None and a["market"] != market:
                continue
            known = a["high"] + a["middle"] + a["low"]
            total_known += known
            total_inspected += a["inspected"]
            total_eligible += a["eligible"]
            total_unknown += a["unknown"]
            card, source = resolve_card(a)
            score = self.grade_score(a, card) if card else None
            earned = (
                a["high"] * card[0] + a["middle"] * card[1] + a["low"] * card[2]
                if card is not None
                else None
            )
            if score is not None:
                total_scorable_known += known
                total_earned += earned or 0.0
            rows.append(
                {
                    "line": a["line"],
                    "market": a["market"],
                    "sku": a["sku"],
                    "scale": a["scale"],
                    "known": known,
                    "actual_weight": None,  # 序列化前在下面补
                    "score": score,
                    "points_source": source if score is not None else "no_point_card",
                    "coverage": a["inspected"] / a["eligible"] if a["eligible"] else None,
                    "unknown_rate": a["unknown"] / a["inspected"] if a["inspected"] else None,
                    "sampling_modes": sorted(set(a["sampling_by_month"].values())),
                    "months": sorted(a["months"]),
                }
            )
        for r in rows:
            r["actual_weight"] = r["known"] / total_known if total_known else None

        index = total_earned / total_scorable_known if total_scorable_known else None
        return {
            "edition_for_points": edition,
            "market": market,
            "index": index if total_scorable_known == total_known else None,
            "partial_index": index,
            "all_known_scorable": total_scorable_known == total_known,
            "known_pieces": total_known,
            "scorable_known_pieces": total_scorable_known,
            "coverage": total_inspected / total_eligible if total_eligible else None,
            "unknown_rate": total_unknown / total_inspected if total_inspected else None,
            "rows": sorted(rows, key=lambda r: (r["line"], r["market"], r["sku"])),
        }

    def market_shares(self, months: Iterable[str] | None) -> list[dict[str, Any]]:
        agg = self.aggregate(months)
        by_market: Counter = Counter()
        inspected: Counter = Counter()
        eligible: Counter = Counter()
        for a in agg.values():
            known = a["high"] + a["middle"] + a["low"]
            by_market[a["market"]] += known
            inspected[a["market"]] += a["inspected"]
            eligible[a["market"]] += a["eligible"]
        total = sum(by_market.values())
        return [
            {
                "market": market,
                "known_pieces": count,
                "known_share": count / total if total else None,
                "inspected_pieces": inspected[market],
                "eligible_pieces": eligible[market],
                "coverage": inspected[market] / eligible[market] if eligible[market] else None,
            }
            for market, count in sorted(by_market.items())
        ]

    def sampling_register(self, months: Iterable[str] | None = None) -> dict[str, Any]:
        """逐规格列抽样政策、覆盖率和 unknown 可见性变化。"""
        month_set = set(months) if months is not None else None
        grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for r in self.counts:
            if month_set is not None and r["month"] not in month_set:
                continue
            key = (r["line"], r["market"], r["sku"], r["scale"])
            eligible = int(r["eligible"])
            inspected = int(r["inspected"])
            unknown = int(r["unknown"])
            grouped[key].append(
                {
                    "month": r["month"],
                    "sampling": r["sampling"],
                    "eligible": eligible,
                    "inspected": inspected,
                    "coverage": inspected / eligible if eligible else None,
                    "unknown": unknown,
                    "unknown_rate": unknown / inspected if inspected else None,
                    "unknown_reason": r["unknown_reason"] or None,
                }
            )

        entries = []
        policy_changes = []
        for key, history in sorted(grouped.items()):
            history.sort(key=lambda x: x["month"])
            modes = [h["sampling"] for h in history]
            changed = len(set(modes)) > 1
            transitions = []
            for before, after in zip(history, history[1:]):
                if before["sampling"] != after["sampling"]:
                    transitions.append(
                        {
                            "from_month": before["month"],
                            "to_month": after["month"],
                            "from_sampling": before["sampling"],
                            "to_sampling": after["sampling"],
                            "coverage_change": (
                                after["coverage"] - before["coverage"]
                                if before["coverage"] is not None and after["coverage"] is not None
                                else None
                            ),
                            "unknown_rate_change": (
                                after["unknown_rate"] - before["unknown_rate"]
                                if before["unknown_rate"] is not None and after["unknown_rate"] is not None
                                else None
                            ),
                        }
                    )
            if changed:
                policy_changes.append({"key": key, "transitions": transitions})
            entries.append(
                {
                    "key": key,
                    "sampling_modes": sorted(set(modes)),
                    "policy_changed": changed,
                    "transitions": transitions,
                    "history": history,
                }
            )
        return {
            "entries": entries,
            "policy_change_count": len(policy_changes),
            "policy_changes": policy_changes,
            "warning": (
                "抽样政策变化会改变等级可见性；coverage/unknown 变化不能被解释成销售结构变化。"
                if policy_changes
                else "当前资料未发现同一规格跨月抽样模式变化；selected 与 routine 仍不可作为同一概率样本合并推断。"
            ),
        }

    # ------------------------------------------------------------------
    # 同篮子比较与构成
    # ------------------------------------------------------------------
    def same_basket_change(
        self,
        edition: str,
        from_months: Iterable[str],
        to_months: Iterable[str],
        *,
        market: str | None = None,
    ) -> dict[str, Any]:
        before = self.basket_view(edition, from_months, market=market)
        after = self.basket_view(edition, to_months, market=market)
        old_items = {(i["line"], i["market"], i["sku"]): i for i in before["items"]}
        new_items = {(i["line"], i["market"], i["sku"]): i for i in after["items"]}
        common = sorted(
            k
            for k, old_item in old_items.items()
            if old_item.get("observed") and new_items.get(k, {}).get("observed")
        )
        common_weight = sum(old_items[k]["weight"] for k in common)
        changes = []
        delta = 0.0
        for k in common:
            old = old_items[k]
            new = new_items[k]
            relative_weight = old["weight"] / common_weight if common_weight else None
            cell_delta = new["score"] - old["score"]
            contribution = relative_weight * cell_delta if relative_weight is not None else None
            delta += contribution or 0.0
            changes.append(
                {
                    "key": k,
                    "basket_weight": old["weight"],
                    "common_relative_weight": relative_weight,
                    "score_before": old["score"],
                    "score_after": new["score"],
                    "score_delta": cell_delta,
                    "weighted_delta": contribution,
                }
            )
        return {
            "edition": edition,
            "market": market,
            "common_keys": common,
            "common_weight": common_weight,
            "full_basket_weight": before["scope_weight"],
            "is_full_basket": abs(common_weight - before["scope_weight"]) <= 1e-7,
            "same_basket_index_before": sum(
                old_items[k]["weight"] * old_items[k]["score"] for k in common
            )
            / common_weight
            if common_weight
            else None,
            "same_basket_index_after": sum(
                new_items[k]["weight"] * new_items[k]["score"] for k in common
            )
            / common_weight
            if common_weight
            else None,
            "performance_delta": delta if common_weight else None,
            "before_view_completeness": before["complete"],
            "after_view_completeness": after["complete"],
            "official_index_before": before["index"],
            "official_index_after": after["index"],
            "naive_partial_after_index": after["partial_normalized_index_not_official"],
            "missing_weight_after": after["missing_weight"],
            "changes": changes,
        }

    def composition_view(
        self,
        edition: str,
        months: Iterable[str],
        *,
        market: str | None = None,
    ) -> dict[str, Any]:
        actual = self.actual_reading(edition, months, market=market)
        basket = self.basket_view(edition, months, market=market)
        actual_by_key = {
            (r["line"], r["market"], r["sku"]): r for r in actual["rows"]
        }
        total_known = actual["known_pieces"]
        rows = []
        covered_known = 0
        mix_gap = 0.0
        for item in basket["items"]:
            key = (item["line"], item["market"], item["sku"])
            ar = actual_by_key.get(key)
            known = ar["known"] if ar else 0
            covered_known += known
            rows.append(
                {
                    "key": key,
                    "basket_weight": item["weight"],
                    "actual_global_weight": known / total_known if total_known else None,
                    "score": item.get("score"),
                    "observed": item["observed"],
                }
            )
        for row in rows:
            if row["observed"] and covered_known:
                row["actual_reference_weight"] = (
                    (row["actual_global_weight"] * total_known) / covered_known
                )
                scope_w = basket["scope_weight"]
                row["basket_reference_weight"] = row["basket_weight"] / scope_w
                row["mix_weight_delta"] = (
                    row["actual_reference_weight"] - row["basket_reference_weight"]
                )
                row["mix_effect_contribution"] = row["mix_weight_delta"] * row["score"]
                mix_gap += row["mix_effect_contribution"]
            else:
                row["actual_reference_weight"] = None
                row["basket_reference_weight"] = None
                row["mix_weight_delta"] = None
                row["mix_effect_contribution"] = None

        outside_known = total_known - covered_known
        return {
            "edition": edition,
            "market": market,
            "actual_index": actual["index"],
            "basket_index": basket["index"],
            "basket_partial_index": basket["partial_normalized_index_not_official"],
            "reference_actual_index": sum(
                r["mix_effect_contribution"] + (r["basket_reference_weight"] or 0) * (r["score"] or 0)
                for r in rows
                if r["observed"]
            )
            if covered_known
            else None,
            "composition_gap_actual_ref_minus_basket": mix_gap if basket["complete"] else None,
            "outside_reference_known_pieces": outside_known,
            "outside_reference_share": outside_known / total_known if total_known else None,
            "rows": rows,
        }

    def operating_bridge(
        self,
        old_months: Iterable[str],
        new_months: Iterable[str],
        *,
        old_edition: str = "published",
        new_edition: str = "candidate",
        group_fields: tuple[str, ...] = ("line", "market"),
    ) -> dict[str, Any]:
        """精确解释两期实际经营指数差额。

        对两期都存在的分组用对称 Shapley 分解：
        score_effect=.5(w0+w1)(q1-q0)，mix_effect=.5(q0+q1)(w1-w0)，二者之和
        恰为 w1q1-w0q0。新增/退出分组单列 scope，不把它们伪装成同类质量变化。
        若两期使用不同分值卡/等级边界，score_effect 必须标为 raw_caliber。
        """

        def build(months, edition):
            agg = self.aggregate(months)
            resolve = self._point_card_resolver(edition)
            groups: dict[tuple, dict[str, Any]] = {}
            total_known = 0
            total_earned = 0.0
            for a in agg.values():
                card, _source = resolve(a)
                known = a["high"] + a["middle"] + a["low"]
                if card is None or known <= 0:
                    continue
                earned = a["high"] * card[0] + a["middle"] * card[1] + a["low"] * card[2]
                key = tuple(a[f] for f in group_fields)
                g = groups.setdefault(
                    key,
                    {"known": 0, "earned": 0.0, "skus": Counter(), "markets": Counter()},
                )
                g["known"] += known
                g["earned"] += earned
                g["skus"][a["sku"]] += known
                g["markets"][a["market"]] += known
                total_known += known
                total_earned += earned
            for g in groups.values():
                g["score"] = g["earned"] / g["known"] if g["known"] else None
                g["weight"] = g["known"] / total_known if total_known else None
            index = total_earned / total_known if total_known else None
            return groups, total_known, index

        g0, n0, i0 = build(old_months, old_edition)
        g1, n1, i1 = build(new_months, new_edition)
        keys = sorted(set(g0) | set(g1))
        rows = []
        score_effect = 0.0
        mix_effect = 0.0
        entry_scope = 0.0
        exit_scope = 0.0
        for key in keys:
            a = g0.get(key)
            b = g1.get(key)
            w0 = a["weight"] if a else 0.0
            w1 = b["weight"] if b else 0.0
            q0 = a["score"] if a else None
            q1 = b["score"] if b else None
            row: dict[str, Any] = {
                "key": key,
                "old_weight": w0,
                "new_weight": w1,
                "old_score": q0,
                "new_score": q1,
                "old_known": a["known"] if a else 0,
                "new_known": b["known"] if b else 0,
                "old_contribution": w0 * q0 if q0 is not None else 0.0,
                "new_contribution": w1 * q1 if q1 is not None else 0.0,
                "contribution_delta": (w1 * q1 if q1 is not None else 0.0)
                - (w0 * q0 if q0 is not None else 0.0),
                "old_top_skus": dict(a["skus"]) if a else {},
                "new_top_skus": dict(b["skus"]) if b else {},
            }
            if a and b and q0 is not None and q1 is not None:
                row["kind"] = "continuing_group_raw_caliber"
                row["score_effect"] = 0.5 * (w0 + w1) * (q1 - q0)
                row["mix_effect"] = 0.5 * (q0 + q1) * (w1 - w0)
                score_effect += row["score_effect"]
                mix_effect += row["mix_effect"]
            elif b and q1 is not None:
                row["kind"] = "new_scope"
                row["scope_effect"] = w1 * q1
                entry_scope += row["scope_effect"]
            elif a and q0 is not None:
                row["kind"] = "retired_scope"
                row["scope_effect"] = -w0 * q0
                exit_scope += row["scope_effect"]
            rows.append(row)

        delta = (i1 - i0) if i0 is not None and i1 is not None else None
        explained = score_effect + mix_effect + entry_scope + exit_scope
        return {
            "group_fields": group_fields,
            "old_edition": old_edition,
            "new_edition": new_edition,
            "old_index": i0,
            "new_index": i1,
            "total_delta": delta,
            "continuing_raw_score_effect": score_effect,
            "continuing_mix_effect": mix_effect,
            "new_scope_effect": entry_scope,
            "retired_scope_effect": exit_scope,
            "explained_delta": explained,
            "reconciliation_residual": (delta - explained) if delta is not None else None,
            "exact": delta is not None and abs(delta - explained) <= 1e-9,
            "caliber_warning": old_edition != new_edition,
            "warning": (
                "两期使用不同分值卡/等级边界；raw_score_effect 不能直接解释为同类质量变化，"
                "可认证部分必须再看 reference_bridge。"
                if old_edition != new_edition
                else None
            ),
            "rows": rows,
        }

    # ------------------------------------------------------------------
    # 规格映射与双评
    # ------------------------------------------------------------------
    def mapping_plan(self, previous_sku: str, market: str | None = None) -> dict[str, Any]:
        edges = [m for m in self.mappings if m["previous"] == previous_sku]
        if market is not None:
            edges = [m for m in edges if m["market"] == market]
        if not edges:
            return {"previous": previous_sku, "market": market, "edges": [], "firm_total": 0.0}

        firm_total = sum(float(m["share_min"]) for m in edges)
        max_total = sum(float(m["share_max"]) for m in edges)
        firm_conflict = firm_total > 1.0 + EPS
        planned = []
        for m in edges:
            lo = float(m["share_min"])
            hi = float(m["share_max"])
            other_firm = firm_total - lo
            # 在给其他映射保留最低份额后，本项最多能占多少，避免同一批老件重复出借。
            joint_max = min(hi, max(lo, 1.0 - other_firm)) if not firm_conflict else hi
            planned.append(
                {
                    **m,
                    "share_min": lo,
                    "share_max": hi,
                    "joint_max_with_other_firm_shares": joint_max,
                    "firm_share": lo,
                }
            )
        feasible_max_total = min(1.0, max_total) if not firm_conflict else None
        return {
            "previous": previous_sku,
            "market": market,
            "edges": planned,
            "firm_total": firm_total,
            "firm_conflict": firm_conflict,
            "unallocated_firm_residual": max(0.0, 1.0 - firm_total),
            "sum_share_max": max_total,
            "feasible_max_nonoverlap_total": feasible_max_total,
            "unproven_upper_band": (
                max(0.0, feasible_max_total - firm_total)
                if feasible_max_total is not None and not firm_conflict
                else None
            ),
        }

    def pair_summary(self, line: str, market: str, sku: str) -> dict[str, Any]:
        rows = [
            r
            for r in self.pairs
            if r["line"] == line and r["market"] == market and r["sku"] == sku
        ]
        matrix: dict[tuple[str, str], int] = {}
        total = 0
        eligible = None
        for r in rows:
            n = int(r["count"])
            matrix[(r["old"], r["new"])] = matrix.get((r["old"], r["new"]), 0) + n
            total += n
            eligible = int(r["eligible"])
        old_totals: Counter = Counter()
        new_totals: Counter = Counter()
        for (old, new), n in matrix.items():
            old_totals[old] += n
            new_totals[new] += n
        return {
            "key": (line, market, sku),
            "found": bool(rows),
            "pair_count": total,
            "eligible_shared": eligible,
            "pair_coverage": total / eligible if eligible else None,
            "matrix": {f"{o}->{n}": n for (o, n), n in sorted(matrix.items())},
            "old_totals": dict(old_totals),
            "new_totals": dict(new_totals),
            "_matrix_tuple": matrix,
        }

    def crosswalk(
        self,
        agg: dict[str, Any],
        pair: dict[str, Any],
        *,
        old_points: tuple[float, float, float],
        new_points: tuple[float, float, float],
        min_pairs: int,
    ) -> dict[str, Any]:
        """返回双向口径桥；双评不足时不给出官方转换值。"""
        count = pair["pair_count"]
        sufficient = count >= min_pairs
        matrix: dict[tuple[str, str], int] = pair["_matrix_tuple"]
        result: dict[str, Any] = {
            "pair_count": count,
            "min_pairs": min_pairs,
            "sufficient": sufficient,
            "old_reported_score": self.grade_score(agg, old_points),
            "current_new_score": self.grade_score(agg, new_points),
            "old_counts_brought_to_new": None,
            "old_basis_current_counts": None,
            "old_basis_current_score": None,
            "old_basis_zero_earned_bound": None,
            "implied_old_unknown": None,
            "unsupported_old_grades": [],
            "unsupported_new_grades": [],
        }
        if not sufficient:
            result["reason"] = f"双评记录 {count} 少于 min_pairs={min_pairs}"
            return result

        # 旧 H/M/L -> 新 H/M/L。旧 unknown 不是等级，不替它指定等级。
        converted_new = {"high": 0.0, "middle": 0.0, "low": 0.0}
        unsupported_old = []
        for old_grade in GRADES:
            observed = agg[old_grade]
            row_total = sum(matrix.get((old_grade, new_grade), 0) for new_grade in GRADES)
            if observed > 0 and row_total <= 0:
                unsupported_old.append(old_grade)
                continue
            if row_total:
                for new_grade in GRADES:
                    converted_new[new_grade] += (
                        observed * matrix.get((old_grade, new_grade), 0) / row_total
                    )
        old_to_new_score = None
        denom = sum(converted_new.values())
        if denom:
            old_to_new_score = (
                converted_new["high"] * new_points[0]
                + converted_new["middle"] * new_points[1]
                + converted_new["low"] * new_points[2]
            ) / denom
        result["old_counts_brought_to_new"] = converted_new
        result["old_to_new_score"] = old_to_new_score
        result["unsupported_old_grades"] = unsupported_old

        # 新 H/M/L -> 旧口径。列合计包含 old=unknown，保留为“旧口径无法定级”，
        # 不把它悄悄归一到 H/M/L。
        old_basis = {"high": 0.0, "middle": 0.0, "low": 0.0, "unknown": 0.0}
        unsupported_new = []
        for new_grade in GRADES:
            observed = agg[new_grade]
            col_total = sum(
                n for (old_grade, ng), n in matrix.items() if ng == new_grade
            )
            if observed > 0 and col_total <= 0:
                unsupported_new.append(new_grade)
                continue
            if col_total:
                for old_grade in ("high", "middle", "low", "unknown"):
                    n = matrix.get((old_grade, new_grade), 0)
                    old_basis[old_grade] += observed * n / col_total
        known_old_basis = old_basis["high"] + old_basis["middle"] + old_basis["low"]
        earned = (
            old_basis["high"] * old_points[0]
            + old_basis["middle"] * old_points[1]
            + old_basis["low"] * old_points[2]
        )
        result["old_basis_current_counts"] = old_basis
        result["implied_old_unknown"] = old_basis["unknown"]
        result["old_basis_current_score"] = earned / known_old_basis if known_old_basis else None
        current_total = agg["high"] + agg["middle"] + agg["low"]
        result["old_basis_zero_earned_bound"] = (
            earned / current_total if current_total else None
        )
        result["unsupported_new_grades"] = unsupported_new
        if unsupported_old or unsupported_new:
            result["sufficient"] = False
            result["reason"] = "双评总量达到门槛，但存在被观测等级缺少对应双评格，不能完整转换"
        return result

    # ------------------------------------------------------------------
    # 跨规格/跨版本承诺桥
    # ------------------------------------------------------------------
    def reference_bridge(
        self,
        edition: str,
        months: Iterable[str],
        baseline_months: Iterable[str],
        *,
        market: str | None = None,
        candidate_edition: str | None = None,
    ) -> dict[str, Any]:
        rows = self.basket_rows(edition, market=market)
        if candidate_edition is None:
            others = [e for e in self.editions if e != edition]
            candidate_edition = others[-1] if others else edition
        new_cards = {
            (r["line"], r["market"], r["sku"]): r for r in self.basket_rows(candidate_edition)
        }
        agg_now = self.aggregate(months)
        agg_old = self.aggregate(baseline_months)
        scope_weight = sum(float(r["weight"]) for r in rows)

        entries = []
        certified_weight = 0.0
        caliber_blocked_weight = 0.0
        observation_blocked_weight = 0.0
        mapping_residual_weight = 0.0
        certified_score_mass = 0.0
        certified_conservative_score_mass = 0.0
        certified_visibility_penalty = 0.0
        baseline_score_mass = 0.0
        baseline_available_weight = 0.0

        for b in rows:
            normalized_weight = float(b["weight"]) / scope_weight if scope_weight else 0.0
            old_key = (b["line"], b["market"], b["sku"], b["scale"])
            old_agg = agg_old.get(old_key)
            old_points = (float(b["high_points"]), float(b["middle_points"]), float(b["low_points"]))
            old_score = self.grade_score(old_agg, old_points) if old_agg else None
            if old_score is not None:
                baseline_score_mass += normalized_weight * old_score
                baseline_available_weight += normalized_weight

            plan = self.mapping_plan(b["sku"], b["market"])
            if not plan["edges"]:
                # 无映射但同规格、同尺度当期仍存在时可直接延续。
                now_same = agg_now.get(old_key)
                mass = normalized_weight
                entry: dict[str, Any] = {
                    "basket_key": (b["line"], b["market"], b["sku"]),
                    "normalized_reference_weight": mass,
                    "mapping_id": None,
                    "old_score": old_score,
                    "kind": "direct_same_scale" if now_same else "no_mapping",
                }
                if now_same and old_score is not None:
                    now_score = self.grade_score(now_same, old_points)
                    earned = (
                        now_same["high"] * old_points[0]
                        + now_same["middle"] * old_points[1]
                        + now_same["low"] * old_points[2]
                    )
                    conservative_score = (
                        earned / now_same["inspected"] if now_same["inspected"] else now_score
                    )
                    entry.update(
                        current_score_old_basis=now_score,
                        zero_earned_score=conservative_score,
                        status="certified_direct",
                    )
                    certified_weight += mass
                    certified_score_mass += mass * now_score
                    certified_conservative_score_mass += mass * conservative_score
                    certified_visibility_penalty += mass * (now_score - conservative_score)
                else:
                    entry.update(status="observation_blocked" if not now_same else "baseline_missing")
                    observation_blocked_weight += mass
                entries.append(entry)
                continue

            allocated_edges_weight = 0.0
            for m in plan["edges"]:
                share = m["firm_share"]
                mass = normalized_weight * share
                allocated_edges_weight += mass
                current_sku = m["current"]
                new_b = new_cards.get((b["line"], m["market"], current_sku))
                new_scale = new_b["scale"] if new_b else "new"
                new_points = (
                    (
                        float(new_b["high_points"]),
                        float(new_b["middle_points"]),
                        float(new_b["low_points"]),
                    )
                    if new_b
                    else (100.0, 50.0, 10.0)
                )
                min_pairs = int(b["min_pairs"])
                pair = self.pair_summary(b["line"], m["market"], current_sku)
                now_agg = agg_now.get((b["line"], m["market"], current_sku, new_scale))
                crosswalk = None
                if now_agg is not None and pair["found"]:
                    crosswalk = self.crosswalk(
                        now_agg,
                        pair,
                        old_points=old_points,
                        new_points=new_points,
                        min_pairs=min_pairs,
                    )
                entry = {
                    "mapping_id": m["id"],
                    "basket_key": (b["line"], b["market"], b["sku"]),
                    "previous": b["sku"],
                    "current": current_sku,
                    "mapping_market": m["market"],
                    "firm_share": share,
                    "share_min": m["share_min"],
                    "share_max": m["share_max"],
                    "normalized_reference_weight": mass,
                    "old_score": old_score,
                    "pair_count": pair["pair_count"],
                    "min_pairs": min_pairs,
                    "pair_sufficient": bool(crosswalk and crosswalk["sufficient"]),
                    "current_observed": now_agg is not None,
                    "current_new_score": crosswalk["current_new_score"] if crosswalk else None,
                    "old_basis_current_score": (
                        crosswalk["old_basis_current_score"]
                        if crosswalk and crosswalk["sufficient"]
                        else None
                    ),
                    "old_basis_zero_earned_bound": (
                        crosswalk["old_basis_zero_earned_bound"]
                        if crosswalk and crosswalk["sufficient"]
                        else None
                    ),
                    "implied_old_unknown": (
                        crosswalk["implied_old_unknown"] if crosswalk else None
                    ),
                    "crosswalk": crosswalk,
                }
                if now_agg is None:
                    entry["status"] = "observation_blocked"
                    observation_blocked_weight += mass
                elif not crosswalk or not crosswalk["sufficient"]:
                    entry["status"] = "caliber_blocked"
                    entry["reason"] = (crosswalk or {}).get(
                        "reason", "缺少可用双评交叉记录"
                    )
                    caliber_blocked_weight += mass
                elif old_score is None:
                    entry["status"] = "baseline_missing"
                    observation_blocked_weight += mass
                else:
                    q1 = crosswalk["old_basis_current_score"]
                    q1_cons = crosswalk["old_basis_zero_earned_bound"]
                    full_share = abs(share - 1.0) <= EPS and plan["unallocated_firm_residual"] <= EPS
                    entry["status"] = "certified_full" if full_share else "certified_firm_share_only"
                    entry["performance_delta_old_basis"] = q1 - old_score
                    entry["zero_earned_score"] = q1_cons
                    certified_weight += mass
                    certified_score_mass += mass * q1
                    if q1_cons is not None:
                        certified_conservative_score_mass += mass * q1_cons
                        certified_visibility_penalty += mass * max(0.0, q1 - q1_cons)
                entries.append(entry)

            residual_share = plan["unallocated_firm_residual"]
            if residual_share > EPS:
                mass = normalized_weight * residual_share
                mapping_residual_weight += mass
                entries.append(
                    {
                        "basket_key": (b["line"], b["market"], b["sku"]),
                        "previous": b["sku"],
                        "normalized_reference_weight": mass,
                        "residual_share_within_old_cell": residual_share,
                        "status": "mapping_unresolved",
                        "reason": "最低映射份额未覆盖同一老规格的全部参照；未证实部分不强拆、不重复分配",
                    }
                )

        total_accounted = (
            certified_weight
            + caliber_blocked_weight
            + observation_blocked_weight
            + mapping_residual_weight
        )
        baseline_index = baseline_score_mass if abs(scope_weight - 1.0) <= EPS else None
        # baseline_score_mass 已按 region scope 归一化权重加权，因此就是地区指数。
        proxy_index = baseline_score_mass
        for e in entries:
            if e.get("status", "").startswith("certified") and e.get("old_score") is not None:
                proxy_index += e["normalized_reference_weight"] * (
                    e["old_basis_current_score"] - e["old_score"]
                )
        unresolved_weight = max(0.0, 1.0 - certified_weight)
        proxy_conservative = proxy_index - certified_visibility_penalty
        return {
            "edition": edition,
            "candidate_edition": candidate_edition,
            "market": market,
            "scope_weight_unnormalized": scope_weight,
            "baseline_months": list(baseline_months),
            "baseline_index": baseline_index,
            "certified_weight": certified_weight,
            "caliber_blocked_weight": caliber_blocked_weight,
            "observation_blocked_weight": observation_blocked_weight,
            "mapping_residual_weight": mapping_residual_weight,
            "unresolved_weight": unresolved_weight,
            "weight_accounting_total": total_accounted,
            "weight_accounting_exact": abs(total_accounted - 1.0) <= 1e-7,
            "certified_score_mass": certified_score_mass,
            "certified_conservative_score_mass": certified_conservative_score_mass,
            "certified_visibility_penalty": certified_visibility_penalty,
            # 仅当未衔接部分保持基线表现时的代理值；不是已完成度结论。
            "proxy_if_unresolved_unchanged": proxy_index
            if baseline_available_weight > EPS
            else None,
            "proxy_conservative_zero_earned": proxy_conservative
            if baseline_available_weight > EPS
            else None,
            "absolute_lower_index": certified_score_mass,
            "absolute_upper_index": certified_score_mass + 100.0 * unresolved_weight,
            "fully_certified": unresolved_weight <= EPS,
            "entries": entries,
        }

    def reference_overlap(
        self,
        old_edition: str = "published",
        new_edition: str = "candidate",
    ) -> dict[str, Any]:
        """建立两版参照的保守重合台账。

        对每条映射，老篮侧可证实质量=老权重×share_min，新篮侧质量=新权重；
        取二者较小值作为不需把同一老件重复出借的保守共同量。两侧超出部分和
        完全无映射单元都属于“参照不重合”，不能唯一分解为质量或构成效应。
        """
        old_rows = self.basket_rows(old_edition)
        new_rows = self.basket_rows(new_edition)
        old_by_sku_market = {(r["market"], r["sku"]): r for r in old_rows}
        new_by_key = {(r["line"], r["market"], r["sku"]): r for r in new_rows}
        old_total = sum(float(r["weight"]) for r in old_rows)
        new_total = sum(float(r["weight"]) for r in new_rows)

        entries = []
        touched_old: set[tuple] = set()
        touched_new: set[tuple] = set()
        common_weight = 0.0
        pair_common_weight = 0.0
        old_firm_total = 0.0
        new_mapped_total = 0.0

        previous_groups = sorted({(m["previous"], m["market"]) for m in self.mappings})
        for previous, market in previous_groups:
            old_b = old_by_sku_market.get((market, previous))
            old_weight = float(old_b["weight"]) if old_b else 0.0
            old_key = (old_b["line"], market, previous) if old_b else None
            if old_key:
                touched_old.add(old_key)
            plan = self.mapping_plan(previous, market)
            for m in plan["edges"]:
                new_b = None
                if old_b:
                    new_b = new_by_key.get((old_b["line"], market, m["current"]))
                new_weight = float(new_b["weight"]) if new_b else 0.0
                new_key = (old_b["line"], market, m["current"]) if old_b and new_b else None
                if new_key:
                    touched_new.add(new_key)
                old_firm_mass = old_weight * m["firm_share"]
                comparable = min(old_firm_mass, new_weight)
                old_firm_total += old_firm_mass
                new_mapped_total += new_weight
                common_weight += comparable

                pair = None
                pair_sufficient = False
                if old_b:
                    min_pairs = int(new_b["min_pairs"]) if new_b else int(old_b["min_pairs"])
                    pair = self.pair_summary(old_b["line"], market, m["current"])
                    pair_sufficient = pair["pair_count"] >= min_pairs
                    if pair_sufficient:
                        pair_common_weight += comparable
                entries.append(
                    {
                        "mapping_id": m["id"],
                        "line": old_b["line"] if old_b else None,
                        "market": market,
                        "previous": previous,
                        "current": m["current"],
                        "old_basket_weight": old_weight,
                        "old_firm_mapped_weight": old_firm_mass,
                        "new_basket_weight": new_weight,
                        "conservative_common_weight": comparable,
                        "old_side_excess_weight": max(0.0, old_firm_mass - comparable),
                        "new_side_excess_weight": max(0.0, new_weight - comparable),
                        "pair_count": pair["pair_count"] if pair else 0,
                        "pair_sufficient": pair_sufficient,
                        "pair_supported_common_weight": comparable if pair_sufficient else 0.0,
                    }
                )

            if old_b and plan["unallocated_firm_residual"] > EPS:
                residual = old_weight * plan["unallocated_firm_residual"]
                entries.append(
                    {
                        "mapping_id": None,
                        "line": old_b["line"],
                        "market": market,
                        "previous": previous,
                        "current": None,
                        "old_basket_weight": old_weight,
                        "old_firm_mapped_weight": 0.0,
                        "new_basket_weight": 0.0,
                        "conservative_common_weight": 0.0,
                        "old_unmapped_residual_weight": residual,
                        "status": "mapping_residual",
                    }
                )

        old_unmapped_weight = 0.0
        for r in old_rows:
            key = (r["line"], r["market"], r["sku"])
            if key not in touched_old:
                w = float(r["weight"])
                old_unmapped_weight += w
                entries.append(
                    {
                        "mapping_id": None,
                        "line": r["line"],
                        "market": r["market"],
                        "previous": r["sku"],
                        "current": None,
                        "old_basket_weight": w,
                        "conservative_common_weight": 0.0,
                        "old_unmapped_residual_weight": w,
                        "status": "old_basket_cell_without_mapping",
                    }
                )

        new_unmapped_weight = 0.0
        for r in new_rows:
            key = (r["line"], r["market"], r["sku"])
            if key not in touched_new:
                w = float(r["weight"])
                new_unmapped_weight += w
                entries.append(
                    {
                        "mapping_id": None,
                        "line": r["line"],
                        "market": r["market"],
                        "previous": None,
                        "current": r["sku"],
                        "new_basket_weight": w,
                        "conservative_common_weight": 0.0,
                        "new_unmapped_weight": w,
                        "status": "new_basket_cell_without_mapping",
                    }
                )

        old_residual_total = sum(e.get("old_unmapped_residual_weight", 0.0) for e in entries)
        old_excess_total = sum(e.get("old_side_excess_weight", 0.0) for e in entries) + old_residual_total
        new_excess_total = sum(e.get("new_side_excess_weight", 0.0) for e in entries) + new_unmapped_weight
        return {
            "old_edition": old_edition,
            "new_edition": new_edition,
            "old_total_weight": old_total,
            "new_total_weight": new_total,
            "mapping_supported_common_weight": common_weight,
            "double_review_supported_common_weight": pair_common_weight,
            "old_reference_nonoverlap_weight": old_excess_total,
            "new_reference_nonoverlap_weight": new_excess_total,
            "new_unmapped_weight": new_unmapped_weight,
            "old_unmapped_or_residual_weight": old_residual_total,
            "no_unique_cross_edition_decomposition": True,
            "entries": entries,
        }

    # ------------------------------------------------------------------
    # 证据登记、目标与分解
    # ------------------------------------------------------------------
    def evidence_register(self, candidate_edition: str = "candidate") -> dict[str, Any]:
        new_rows = self.basket_rows(candidate_edition)
        entries = []
        for b in new_rows:
            pair = self.pair_summary(b["line"], b["market"], b["sku"])
            mappings = [
                m
                for m in self.mappings
                if m["current"] == b["sku"] and m["market"] == b["market"]
            ]
            mapping = mappings[0] if mappings else None
            min_pairs = int(b["min_pairs"])
            sufficient = pair["pair_count"] >= min_pairs
            full_mapping = bool(mapping and abs(float(mapping["share_min"]) - 1.0) <= EPS)
            if full_mapping and sufficient:
                status = "continuous_full_chain"
                allowed = True
            elif mapping and sufficient:
                status = "continuous_supported_share_only"
                allowed = "supported_share_only"
            elif mapping and not sufficient:
                status = "mapping_known_but_double_review_insufficient"
                allowed = False
            else:
                status = "new_baseline_only"
                allowed = False
            entries.append(
                {
                    "line": b["line"],
                    "market": b["market"],
                    "sku": b["sku"],
                    "candidate_weight": float(b["weight"]),
                    "mapping_id": mapping["id"] if mapping else None,
                    "previous": mapping["previous"] if mapping else None,
                    "share_min": float(mapping["share_min"]) if mapping else 0.0,
                    "share_max": float(mapping["share_max"]) if mapping else 0.0,
                    "pair_count": pair["pair_count"],
                    "min_pairs": min_pairs,
                    "pair_sufficient": sufficient,
                    "status": status,
                    "carry_forward_allowed": allowed,
                }
            )

        lines = []
        for line in sorted({e["line"] for e in entries}):
            le = [e for e in entries if e["line"] == line]
            full = [e for e in le if e["status"] == "continuous_full_chain"]
            partial = [e for e in le if e["status"] == "continuous_supported_share_only"]
            blocked = [
                e for e in le if e["status"] == "mapping_known_but_double_review_insufficient"
            ]
            new_only = [e for e in le if e["status"] == "new_baseline_only"]
            if full and not partial and not blocked and not new_only:
                line_status = "line_wide_continuous"
            elif full or partial:
                line_status = "partial_continuous_evidence_limited_scope"
            else:
                line_status = "new_baseline_only"
            lines.append(
                {
                    "line": line,
                    "status": line_status,
                    "full_chain_scopes": [f"{e['market']}/{e['sku']}" for e in full],
                    "partial_scopes": [f"{e['market']}/{e['sku']}@{e['share_min']:.0%}" for e in partial],
                    "blocked_scopes": [f"{e['market']}/{e['sku']}" for e in blocked],
                    "new_baseline_scopes": [f"{e['market']}/{e['sku']}" for e in new_only],
                }
            )
        return {"edition": candidate_edition, "lines": lines, "entries": entries}

    def target_by_id(self, target_id: str) -> dict[str, str]:
        for r in self.targets:
            if r["id"] == target_id:
                return r
        raise KeyError(f"目标 {target_id} 不存在")

    def original_commitments(self, quarter: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "level": r["level"],
                "market": r["market"] or None,
                "edition": r["edition"],
                "target": float(r["target"]),
                "fixed": r["fixed"] == "1",
                "effective_from": r["effective_from"],
                "effective_until": r["effective_until"],
                "active_in_quarter": quarter_active(
                    quarter, r["effective_from"], r["effective_until"]
                )
                if quarter
                else None,
                "region_floor": float(r["region_floor"]),
                "region_cap": float(r["region_cap"]),
            }
            for r in self.targets
            if r["fixed"] == "1"
        ]

    def effective_overlaps(self, quarter: str) -> list[dict[str, Any]]:
        active = [r for r in self.targets if quarter_active(quarter, r["effective_from"], r["effective_until"])]
        overlaps = []
        by_quarter_edition: dict[str, list[str]] = defaultdict(list)
        # 找出同一季度同时生效但版本/承诺性质不同的目标。
        for q in [f"{quarter_key(quarter)[0]}-Q{i}" for i in range(1, 5)] + [
            f"{quarter_key(quarter)[0] + 1}-Q{i}" for i in range(1, 5)
        ]:
            qa = [r for r in self.targets if quarter_active(q, r["effective_from"], r["effective_until"])]
            if len({r["edition"] for r in qa}) > 1:
                overlaps.append(
                    {
                        "quarter": q,
                        "target_ids": [r["id"] for r in qa],
                        "editions": sorted({r["edition"] for r in qa}),
                        "instruction": "重叠期内已公布固定目标仍按原承诺解释；候选目标只能并行试算，不能悄悄替代。",
                    }
                )
        return overlaps

    def evaluate_target(
        self,
        target_id: str,
        *,
        quarter: str | None = None,
        months: Iterable[str] | None = None,
        baseline_months: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        target = self.target_by_id(target_id)
        period = self.resolve_period(months=months, quarter=quarter)
        q = quarter or period["quarter"]
        if months is None:
            use_months = period["months"]
        else:
            use_months = list(months)
        if baseline_months is None:
            baseline = previous_year_months(use_months)
        else:
            baseline = list(baseline_months)

        market = target["market"] or None
        edition = target["edition"]
        target_value = float(target["target"])
        view = self.basket_view(edition, use_months, market=market) if use_months else None
        active = quarter_active(q, target["effective_from"], target["effective_until"])
        result: dict[str, Any] = {
            "id": target_id,
            "target": target,
            "quarter": q,
            "active_in_quarter": active,
            "period": period,
            "market": market,
            "edition": edition,
            "target_value": target_value,
            "fixed": target["fixed"] == "1",
            "basket_view": view,
            "bridge": None,
            "adopted_index_explanation": [],
            "excluded_market_shares": [],
            "original_commitments": self.original_commitments(q),
            "effective_overlaps": self.effective_overlaps(q),
        }

        shares = self.market_shares(use_months)
        if market:
            result["excluded_market_shares"] = [s for s in shares if s["market"] != market]
            result["included_market_shares"] = [s for s in shares if s["market"] == market]
        else:
            result["included_market_shares"] = shares

        if not use_months:
            result["status"] = "no_observations"
            result["gap"] = None
            result["adopted_index_explanation"].append("指定期间没有任何计数记录；不得把缺报当作零件或达标。")
            return result

        result["adopted_index_explanation"].append(
            "指数=Σ固定篮子权重×规格得分；规格得分=(high×高分+middle×中分+low×低分)/(high+middle+low)。unknown 不赋等级分。"
        )
        result["adopted_index_explanation"].append(
            "地区指数只在该 market 的篮子权重范围内归一化；缺单元时输出 partial_normalized_index_not_official，不重新分篮。"
        )

        if view and view["complete"]:
            idx = view["index"]
            result["index"] = idx
            result["gap"] = idx - target_value
            provisional = not period["quarter_calendar_complete"]
            if not active:
                result["status"] = "future_or_inactive_target_baseline"
            elif provisional:
                result["status"] = "provisional_period_not_quarter_complete"
            else:
                result["status"] = "met" if idx >= target_value - EPS else "missed"
            if view["non_probability_sample"]:
                result["sampling_caveat"] = (
                    "范围内含 selected/非例行抽样；现有资料没有纠偏权重，可列观测值，但不能宣称概率样本总体推断。"
                )
            return result

        # 已公布篮子在当期不完整：尝试跨规格/双评桥。
        bridge = self.reference_bridge(
            edition,
            use_months,
            baseline,
            market=market,
        )
        result["bridge"] = bridge
        result["index"] = None
        result["gap"] = None
        result["proxy_index"] = bridge["proxy_if_unresolved_unchanged"]
        result["proxy_gap"] = (
            bridge["proxy_if_unresolved_unchanged"] - target_value
            if bridge["proxy_if_unresolved_unchanged"] is not None
            else None
        )
        result["proxy_conservative_zero_earned"] = bridge["proxy_conservative_zero_earned"]
        result["proxy_conservative_gap"] = (
            bridge["proxy_conservative_zero_earned"] - target_value
            if bridge["proxy_conservative_zero_earned"] is not None
            else None
        )
        if bridge["fully_certified"] and period["quarter_calendar_complete"]:
            idx = bridge["proxy_if_unresolved_unchanged"]
            result["index"] = idx
            result["gap"] = idx - target_value
            result["status"] = "met" if idx >= target_value - EPS else "missed"
            if not active:
                result["status"] = "inactive_reference_estimate"
        elif bridge["fully_certified"]:
            result["status"] = "provisional_period_not_quarter_complete"
            result["index"] = bridge["proxy_if_unresolved_unchanged"]
            result["gap"] = result["index"] - target_value
            if not active:
                result["status"] = "inactive_reference_estimate"
        else:
            result["status"] = "committed_definition_not_certifiable"
            result["adopted_index_explanation"].append(
                "固定篮子不能在当前资料中完整衔接；候选指数只能作为新结构读数，不能替代原承诺完成度。"
            )
        return result

    def trace_region(self, market: str, quarter: str) -> dict[str, Any]:
        ids = [r["id"] for r in self.targets if r["level"] == "regional" and r["market"] == market]
        if not ids:
            raise KeyError(f"地区 {market!r} 没有地区目标")
        return {
            "market": market,
            "quarter": quarter,
            "target_ids": ids,
            "traces": [self.evaluate_target(tid, quarter=quarter) for tid in ids],
            "original_commitments": self.original_commitments(quarter),
        }

    def target_allocation(self, total_id: str = "T03", quarter: str | None = None) -> dict[str, Any]:
        total = self.target_by_id(total_id)
        edition = total["edition"]
        rows = self.basket_rows(edition)
        region_weight: Counter = Counter()
        for r in rows:
            region_weight[r["market"]] += float(r["weight"])

        constraints: dict[str, dict[str, Any]] = {}
        for r in self.targets:
            if r["level"] != "regional" or not r["market"]:
                continue
            active = quarter_active(quarter, r["effective_from"], r["effective_until"]) if quarter else True
            c = constraints.setdefault(
                r["market"],
                {
                    "market": r["market"],
                    "weight_candidate": region_weight.get(r["market"], 0.0),
                    "target_rows": [],
                },
            )
            c["target_rows"].append(
                {
                    "id": r["id"],
                    "edition": r["edition"],
                    "target": float(r["target"]),
                    "fixed": r["fixed"] == "1",
                    "floor": float(r["region_floor"]),
                    "cap": float(r["region_cap"]),
                    "active": active,
                }
            )

        # 合同硬约束：fixed=1 的地区必须精确等于合同目标；其他目标用 floor/cap。
        hard_min = hard_max = 0.0
        hard_fixed_value = 0.0
        hard_details = []
        for market, w in region_weight.items():
            c = constraints.get(market)
            fixed_rows = [x for x in (c["target_rows"] if c else []) if x["fixed"]]
            flexible_rows = [x for x in (c["target_rows"] if c else []) if not x["fixed"]]
            if fixed_rows:
                # 同一地区若有多条固定承诺，不能擅自选一条；资料中通常一条。
                vals = [x["target"] for x in fixed_rows]
                required = vals[0]
                lo = hi = required
                hard_fixed_value += w * required
            elif flexible_rows:
                x = flexible_rows[-1]
                lo, hi = x["floor"], x["cap"]
            else:
                lo, hi = 0.0, 100.0
            hard_min += w * lo
            hard_max += w * hi
            hard_details.append({"market": market, "weight": w, "min": lo, "max": hi})

        # 假设下一轮可以在 floor/cap 内重谈（不要求 fixed 精确），看目标包络是否容得下总目标。
        envelope_min = envelope_max = 0.0
        envelope_details = []
        for market, w in region_weight.items():
            c = constraints.get(market)
            all_rows = c["target_rows"] if c else []
            if all_rows:
                # 若不同版本给了不同边界，不能跨版本拼成合法承诺；算术上取并集仅用于暴露缺口。
                lo = min(x["floor"] for x in all_rows)
                hi = max(x["cap"] for x in all_rows)
            else:
                lo, hi = 0.0, 100.0
            envelope_min += w * lo
            envelope_max += w * hi
            envelope_details.append({"market": market, "weight": w, "floor": lo, "cap": hi})

        target_value = float(total["target"])
        proposed_weighted = 0.0
        proposed_details = []
        proposed_complete = True
        for market, w in region_weight.items():
            c = constraints.get(market)
            rows = c["target_rows"] if c else []
            fixed_rows = [r for r in rows if r["fixed"]]
            if fixed_rows:
                value = fixed_rows[0]["target"]
                source_id = fixed_rows[0]["id"]
            elif rows:
                value = rows[-1]["target"]
                source_id = rows[-1]["id"]
            else:
                value = None
                source_id = None
                proposed_complete = False
            if value is not None:
                proposed_weighted += w * value
            proposed_details.append({"market": market, "weight": w, "target": value, "target_id": source_id})

        period = self.resolve_period(quarter=quarter)
        baseline_views = {}
        baseline_index = None
        if period["months"]:
            bv = self.basket_view(edition, period["months"])
            baseline_index = bv["index"]
            for market in region_weight:
                rv = self.basket_view(edition, period["months"], market=market)
                baseline_views[market] = rv["index"]

        return {
            "total_target_id": total_id,
            "total_target": target_value,
            "edition": edition,
            "quarter": quarter or period["quarter"],
            "proposed_weighted_target": proposed_weighted if proposed_complete else None,
            "proposed_gap_above_total": (
                target_value - proposed_weighted if proposed_complete else None
            ),
            "proposed_details": proposed_details,
            "region_weights": dict(region_weight),
            "constraints": list(constraints.values()),
            "hard_contract_arithmetic": {
                "achievable_min": hard_min,
                "achievable_max": hard_max,
                "fixed_weighted_value": hard_fixed_value,
                "total_feasible": hard_min - EPS <= target_value <= hard_max + EPS,
                "gap_above_max": max(0.0, target_value - hard_max),
                "details": hard_details,
                "warning": "这里把 published 固定值与 candidate 总目标放入同一加权式仅为暴露冲突；口径不同，不构成合法合并承诺。",
            },
            "floor_cap_envelope": {
                "weighted_min": envelope_min,
                "weighted_max": envelope_max,
                "total_feasible": envelope_min - EPS <= target_value <= envelope_max + EPS,
                "gap_above_max": max(0.0, target_value - envelope_max),
                "details": envelope_details,
            },
            "baseline_period": period,
            "baseline_index": baseline_index,
            "baseline_gap_to_total": baseline_index - target_value if baseline_index is not None else None,
            "regional_baseline_indices": baseline_views,
            "cannot_reweight_to_prove_all_met": True,
        }

    def full_report(self, quarter: str | None = None, candidate_edition: str | None = None) -> dict[str, Any]:
        period = self.resolve_period(quarter=quarter)
        q = period["quarter"]
        months = period["months"]
        baseline = previous_year_months(months)
        if candidate_edition is None:
            candidate_edition = "candidate" if "candidate" in self.editions else self.editions[-1]
        evidence = self.evidence_register(candidate_edition)
        latest_two = self.available_months[-2:]
        latest_change = (
            self.same_basket_change(candidate_edition, [latest_two[-2]], [latest_two[-1]])
            if len(latest_two) >= 2
            else None
        )
        composition = self.composition_view(candidate_edition, months) if months else None
        target_results = [self.evaluate_target(r["id"], quarter=q) for r in self.targets]
        allocation = self.target_allocation("T03", quarter=q) if any(r["id"] == "T03" for r in self.targets) else None
        return clean_for_json(
            {
                "data_directory": str(self.directory),
                "quarter": q,
                "period": period,
                "baseline_months": baseline,
                "editions": self.editions,
                "market_shares": self.market_shares(months),
                "sampling": self.sampling_register(months),
                "latest_same_basket_change": latest_change,
                "composition": composition,
                "candidate_edition": candidate_edition,
                "current_reading_candidate": self.actual_reading(candidate_edition, months),
                "published_basket_current": self.basket_view("published", months)
                if "published" in self.editions
                else None,
                "candidate_basket_current": self.basket_view(candidate_edition, months),
                "yoy_published_bridge": self.reference_bridge(
                    "published", months, baseline, candidate_edition=candidate_edition
                )
                if "published" in self.editions
                else None,
                "reference_overlap": self.reference_overlap("published", candidate_edition)
                if "published" in self.editions
                else None,
                "yoy_operating_delta_by_line_market": self.operating_bridge(
                    baseline,
                    months,
                    old_edition="published",
                    new_edition=candidate_edition,
                    group_fields=("line", "market"),
                )
                if months and baseline
                else None,
                "yoy_operating_delta_by_line": self.operating_bridge(
                    baseline,
                    months,
                    old_edition="published",
                    new_edition=candidate_edition,
                    group_fields=("line",),
                )
                if months and baseline
                else None,
                "yoy_operating_delta_by_sku": self.operating_bridge(
                    baseline,
                    months,
                    old_edition="published",
                    new_edition=candidate_edition,
                    group_fields=("line", "market", "sku"),
                )
                if months and baseline
                else None,
                "evidence": evidence,
                "targets": target_results,
                "allocation": allocation,
                "commitment_separation": self.commitment_separation(q),
                "effective_overlaps": self.effective_overlaps(q),
            }
        )

    def commitment_separation(self, quarter: str) -> list[dict[str, Any]]:
        return [
            {
                "upstream_indicator": "published 旧边界等级/固定地区目标 T01/T02",
                "downstream_indicator": "candidate 新边界总目标 T03/新市场目标 T04",
                "cannot_combine": ["published/T01/domestic", "published/T02/export"],
                "with": ["candidate/T03/all", "candidate/T04/new"],
                "kind": "reference_caliber",
                "reason": "旧篮子、旧分值/边界与候选新篮子并存；未取得完整规格映射和足够双评前，不能写进同一张承诺。",
            },
            {
                "upstream_indicator": "routine 概率抽检覆盖率/等级可见性",
                "downstream_indicator": "selected 定向抽检观测指数",
                "cannot_combine": ["sampling=routine"],
                "with": ["sampling=selected"],
                "kind": "sampling_design",
                "reason": "例行概率样本与定向抽检不是同一概率样本，且资料没有定向抽检纠偏权重；只能并列观测，不能混成覆盖率或总体达标证明。",
            },
            {
                "upstream_indicator": "eligible/inspected coverage 与 unknown reason",
                "downstream_indicator": "加权等级质量分/季度供货目标",
                "cannot_combine": ["coverage/unknown diagnostic"],
                "with": ["quality index points"],
                "kind": "indicator_type",
                "reason": "覆盖率和 unknown 率是可见性边界，不是等级分；unknown 不能按高/中/低任意赋分。",
            },
            {
                "upstream_indicator": "产品线/市场/SKU 旧篮小计",
                "downstream_indicator": "不同 edition 的单一加权承诺",
                "cannot_combine": ["line/market/sku subtotals from different editions"],
                "with": ["single weighted promise"],
                "kind": "reference_non_overlap",
                "reason": "参照不重合的权重只能列为未衔接/未分解量，不能通过重新分篮或归一化制造精确总承诺。",
            },
        ]
