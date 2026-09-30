# ledger-recon · 产品文档说明报告

> 项目名称：ledger-recon — 日志驱动的自动化对账测试框架
> 文档版本：v1.0（基于 2026-09-29 源码逐行梳理）
> 项目路径：`C:\Users\Administrator\WorkBuddy\2026-09-29-19-58-12\ledger-recon`
> 许可证：MIT（开源通用化版本，脱敏自真实生产经验，不含公司代码与私有协议）

---

## 1. 项目定位：一句话说清它是干嘛的

**以客户端日志流为唯一输入，逐回合重建资金流（下注 / 派彩 / 召回），用两套独立模型并行核算余额并检测分歧，再与数据库账本交叉比对——任何一处对不平都会被抓出来。**

它的直接业务对应物：你在公司主导的自研对账框架（adb + 日志解析 + SQL 校验，覆盖 12 款拉霸游戏三端）。ledger-recon 是把那套生产经验**脱敏、通用化、开源化**后的版本——日志格式、事件名（ResultCall/ResultRecall → RESULT/RECALL）、密钥全部换成了抽象接口，但**核心思想（双模型对账）原封不动保留**。

## 2. 它解决什么问题（背景与动机）

### 2.1 生产事故复盘

真实生产环境存在一个余额失衡 bug（每 20 小时出现 5~6 次），根因是：

| 模型 | 谁在用 | 算法 |
|------|--------|------|
| **增量累加**（INCREMENTAL） | 客户端 | `起始余额 + Σ(各回合：-下注 + 有效派彩)` |
| **权威推送**（AUTHORITATIVE） | 服务端 | 每次结算直接下发余额真值 |

两套模型在**召回 / 重复结算**等边界场景会分歧：
- 客户端弱网丢了一个 RECALL 事件 → 还按原派彩累加 → 余额比真值高
- 客户端增量逻辑漏算召回更正 → 同样的漂移
- 服务端重复推送结算 → 增量模型记两次，权威模型记一次

**单看任何一个模型都对不出来**——客户端觉得自己没毛病，服务端也觉得自己没毛病。只有两边并行记账、互相对比，分歧点才能暴露。

### 2.2 本框架的解法

让两个模型在引擎内部并行记账，**分歧本身就是一类 Finding**（`MODEL_DIVERGENCE`），再叠加与数据库的第三路交叉校验。这就是"双模型对账"的全部意义。

## 3. 整体架构与数据流

```
日志流 (adb logcat / WebSocket 抓包落盘 / 文件)
        │  FileSource / AdbSource
        ▼
    LogParser ──(Base64 / XOR 解码层)──► Event 流（结构化事件）
        ▼
    Reconciler · 双模型账本
      ├── INCREMENTAL     起始余额 + Σ(下注 - 派彩 + 召回更正)
      ├── AUTHORITATIVE   服务端结算推送 = 真值
      └── 检测: 模型分歧 / 余额失衡 / 客户端漂移 / 异常事件
        ▼
    cross_check_db()  ◄── LedgerDB（SQLite 参考实现 / 生产只读 SQL）
        ▼
    ReconReport（strict 模式下任何发现立即抛 ReconciliationError）
```

### 三种日志源

| 源 | 类 | 说明 |
|----|----|----|
| 本地文件 | `FileSource` | 逐行读 `.log`，`.gz` 透明解压 |
| 真机 | `AdbSource` | `adb -s <serial> logcat -s GameLog:V` 实时流，解析 logcat 前缀 |
| WebSocket 落盘 | 生产实际用法 | 抓包落盘成文件后再喂 FileSource |

## 4. 模块拆解（逐文件）

### 4.1 `recon/models.py` — 数据模型

三个 dataclass + 常量，是全框架的"语言"：

- **`Event`**：一条解析后的日志事件。字段：`ts`（ISO 时间戳）、`seq`（流内序号，乱序检测用）、`type`（5 种之一）、`payload`（解码后的业务字段 dict）。`frozen=True` 不可变。
- **5 种事件类型**（脱敏命名）：`HB` 心跳余额 / `BET` 下注 / `RESULT` 服务端结算推送 / `RECALL` 结算召回更正 / `ROUND_END` 回合关闭。
- **`RoundRecord`**：一个回合的完整画像。关键字段：`bet`、`payout`（最新 RESULT 的派彩）、`recalled`（是否被召回）、`payout_effective`（召回更正后的有效派彩）、`server_balance`（权威余额）、`client_end_balance`（客户端上报余额）、`closed`。
- **`Finding`**：一次对账发现（失衡/异常/可疑点）。`kind` + `round_id` + `detail`。
- **9 类 Finding**（详见第 5 节表格）。

### 4.2 `recon/codec.py` — 编解码层

