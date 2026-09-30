"""端到端演示：同一套框架抓出三类不同故障.

运行: python examples/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import (GameSimulator, LogParser, ReconReport, Reconciler,
                   SQLiteLedger)


def run(name, faults=(), db=None):
    lines, ledger = GameSimulator(rounds=30, seed=7, faults=faults).generate()
    r = Reconciler(start_balance=1000.0)
    r.feed_all(LogParser().parse_stream(iter(lines)))
    r.finish()
    if db is not None:
        for rid, row in ledger.items():
            db.insert(rid, row["bet"], row["payout_final"], row["balance_after"])
        r.cross_check_db(db)
    print(f"\n=== {name} ===")
    print(ReconReport(r.findings, len(r.rounds)).to_table())
    return r


def main():
    run("正常流：30 回合全对平")

    db = SQLiteLedger(":memory:")
    r = run("数据库交叉校验：与 SQLite 账本逐回合比对", db=db)
    db.close()

    run("故障① 弱网丢召回 → BALANCE_MISMATCH + CLIENT_DRIFT",
        faults=("missed_recall",))
    run("故障② 客户端增量漏算更正（生产 bug 复刻）",
        faults=("incremental_bug",))
    run("故障③ 服务端重复结算推送", faults=("duplicate_result",))

    print("\n每类故障都被精确捕获 —— 这就是对账框架的价值。")


if __name__ == "__main__":
    main()
