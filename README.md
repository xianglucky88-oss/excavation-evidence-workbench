# 考古发掘证据

将现场关系、测年记录、陶片、浮选样本和地表踏查整理为可复核的数据成果。

数据工程子方向为**数据分析与数据应用**。基线接收已整理为 UTF-8 JSON 的合成业务数据，按数据链独立产出 JSON 事实。仓库根目录可直接运行，运行时和测试使用 Python 3.11 或以上的标准库。

## 运行

```sh
python3 -m excavation_evidence_workbench stratigraphic-order examples/stratigraphic-order.json
python3 -m excavation_evidence_workbench stratigraphic-order examples/stratigraphic-order.json --sqlite facts.sqlite
python3 -m excavation_evidence_workbench phase-dating-review examples/phase-dating-review.json
python3 -m unittest discover -s tests -v
```

Python 调用入口为 `process(pipeline, data)`。命令行也可以将 JSON 结果输出到标准输出。选用 SQLite 时，`runs` 保留原始 JSON 及 SHA-256，`facts` 保留每项具名事实和对应批次。每个输入按规范化 JSON 的摘要标识，同一处理链重复导入同一输入只保留一批；输入或处理失败时不会留下半批结果。处理逻辑改版后的重算应使用独立结果库。

## 数据链与离线样本

| 数据链 | 基线行为 | 试样 |
|---|---|---|
| `stratigraphic-order` | 从已确认的早晚方向形成地层关系与分层次序。 | `examples/stratigraphic-order.json` |
| `dating-evidence` | 从本地参考曲线候选点中保存测年值的最近参考点和残差。 | `examples/dating-evidence.json` |
| `phase-dating-review` | 按发掘阶段复核年代：合并地层先后、标本归属、测值允许范围与参考曲线历年对应关系。 | `examples/phase-dating-review.json` |
| `ceramic-counts` | 按出土单位统计口沿片和其他陶片。 | `examples/ceramic-counts.json` |
| `flotation-yield` | 计算一份已完整鉴定的浮选组分每升处理土的计数。 | `examples/flotation-yield.json` |
| `fieldwalking-density` | 用标称样带长度和宽度计算已完整踏查样带的发现密度。 | `examples/fieldwalking-density.json` |

### `phase-dating-review` 复核口径

输入按探方（`trenches`）、发掘阶段（`deposits`）、已确认地层关系（`relations`）、标本（`specimens`）、本地参考曲线（`reference_points`，按资料给定的历年次序排列）和复核记录（`reviews`）组织。关系在现场记录中均为 `confirmed`，撤回只能通过复核动作表达，使复核前状态可由数据重建。

* **回折保留**：每件标本按 `measured_bp ± tolerance_bp` 截取参考曲线，曲线点跌出允许范围再回来时分成多个历年段，落在允许范围内的各段全部保留，不合并为单一区间；允许范围内没有曲线点时标注 `no-reference-point-in-tolerance`。
* **归属处理**：`in-situ` 标本直接限定所属阶段；`reworked` 标本的硬约束归入旧层，出土阶段只保留 `residual_old_layer_constraints` 提示；`ambiguous` 标本不进硬约束，而是分别给出 `in-situ` 与 `reworked` 两种情景的历年包络、可支持的关系以及被哪些关系阻断。
* **独立探方**：以已确认关系的连通分量划分阶段组，没有相对关系的探方互不施加约束，层位各自从 0 起算。
* **冲突隔离**：现场判读成环记为 `stratigraphic-cycle`，校准结果与可核实先后关系不能同时成立记为 `chronological-inversion`，冲突事实列出受影响阶段与证据；未参与冲突的阶段仍给出 `supported` 结论。
* **复核留痕**：每条 `review-change` 对比该复核应用前后的层位、历年包络和冲突清单，只列出发生变化的阶段，并在 `evidence_basis` 中给出复核记录与被撤回关系或被改正标本的标识。

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。