- `encode_payload`：dict → JSON →（可选 XOR）→ Base64
- `decode_payload`：反向。**任何解码失败统一归一为 `LogFormatError`**
- `_xor`：逐字节循环密钥异或——生产上日志是 Base64 加密经 WebSocket 传输，XOR 层留作自定义对称编码的扩展位

### 4.3 `recon/parser.py` — 日志解析器

行格式：`<ISO时间戳>|<序号>|<事件类型>|<Base64 payload>`（单行一事件，`split("|", 3)` 保证 payload 内可以有 `|`）。

**核心设计：fail-fast**。解析器职责只有一个：把不可信原始文本变成结构化事件。字段数不足、事件类型未知、序号非整数、解码失败——全部抛 `LogFormatError`，**绝不静默跳过**。设计原话："对账框架里'看不懂的行'本身就是风险信号。"

`parse_stream` 提供两种模式：默认坏行即抛（测试场景推荐）；传 `on_error` 回调则记录后继续（生产长跑场景）。

### 4.4 `recon/reconciler.py` — 对账引擎（核心中的核心）

`Reconciler` 是一个**流式状态机**：`feed()` 一条条喂事件，`finish()` 收尾检查。

初始化参数：
- `start_balance`：起始余额
- `tol = 1e-6`：金额比较容忍度（浮点数不能直接 ==）
- `drift_threshold`：心跳余额漂移告警阈值
- `strict`：True 时任何 Finding **立即抛 `ReconciliationError`**（用于测试脚本即时熔断，配合截图/日志留档做告警）

事件分发用**字典映射**（不是 if-elif 链）：

```python
handler = {
    EV_BET: self._on_bet,
    EV_RESULT: self._on_result,
    EV_RECALL: self._on_recall,
    EV_ROUND_END: self._on_round_end,
    EV_HEARTBEAT: self._on_heartbeat,
}[ev.type]
```

**各处理器的关键逻辑：**

- `_on_bet`：新回合建 `RoundRecord`；重复下注 → `DUPLICATE_BET`；增量账本 `-bet`
- `_on_result`：回合不存在 → `UNKNOWN_ROUND`（结算先于下注）；已有结算再收一次 → `DUPLICATE_RESULT`；重算增量期望；调 `_check_divergence`
- `_on_recall`：用 `corrected_payout` 覆盖 `payout_effective`；同样触发分歧检测
- `_on_round_end`：关闭回合，做**两路比对**——客户端上报余额 vs 增量模型期望（超差 → `BALANCE_MISMATCH`）、客户端上报余额 vs 服务端权威余额（超差 → `CLIENT_DRIFT`）
- `_on_heartbeat`：心跳发生在回合中间（下注已扣、结算未到），**正确的比对锚点是增量模型实时期望**，不是上一次服务端推送——这个注释里藏着一个很容易踩的坑
- `_check_divergence`：**框架的灵魂**。增量模型期望 vs 服务端权威余额，超差 → `MODEL_DIVERGENCE`
- `_check_order`：seq 回退 → `OUT_OF_ORDER`
- `finish()`：流结束仍有未关闭回合 → `MISSING_ROUND_END`（弱网丢事件 / 客户端卡死的典型症状）
- `cross_check_db(db)`：**第三路校验**——已关闭回合逐条与数据库账本比 `payout_final` 和 `balance_after`，不符 → `DB_MISMATCH`

### 4.5 `recon/db.py` — 数据库账本适配层

- `LedgerDB`：抽象接口，只要实现 `get_round(round_id)`，返回 `{round_id, bet, payout_final, balance_after}`——生产上对接你的只读 SQL 流水表权限，就实现这一个方法
- `MemoryLedger`：进程内字典账本，测试用
- `SQLiteLedger`：SQLite 参考实现（`round_ledger` 表），模拟器用它落库

### 4.6 `recon/simulator.py` — 故障注入模拟器（框架的验收数据源）

测试对账框架本身需要"知道答案"的数据。`GameSimulator` 生成 N 个回合的日志流 + 服务端真值账本，可注入 5 种故障，**每种故障应被特定 Finding 捕获**：

| 故障注入 | 模拟什么 | 应被捕获为 |
|---------|---------|-----------|
| `missed_recall` | 弱网丢 RECALL，客户端按原派彩计 | `BALANCE_MISMATCH` + `CLIENT_DRIFT` |
| `incremental_bug` | 客户端增量漏算召回更正（**生产 bug 复刻**） | 同上 |
| `dropped_heartbeat` | 丢一次心跳 | 观察用 |
| `duplicate_result` | 服务端重复推送结算 | `DUPLICATE_RESULT` |
| `out_of_order` | ROUND_END 抢在 RESULT 前 | `OUT_OF_ORDER` |

