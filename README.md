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
| `flotation-yield` | 计算一份已完整鉴定的浮选组分每升处理土的计数。 | `examples/flotation-yield.json` |
| `fieldwalking-density` | 用标称样带长度和宽度计算已完整踏查样带的发现密度。 | `examples/fieldwalking-density.json` |
| `fieldwalking-release-audit` | 用踏查带归属与计数、各版本公开边界和已公布数字核对分区汇总发布草稿，给出各统计项允许公开的最细区域、读者仍可推断的范围与需要暂缓的分区。 | `examples/fieldwalking-release-audit.json` |

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。
