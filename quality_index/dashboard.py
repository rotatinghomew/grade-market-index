"""生成无外部依赖、可离线打开的谈判仪表盘。"""

from __future__ import annotations

import json
from pathlib import Path

from .core import Project, clean_for_json


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _num(x, n=2):
    return "—" if x is None else f"{x:.{n}f}"


def write_dashboard(project: Project, output: str | Path, quarter: str | None = "2026-Q2") -> Path:
    data = clean_for_json(project.full_report(quarter))
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>供货质量指数谈判仪表盘</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --line:#d9dee7; --text:#172033; --muted:#667085; --blue:#2457c5; --red:#b42318; --amber:#b54708; --green:#067647; }
* { box-sizing:border-box; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--text); }
header { padding:20px 28px; background:#102a56; color:white; }
header h1 { margin:0 0 6px; font-size:22px; }
header .meta { color:#c9d6f2; font-size:13px; }
nav { display:flex; gap:6px; padding:10px 20px 0; background:#102a56; flex-wrap:wrap; }
nav button { border:0; padding:10px 14px; border-radius:8px 8px 0 0; background:#244273; color:#dbe6ff; cursor:pointer; font-size:14px; }
nav button.active { background:var(--bg); color:var(--blue); font-weight:700; }
main { padding:22px 28px 60px; }
.tab { display:none; } .tab.active { display:block; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:14px; margin-bottom:18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; box-shadow:0 1px 2px rgba(16,24,40,.05); }
.card .label { color:var(--muted); font-size:13px; margin-bottom:8px; }
.card .value { font-size:26px; font-weight:750; }
.good { color:var(--green); } .bad { color:var(--red); } .warn { color:var(--amber); }
.panel { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; margin:14px 0; }
h2 { font-size:18px; margin:0 0 12px; } h3 { font-size:15px; margin:18px 0 8px; }
table { border-collapse:collapse; width:100%; font-size:13px; background:white; }
th,td { border-bottom:1px solid var(--line); padding:8px 9px; text-align:right; white-space:nowrap; }
th:first-child,td:first-child, td.left { text-align:left; white-space:normal; }
th { background:#eef2f8; position:sticky; top:0; }
.tablewrap { overflow:auto; border:1px solid var(--line); border-radius:10px; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; background:#eef2f8; }
.badge.bad { background:#fee4e2; } .badge.warn { background:#fef0c7; } .badge.good { background:#dcfae6; }
.muted { color:var(--muted); font-size:13px; line-height:1.65; }
ul { line-height:1.8; font-size:14px; }
.targetgrid { display:grid; grid-template-columns:280px 1fr; gap:16px; }
.targetlist button { display:block; width:100%; text-align:left; margin:8px 0; padding:12px; border:1px solid var(--line); border-radius:10px; background:white; cursor:pointer; }
.targetlist button.active { border-color:var(--blue); box-shadow:0 0 0 2px rgba(36,87,197,.15); }
.kv { display:grid; grid-template-columns:190px 1fr; gap:6px 12px; font-size:14px; }
.kv div:nth-child(odd) { color:var(--muted); }
@media (max-width:900px) { .targetgrid { grid-template-columns:1fr; } }
</style>
</head>
<body>
<header>
  <h1>供货质量指数谈判仪表盘</h1>
  <div class="meta" id="meta"></div>
</header>
<nav id="nav">
  <button data-tab="current" class="active">当前经营读数</button>
  <button data-tab="basket">同篮子表现</button>
  <button data-tab="composition">规格/市场构成</button>
  <button data-tab="bridge">跨季与参照差额</button>
  <button data-tab="targets">地区目标回溯</button>
  <button data-tab="evidence">证据与口径边界</button>
</nav>
<main>
  <section id="current" class="tab active"></section>
  <section id="basket" class="tab"></section>
  <section id="composition" class="tab"></section>
  <section id="bridge" class="tab"></section>
  <section id="targets" class="tab"></section>
  <section id="evidence" class="tab"></section>
</main>
<script id="report-data" type="application/json">{payload}</script>
<script>
const D = JSON.parse(document.getElementById('report-data').textContent);
const fmt = (x,n=2) => x === null || x === undefined ? '—' : Number(x).toFixed(n);
const pct = x => x === null || x === undefined ? '—' : (x*100).toFixed(1)+'%';
const esc = x => String(x ?? '—').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const keyText = r => (r.key || []).map(esc).join('/');
function table(rows, cols) {
  return `<div class="tablewrap"><table><thead><tr>${cols.map(c=>`<th class="${c.align==='left'?'left':''}">${c.title}</th>`).join('')}</tr></thead><tbody>${
    rows.map(r=>`<tr>${cols.map(c=>`<td class="${c.align==='left'?'left':''}">${c.render ? c.render(r) : esc(r[c.key])}</td>`).join('')}</tr>`).join('')
  }</tbody></table></div>`;
}
function card(label, value, cls='') { return `<div class="card"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`; }
function statusBadge(s) {
  const bad = String(s||'').includes('not_certifiable') || String(s||'').includes('missed') || String(s||'').includes('blocked');
  const good = String(s||'') === 'met' || String(s||'').includes('continuous_full');
  return `<span class="badge ${bad?'bad':good?'good':'warn'}">${esc(s)}</span>`;
}
document.getElementById('meta').textContent = `${D.quarter} · 月份 ${D.period.months.join(', ')} · 季度完整=${D.period.quarter_calendar_complete} · 数据 ${D.data_directory}`;

document.getElementById('current').innerHTML = `
 <div class="cards">
  ${card('实际件数加权指数', fmt(D.current_reading_candidate.index))}
  ${card('candidate 固定篮指数', fmt(D.candidate_basket_current.index))}
  ${card('实际−篮子构成差', fmt(D.current_reading_candidate.index-D.candidate_basket_current.index), 'warn')}
  ${card('pooled 覆盖率', pct(D.candidate_basket_current.coverage))}
  ${card('unknown 率', pct(D.candidate_basket_current.unknown_rate), 'warn')}
 </div>
 <div class="panel"><h2>市场份额：新市场计入当前读数，但在原承诺回溯中单独排除</h2>
  ${table(D.market_shares, [
    {key:'market',title:'市场',align:'left'},
    {title:'可定级份额',render:r=>pct(r.known_share)},
    {title:'抽检件数',render:r=>r.inspected_pieces},
    {title:'覆盖率',render:r=>pct(r.coverage)}
  ])}
 </div>
 <div class="panel"><h2>规格经营读数</h2>
  ${table(D.current_reading_candidate.rows, [
    {title:'线/市场/规格',align:'left',render:r=>`${esc(r.line)}/${esc(r.market)}/${esc(r.sku)}`},
    {title:'实际权重',render:r=>pct(r.actual_weight)},
    {title:'得分',render:r=>fmt(r.score)},
    {title:'覆盖率',render:r=>pct(r.coverage)},
    {title:'unknown率',render:r=>pct(r.unknown_rate)},
    {title:'抽样',render:r=>r.sampling_modes.join(',')}
  ])}
 </div>`;

const ch = D.latest_same_basket_change;
document.getElementById('basket').innerHTML = `
 <div class="panel"><h2>固定篮子完整性</h2><p class="muted">缺单元时不重新归一化；partial 数只能观察，不能作为合同指数。</p>
 <div class="cards">
  ${card('官方篮指数', fmt(D.candidate_basket_current.index))}
  ${card('已观测权重', pct(D.candidate_basket_current.observed_weight))}
  ${card('缺失权重', pct(D.candidate_basket_current.missing_weight), 'bad')}
  ${card('月度共同篮变化', ch?fmt(ch.performance_delta):'—', ch&&ch.performance_delta<0?'bad':'good')}
 </div>
 ${ch ? `<p class="muted">${ch.edition}：${fmt(ch.same_basket_index_before)} → ${fmt(ch.same_basket_index_after)}；共同权重 ${pct(ch.common_weight)}；完整篮=${ch.is_full_basket}。擅自归一化的后期值=${fmt(ch.naive_partial_after_index)}。</p>
 ${table(ch.changes, [
   {title:'规格',align:'left',render:keyText},
   {title:'共同内权重',render:r=>pct(r.common_relative_weight)},
   {title:'前值',render:r=>fmt(r.score_before)},
   {title:'后值',render:r=>fmt(r.score_after)},
   {title:'变化',render:r=>fmt(r.score_delta),},
   {title:'加权变化',render:r=>fmt(r.weighted_delta)}
 ])}` : ''}
 </div>
 <div class="panel"><h2>篮子单元</h2>${table(D.candidate_basket_current.items, [
   {title:'单元',align:'left',render:r=>`${esc(r.line)}/${esc(r.market)}/${esc(r.sku)}/${esc(r.scale)}`},
   {title:'权重',render:r=>fmt(r.weight)},
   {title:'得分',render:r=>fmt(r.score)},
   {title:'unknown零收益界',render:r=>fmt(r.zero_earned_visibility_score)},
   {title:'覆盖率',render:r=>pct(r.coverage)},
   {title:'unknown率',render:r=>pct(r.unknown_rate)},
   {title:'状态',render:r=>statusBadge(r.status)}
 ])}</div>`;

const c = D.composition;
document.getElementById('composition').innerHTML = `
 <div class="panel"><h2>实际构成相对固定篮子</h2>
  <div class="cards">${card('构成差额', fmt(c.composition_gap_actual_ref_minus_basket), 'warn')}${card('参照外份额', pct(c.outside_reference_share))}</div>
  ${table(c.rows, [
    {title:'规格',align:'left',render:keyText},
    {title:'篮权重',render:r=>pct(r.basket_weight)},
    {title:'实际全局权重',render:r=>pct(r.actual_global_weight)},
    {title:'参照内实际权重',render:r=>pct(r.actual_reference_weight)},
    {title:'权重差',render:r=>pct(r.mix_weight_delta)},
    {title:'构成贡献',render:r=>fmt(r.mix_effect_contribution)}
  ])}
 </div>`;

const y = D.yoy_operating_delta_by_line_market, o = D.reference_overlap, b = D.yoy_published_bridge;
document.getElementById('bridge').innerHTML = `
 <div class="panel"><h2>同比总差额先精确闭合</h2>
  <div class="cards">${card('旧经营指数',fmt(y.old_index))}${card('新经营指数',fmt(y.new_index))}${card('总差额',fmt(y.total_delta),'warn')}${card('分解残差',fmt(y.reconciliation_residual,4))}</div>
  <p class="muted">持续组 raw 得分效应 ${fmt(y.continuing_raw_score_effect)}；持续组构成 ${fmt(y.continuing_mix_effect)}；新市场 scope ${fmt(y.new_scope_effect)}。两版分值卡/边界不同，raw 得分效应不能直接称为质量变化。</p>
  ${table(y.rows,[
    {title:'线/市场',align:'left',render:keyText},
    {title:'旧权重',render:r=>pct(r.old_weight)},{title:'新权重',render:r=>pct(r.new_weight)},
    {title:'旧分',render:r=>fmt(r.old_score)},{title:'新分',render:r=>fmt(r.new_score)},
    {title:'raw得分',render:r=>fmt(r.score_effect)},
    {title:'结构/scope',render:r=>fmt(r.mix_effect ?? r.scope_effect)},
    {title:'总贡献差',render:r=>fmt(r.contribution_delta)}
  ])}
 </div>
 <div class="panel"><h2>参照不重合量</h2>
  <ul><li>映射可支持共同权重：<b>${pct(o.mapping_supported_common_weight)}</b>；双评支持：<b>${pct(o.double_review_supported_common_weight)}</b></li>
  <li>旧篮侧不重合：<b>${pct(o.old_reference_nonoverlap_weight)}</b>；新篮侧不重合：<b>${pct(o.new_reference_nonoverlap_weight)}</b></li>
  <li>两侧不是同一个可相减的量；没有合同指定共同权重时，不存在唯一跨版本质量/构成分解。</li></ul>
 </div>
 <div class="panel"><h2>published 承诺桥权重台账</h2>
  <div class="cards">${card('可认证',pct(b.certified_weight),'good')}${card('双评阻断',pct(b.caliber_blocked_weight),'bad')}${card('缺观测',pct(b.observation_blocked_weight),'warn')}${card('映射residual',pct(b.mapping_residual_weight),'warn')}</div>
  <p class="muted">台账合计 ${pct(b.weight_accounting_total)}，精确闭合=${b.weight_accounting_exact}；证据代理指数 ${fmt(b.proxy_if_unresolved_unchanged)}；零收益审慎代理 ${fmt(b.proxy_conservative_zero_earned)}；最宽区间 [${fmt(b.absolute_lower_index)}, ${fmt(b.absolute_upper_index)}]。</p>
  ${table(b.entries.filter(e=>e.mapping_id),[
    {key:'mapping_id',title:'Mapping',align:'left'},
    {title:'老→新',align:'left',render:r=>`${esc(r.previous)}→${esc(r.current)}`},
    {key:'mapping_market',title:'市场',align:'left'},
    {title:'份额',render:r=>pct(r.firm_share)},
    {title:'台账权重',render:r=>pct(r.normalized_reference_weight)},
    {title:'双评',render:r=>`${r.pair_count}/${r.min_pairs}`},
    {title:'旧口径当期分',render:r=>fmt(r.old_basis_current_score)},
    {title:'状态',render:r=>statusBadge(r.status)}
  ])}
 </div>`;

const alloc = D.allocation;
function renderTarget(id) {
 const t = D.targets.find(x=>x.id===id);
 return `<div class="kv">
   <div>状态</div><div>${statusBadge(t.status)}</div>
   <div>版本/地区</div><div>${t.edition} / ${t.market||'all'}</div>
   <div>目标</div><div>${fmt(t.target_value)}；固定=${t.fixed}</div>
   <div>采用指数</div><div>${fmt(t.index ?? t.proxy_index)} ${t.proxy_index!==null&&t.index===null?'（代理，非达标结论）':''}</div>
   <div>差额</div><div>${fmt(t.gap ?? t.proxy_gap)}</div>
  </div>
  <h3>采用的指数解释</h3><ul>${t.adopted_index_explanation.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>
  <h3>不能纳入的市场份额</h3>${table(t.excluded_market_shares,[
    {key:'market',title:'市场',align:'left'},{title:'份额',render:r=>pct(r.known_share)},{title:'覆盖率',render:r=>pct(r.coverage)}])}
  <h3>仍约束下一轮的原承诺</h3>${table(t.original_commitments,[
    {key:'id',title:'ID',align:'left'},{key:'market',title:'地区',align:'left'},{key:'edition',title:'版本',align:'left'},
    {title:'目标',render:r=>fmt(r.target)},{title:'固定',render:r=>r.fixed?'是':'否'},
    {title:'有效期',align:'left',render:r=>`${r.effective_from}–${r.effective_until}`}
  ])}`;
}
document.getElementById('targets').innerHTML = `
 <div class="panel"><h2>T03 地区分解：口径、结构与实际表现分开</h2>
  <ul><li>按已拟地区目标加权为 <b>${fmt(alloc.proposed_weighted_target)}</b>，距 86 差 <b class="bad">${fmt(alloc.proposed_gap_above_total)}</b>。</li><li>固定合同值硬约束下加权目标区间 [${fmt(alloc.hard_contract_arithmetic.achievable_min)},${fmt(alloc.hard_contract_arithmetic.achievable_max)}]，硬包络缺口 <b class="bad">${fmt(alloc.hard_contract_arithmetic.gap_above_max)}</b>；这是口径/承诺冲突，不能合并成合法承诺。</li>
  <li>允许 floor/cap 重谈后的权重包络上限 ${fmt(alloc.floor_cap_envelope.weighted_max)}，结构缺口 <b class="bad">${fmt(alloc.floor_cap_envelope.gap_above_max)}</b>。</li>
  <li>新结构基线 ${fmt(alloc.baseline_index)}，实际表现缺口 <b class="warn">${fmt(alloc.baseline_gap_to_total)}</b>。</li></ul>
 </div>
 <div class="targetgrid"><div class="panel targetlist">
  ${D.targets.map(t=>`<button data-id="${t.id}" class="${t.id==='T01'?'active':''}"><b>${t.id}</b> · ${esc(t.market||'all')} · ${fmt(t.target_value)}<br><span class="muted">${esc(t.status)}</span></button>`).join('')}
 </div><div class="panel" id="trace-detail">${renderTarget('T01')}</div></div>`;
document.querySelectorAll('.targetlist button').forEach(btn=>btn.onclick=()=>{
 document.querySelectorAll('.targetlist button').forEach(b=>b.classList.remove('active'));
 btn.classList.add('active'); document.getElementById('trace-detail').innerHTML=renderTarget(btn.dataset.id);
});

document.getElementById('evidence').innerHTML = `
 <div class="panel"><h2>产品线连续证据</h2>${table(D.evidence.lines.map(x=>({...x,
   full:x.full_chain_scopes.join(', '), partial:x.partial_scopes.join(', '), blocked:x.blocked_scopes.join(', '), fresh:x.new_baseline_scopes.join(', ')
 })),[
  {key:'line',title:'线',align:'left'},{title:'状态',align:'left',render:r=>statusBadge(r.status)},
  {key:'full',title:'全链',align:'left'},{key:'partial',title:'限定份额',align:'left'},
  {key:'blocked',title:'双评阻断',align:'left'},{key:'fresh',title:'新基线',align:'left'}
 ])}</div>
 <div class="panel"><h2>不可合并到同一张承诺的指标</h2><ul>
  ${D.commitment_separation.map(x=>`<li><b>[${x.kind}]</b> ${esc(x.upstream_indicator)} → ${esc(x.downstream_indicator)}：${esc(x.reason)}</li>`).join('')}
 </ul></div>
 <div class="panel"><h2>抽样可见性</h2><p class="muted">${esc(D.sampling.warning)} 跨月政策变化 ${D.sampling.policy_change_count} 组。</p></div>`;

document.querySelectorAll('nav button').forEach(btn=>btn.onclick=()=>{
 document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
 document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
 btn.classList.add('active'); document.getElementById(btn.dataset.tab).classList.add('active');
});
</script>
</body></html>
"""
    html = html.replace("{payload}", payload)
    output = Path(output)
    output.write_text(html, encoding="utf-8")
    return output
