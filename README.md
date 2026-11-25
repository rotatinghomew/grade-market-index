# 供货质量指数资料交换

`data/` 收录供演练使用的合成记录，不含真实企业或人员资料。资料使用 UTF-8 逗号分隔，首行为字段名，带逗号的单值用双引号括起。日期采用 ISO 表示，空字段表示来源未填或不适用，不代表数值零。`sources.json` 记录各表列名、身份键和可直接核对的引用。

## 业务资料

- `counts.csv`：已公布业务分组原账，抽样覆盖由 eligible/inspected 表示；分级计数与未定级同口径，不计同一抽检对象两次。
- `mapping.csv`：规格关系与老篮子中可指认的份额。若各候选份额存在重叠或总和超出独占份额，不将未证实部分强拆。
- `paired.csv`：同件双评的交叉计数，eligible 数跨评分格共享。
- `baskets.csv`：参照篮子各单元固定权重，支持原约定与候选并存。
- `targets.csv`：分地区发布的固定目标与整体候选；生效季度重叠时不能悄悄替换原指标。

例行抽检与定向抽检不作为同一概率样本；现有计数不提供定向抽检的纠偏权重。当前经营读数允许列观测指数及覆盖边界，合同目标的衔接按篮子所列最小双评覆盖。unknown 不按任意等级赋分；覆盖不足和规格不重合要分别保留。地区目标在同一候选定义下按业务篮子权重汇总，已有旧定义指标另列。

## 本地核对

需要 Python 3.11。分析环境可使用 `python3 -m pip install -r requirements.txt` 安装锁定的数值依赖。

从仓库根目录执行 `python3 check_sources.py`，返回各表行数；其他同格式资料可用 `python3 check_sources.py 路径` 检查。脚本只读资料并报告结构或引用错误，不给出业务结论。执行 `python3 -m unittest discover -s tests -v` 检查资料约定及拒绝重复身份的行为。

## 谈判核算入口

```bash
# 完整报告和机器可读 JSON
python3 analyze.py report --quarter 2026-Q2 \
  --out NEGOTIATION_REPORT.md --json-out report.json

# 可点击切换“当前读数 / 同篮子 / 构成 / 桥 / 目标回溯”的离线页面
python3 analyze.py dashboard --quarter 2026-Q2 --out dashboard.html

# 三个可切换视图
python3 analyze.py current  --quarter 2026-Q2 --json
python3 analyze.py basket candidate --quarter 2026-Q2
python3 analyze.py compare candidate --from-months 2026-04 --to-months 2026-05
python3 analyze.py operating --old-months 2025-04,2025-05 --new-months 2026-04,2026-05
python3 analyze.py composition candidate --quarter 2026-Q2
python3 analyze.py sampling --quarter 2026-Q2

# 跨规格/双评/参照重合，以及地区目标回溯
python3 analyze.py bridge --quarter 2026-Q2 --json
python3 analyze.py overlap --json
python3 analyze.py targets --quarter 2026-Q2
python3 analyze.py trace T01 --quarter 2026-Q2 --json
python3 analyze.py trace --market new --quarter 2026-Q2
```

也可在 Python 中使用：

```python
from quality_index import Project

p = Project.load("data")
p.full_report("2026-Q2")
p.evaluate_target("T04", quarter="2026-Q2")
p.reference_overlap("published", "candidate")
```

### 核算口径

- 规格得分只使用高/中/低件数：`(high×高分 + middle×中分 + low×低分)/(high+middle+low)`；`unknown` 不按任何等级赋分，只报告原因、unknown 率和“全部 unknown 零收益”的可见性边界。
- 地区指数按该地区篮子权重归一化；缺规格或月份时保留缺失权重，输出的 `partial_normalized_index_not_official` 只用于观察，不是合同指数。
- 规格拆分按 `share_min` 建立保守、非重叠老件台账；未证实份额保留为 `mapping_residual_weight`，不把同一批老件同时借给 A1、A2。
- 双评交叉数的 `eligible` 是跨评分格共享值，不逐格相加；双评数低于篮子 `min_pairs` 时不做等级边界转换。
- 新市场/新规格计入当前 candidate 读数和实际经营指数，但在 published 同篮承诺中列为 scope/参照不重合，不为了同比而删除。
- 固定 published 目标与 candidate 试拟目标分别解释。季度区间按含端点处理；例如 2026-Q3 同时存在 T01/T02 与 T03/T04 时，原承诺不被候选目标替换。
- 新增月份或第二套篮子（例如 `edition=candidate2`）写入同结构 CSV 后，所有命令按参数重新核算，不需要改月份常量。

