# ledger-recon · 日志驱动的自动化对账测试框架

以客户端日志流为唯一输入，逐回合重建资金流（下注/派彩/召回），
用**两种独立模型**并行核算余额并检测分歧，再与数据库账本交叉比对——
任何一处对不平都会被抓出来，可注入故障复现真实生产问题。

> 本仓库是通用化开源版本，源自真实生产项目的经验抽象，不含任何公司代码与私有协议。

## 为什么是"双模型"

单一模型的对账有盲区。真实生产事故（余额失衡，每 20 小时 5~6 次）的根因是：

- 客户端用**增量累加**模型算余额：`起始余额 + Σ(事件增量)`
- 服务端用**权威推送**模型下发余额：每次结算直接下发真值

两套模型在**召回/重复结算**等边界场景下会出现分歧——只盯任何一边都对不出来。
本框架让两个模型并行记账，分歧本身就是一类 Finding：

```
日志流 (adb logcat / WebSocket 抓包落盘)
        │  FileSource / AdbSource
        ▼
    LogParser ──(Base64/XOR 解码层)──► Event 流
        ▼
    Reconciler · 双模型账本
      ├── INCREMENTAL     起始余额 + Σ(下注 -派彩 +召回更正)
      ├── AUTHORITATIVE   服务端结算推送 = 真值
      └── 检测: 模型分歧 / 余额失衡 / 客户端漂移 / 异常事件
        ▼
    cross_check_db()  ◄── LedgerDB (SQLite 参考实现 / 生产只读 SQL)
        ▼
    ReconReport（strict 模式下任何发现立即抛 ReconciliationError）
```

## 能抓什么（Finding 一览）

| Finding | 含义 | 生产对应场景 |
|---|---|---|
| `BALANCE_MISMATCH` | 回合末客户端余额 ≠ 期望余额 | 对账失衡 |
| `MODEL_DIVERGENCE` | 增量模型与服务端权威模型分歧 | 双模型不一致 bug |
| `CLIENT_DRIFT` | 客户端余额漂移于服务端真值 | 弱网丢包 |
| `MISSING_ROUND_END` | 流结束时回合未关闭 | 弱网丢事件/客户端卡死 |
| `DUPLICATE_RESULT` | 同回合重复结算推送 | 重复派彩 |
| `UNKNOWN_ROUND` | 结算指向不存在的回合 | 事件错序 |
| `OUT_OF_ORDER` | 事件序号回退 | 乱序传输 |
| `DB_MISMATCH` | 与数据库账本不一致 | 服务端数据问题 |

## 快速开始

```bash
pip install -r requirements.txt
python examples/demo.py      # 故障注入演示：3 种故障逐一被抓出
pytest tests/ -v             # 30+ 测试
```

30 秒上手：

```python
from recon import LogParser, Reconciler, FileSource, ReconReport

r = Reconciler(start_balance=1000.0)
r.feed_all(LogParser().parse_stream(FileSource("game.log").lines()))
r.finish()
print(ReconReport(r.findings, len(r.rounds)).to_table())
```

## 故障注入模拟器

测试对账框架本身需要"知道答案"的数据——模拟器生成日志流时可注入故障，
每类故障都应被特定 Finding 捕获（框架的验收标准，见 `tests/test_simulator_e2e.py`）：

```python
from recon import GameSimulator, LogParser, Reconciler

lines, ledger = GameSimulator(
    rounds=30, seed=7, faults=("missed_recall",)   # 弱网丢召回
).generate()

r = Reconciler(start_balance=1000.0)
r.feed_all(LogParser().parse_stream(iter(lines)))
r.finish()
assert any(f.kind == "BALANCE_MISMATCH" for f in r.findings)
```

## 接入真实环境

- **日志源**：实现 `lines()` 返回行迭代器即可（`AdbSource` 提供 logcat 骨架）
- **编解码**：`LogParser(xor_key=b"...")` 支持自定义对称编码层
- **数据库**：实现 `LedgerDB.get_round(round_id)` 对接生产只读 SQL 流水表
- **测试脚本**：`Reconciler(strict=True)` 让任何失衡立即抛 `ReconciliationError`，
  配合截图/日志留档做告警

## 项目结构

```
recon/
├── parser.py      # 日志行 → Event（fail-fast，坏行即异常）
├── codec.py       # Base64(/XOR) 编解码层
├── reconciler.py  # 核心：双模型账本 + 9 类 Finding 检测
├── db.py          # LedgerDB 抽象 + SQLite 参考实现
├── simulator.py   # 故障注入模拟器（框架自身的验收数据源）
├── sources.py     # FileSource(gzip) / AdbSource(logcat)
└── report.py      # ReconReport 汇总输出
tests/             # 30+ 测试：单元 + 故障注入端到端
```

## CI

Push 即自动跑全部测试（GitHub Actions，见 `.github/workflows/tests.yml`）。

## License

MIT
