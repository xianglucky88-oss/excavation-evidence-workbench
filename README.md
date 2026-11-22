# 考古发掘证据

将现场关系、测年记录、陶片、浮选样本和地表踏查整理为可复核的数据成果。

数据工程子方向为**数据分析与数据应用**。基线接收已整理为 UTF-8 JSON 的合成业务数据，按数据链独立产出 JSON 事实。仓库根目录可直接运行，运行时和测试使用 Python 3.11 或以上的标准库。

## 运行

```sh
python3 -m excavation_evidence_workbench stratigraphic-order examples/stratigraphic-order.json
python3 -m excavation_evidence_workbench stratigraphic-order examples/stratigraphic-order.json --sqlite facts.sqlite
python3 -m unittest discover -s tests -v
```

Python 调用入口为 `process(pipeline, data)`。命令行也可以将 JSON 结果输出到标准输出。选用 SQLite 时，`runs` 保留原始 JSON 及 SHA-256，`facts` 保留每项具名事实和对应批次。每个输入按规范化 JSON 的摘要标识，同一处理链重复导入同一输入只保留一批；输入或处理失败时不会留下半批结果。处理逻辑改版后的重算应使用独立结果库。

## 数据链与离线样本

| 数据链 | 基线行为 | 试样 |
|---|---|---|
| `stratigraphic-order` | 从已确认的早晚方向形成地层关系与分层次序。 | `examples/stratigraphic-order.json` |
| `dating-evidence` | 从本地参考曲线候选点中保存测年值的最近参考点和残差。 | `examples/dating-evidence.json` |
| `ceramic-counts` | 按出土单位统计口沿片和其他陶片。 | `examples/ceramic-counts.json` |
| `ceramic-reconciliation` | 清洗改号、移袋、拼合确认与复核的双时间轴盘点（原认定/最新认定与差异追溯）。 | `examples/ceramic-reconciliation.json` |
| `flotation-yield` | 计算一份已完整鉴定的浮选组分每升处理土的计数。 | `examples/flotation-yield.json` |
| `fieldwalking-density` | 用标称样带长度和宽度计算已完整踏查样带的发现密度。 | `examples/fieldwalking-density.json` |

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。

## 陶片改号与拼合的双时间轴核对

`ceramic-reconciliation` 接收六类不可变记录和一组盘点查询，所有记录同时带两个日期：

- `date`：业务生效日（valid time），登记、改号、移袋、确认、撤回、复核实际发生的日子；
- `received_date`：资料组收到记录的系统日（transaction time）。

记录类型：`registrations`（首次登记，产生稳定身份 `sherd_id`）、`renumbers`（同一物理陶片的编号别名）、`bag_moves`（只改保管位置）、`group_confirmations`（按当时登记号确认器物组）、`group_withdrawals`（撤回确认）、`reviews`（对组内某片 `hold` 留待复核，或 `release` 解除）。查询分两种口径：

- `mode: "original"`：只纳入 `received_date <= received_date`（出具当日）的资料，再按 `date` 回放，重现当时的原认定；
- `mode: "latest"`：纳入当前收到的全部资料，按 `date` 回放至盘点截止日，给出最新认定。后来补录、生效日回填的更正因此只影响新盘点，不改变已出具结论的可重现性。

口径规则：改号经编号时间线解析回同一 `sherd_id`，同一陶片的两个编号不重复入数；移袋不改变出土单位；复核 `hold` 以可核实的物理身份维持组内其他片的有效归属，冲突片进入 `in_review_sherd_ids`，组的 `effective_member_count` 相应扣减；跨出土单位的器物组在整批 `batch` 事实中只计一个，各涉及单位的 `group_evidence` 仍列本单位实物。`diff_pairs` 对两个查询出具差异事实，`causal_record_ids` 可从差异查回造成变化的登记、改号、撤回或复核记录；每个查询另附 `ledger` 事实列出该视图实际采用的全部记录及其双日期。