模拟器内部同时维护"服务端视角余额（真值）"和"客户端视角余额"，故障注入的本质就是让这两个视角**故意分家**。`seed` 参数保证可复现。

### 4.7 `recon/report.py` — 对账报告

`ReconReport`：汇总 Finding → `to_table()` 人可读输出（OK 显示"N 个回合全部对平"，FAIL 显示数量分布 + 前 20 条明细）；`assert_ok()` 是 pytest 断言辅助。

### 4.8 `recon/exceptions.py`

`ReconciliationError`（strict 模式抛出）与 `LogFormatError`（解析失败抛出）——就是你在面试里讲的自定义异常体系。

## 5. Finding 类型全表（框架的"产出物清单"）

| Finding | 含义 | 生产对应场景 |
|---|---|---|
| `BALANCE_MISMATCH` | 回合末客户端余额 ≠ 增量期望 | 对账失衡 |
| `MODEL_DIVERGENCE` | 增量模型与权威模型分歧 | 双模型不一致 bug（核心） |
| `CLIENT_DRIFT` | 客户端余额漂移于权威真值 | 弱网丢包 |
| `MISSING_ROUND_END` | 流结束时回合未关闭 | 弱网丢事件/客户端卡死 |
| `DUPLICATE_RESULT` | 同回合重复结算推送 | 重复派彩 |
| `DUPLICATE_BET` | 同回合重复下注 | 双击/重试未幂等 |
| `UNKNOWN_ROUND` | 结算/召回指向不存在的回合 | 事件错序 |
| `OUT_OF_ORDER` | 事件序号回退 | 乱序传输 |
| `DB_MISMATCH` | 与数据库账本不一致 | 服务端数据问题 |

## 6. 使用方式

```python
from recon import LogParser, Reconciler, FileSource, ReconReport

r = Reconciler(start_balance=1000.0)
r.feed_all(LogParser().parse_stream(FileSource("game.log").lines()))
r.finish()
print(ReconReport(r.findings, len(r.rounds)).to_table())
```

接入真实生产环境的四个接入点（每个都是"实现一个方法/传一个参数"级别）：

1. **日志源**：实现 `lines()` 返回行迭代器（`AdbSource` 已提供 logcat 骨架）
2. **编解码**：`LogParser(xor_key=b"...")` 传自定义密钥
3. **数据库**：实现 `LedgerDB.get_round(round_id)` 对接生产只读 SQL 流水表
4. **测试脚本**：`Reconciler(strict=True)` + 截图/日志留档 = 自动化告警闭环

## 7. 测试与 CI

- `tests/` 下 4 个测试文件、30+ 用例：解析器单元测试、对账引擎单元测试、日志源测试，以及 `test_simulator_e2e.py`——**故障注入端到端**：每种注入的故障必须被对应的 Finding 捕获，这是框架的验收标准
- CI：GitHub Actions push 即跑全部测试（`.github/workflows/tests.yml`）
- 依赖极少：`pytest>=8.0`（运行时零三方依赖，只用标准库）

## 8. 关键设计决策回顾（面试可讲的"为什么"）

1. **为什么双模型？** 单模型有盲区——召回/重复结算场景下两边各自"自洽"，只有互相对比才能暴露分歧
2. **为什么解析 fail-fast？** 坏行被静默跳过会让"丢事件"这类故障隐形，对账框架必须宁可误报不可漏报
3. **为什么心跳比对锚点是增量期望而不是权威余额？** 心跳在回合中间，下注已扣结算未到，与权威余额必然有差——锚错了会产生海量误报
4. **为什么 tol = 1e-6？** 浮点数不能用 == 比较金额
5. **为什么模拟器？** 测"测数据的工具"自己需要已知答案的数据，且弱网/乱序场景生产难以按需复现

## 9. 局限与扩展方向

- `AdbSource` 是骨架：实际生产是 Base64 加密的 WebSocket 日志流，需自行实现 WebSocketSource
- 单线程流式处理：12 款游戏并行跑需要外层开多进程/多实例
- 报告目前是文本表格：可扩展输出 JSON/HTML，接告警平台（企业微信/邮件）

## 10. 与你的简历/面试的对应关系

- 简历条目"自研日志驱动 Python 自动化对账框架（adb + 日志解析 + SQL 校验）"= 本框架的生产版
- 面试讲"工具与框架的区别"：ledger-recon 就是框架形态的样板——有抽象接口（LedgerDB/Source）、可插拔（codec/xor_key）、有验收体系（simulator + Finding 映射）、有 CI
- 面试被问"怎么发现余额失衡的"：`MODEL_DIVERGENCE` 一节就是标准答案
