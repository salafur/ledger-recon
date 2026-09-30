"""模拟器端到端：故障注入 → 解析 → 对账 → DB 交叉校验全链路."""

import pytest

from recon.db import SQLiteLedger
from recon.parser import LogParser
from recon.reconciler import Reconciler
from recon.simulator import GameSimulator


def run_sim(rounds=30, seed=7, faults=(), db=None):
    lines, ledger = GameSimulator(rounds=rounds, seed=seed,
                                  faults=faults).generate()
    parser = LogParser()
    r = Reconciler(start_balance=1000.0)
    r.feed_all(parser.parse_stream(iter(lines)))
    r.finish()
    if db is not None:
        for rid, row in ledger.items():
            db.insert(rid, row["bet"], row["payout_final"],
                      row["balance_after"])
        r.cross_check_db(db)
    return r


class TestCleanStream:
    def test_no_findings(self):
        r = run_sim()
        assert r.findings == [], r.findings[:5]

    def test_all_rounds_closed(self):
        r = run_sim()
        assert all(rr.closed for rr in r.rounds.values())

    def test_db_cross_check_clean(self, tmp_path):
        db = SQLiteLedger(str(tmp_path / "ledger.db"))
        r = run_sim(db=db)
        db.close()
        assert r.findings == [], r.findings[:5]

    def test_reproducible(self):
        a = run_sim(seed=99)
        b = run_sim(seed=99)
        assert [f.kind for f in a.findings] == [f.kind for f in b.findings]
        assert len(a.rounds) == len(b.rounds)


class TestFaultInjection:
    def test_missed_recall_detected(self):
        r = run_sim(faults=("missed_recall",))
        kinds = {f.kind for f in r.findings}
        assert "BALANCE_MISMATCH" in kinds, r.findings[:5]
        assert "CLIENT_DRIFT" in kinds

    def test_incremental_bug_detected(self):
        """生产 bug 复刻：客户端增量漏算召回更正必须被抓到."""
        r = run_sim(faults=("incremental_bug",))
        kinds = {f.kind for f in r.findings}
        assert "BALANCE_MISMATCH" in kinds, r.findings[:5]

    def test_duplicate_result_detected(self):
        r = run_sim(faults=("duplicate_result",))
        assert any(f.kind == "DUPLICATE_RESULT" for f in r.findings)

    def test_out_of_order_detected(self):
        r = run_sim(faults=("out_of_order",))
        assert any(f.kind == "OUT_OF_ORDER" for f in r.findings)

    def test_dropped_heartbeat_absent(self):
        lines, _ = GameSimulator(rounds=10, seed=3,
                                 faults=("dropped_heartbeat",)).generate()
        assert not any("|HB|" in l for l in lines)

    def test_fault_does_not_break_others(self):
        """故障回合之外的回合依然对平——定位能力的关键."""
        r = run_sim(faults=("missed_recall",))
        mismatch_rounds = {f.round_id for f in r.findings
                           if f.kind == "BALANCE_MISMATCH"}
        clean = [rid for rid, rr in r.rounds.items()
                 if rr.closed and rid not in mismatch_rounds]
        assert clean, "应存在大量正常回合"

    def test_report_table_render(self):
        r = run_sim(faults=("duplicate_result",))
        text = r.report().to_table()
        assert "FAIL" in text and "DUPLICATE_RESULT" in text
