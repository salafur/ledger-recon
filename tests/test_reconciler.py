"""对账引擎测试：双模型、召回、异常事件、DB 交叉校验."""

import pytest

from recon.db import MemoryLedger
from recon.exceptions import ReconciliationError
from recon.models import Event
from recon.reconciler import Reconciler


def ev(seq, etype, payload, ts="2026-09-30T10:00:00.000"):
    return Event(ts=ts, seq=seq, type=etype, payload=payload)


START = 1000.0


class TestCleanRound:
    def test_win_round_no_findings(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 10.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 25.0,
                                "balance": START - 10 + 25}))
        r.feed(ev(3, "ROUND_END", {"round_id": "R1",
                                   "balance": START - 10 + 25}))
        assert r.finish() == []

    def test_lose_round_no_findings(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 8.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 0.0,
                                "balance": START - 8}))
        r.feed(ev(3, "ROUND_END", {"round_id": "R1", "balance": START - 8}))
        assert r.finish() == []

    def test_recall_corrected_client_applies(self):
        """召回更正，客户端正确应用 → 无发现."""
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 10.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 20.0,
                                "balance": START - 10 + 20}))
        r.feed(ev(3, "RECALL", {"round_id": "R1", "corrected_payout": 10.0,
                                "balance": START - 10 + 10}))
        r.feed(ev(4, "ROUND_END", {"round_id": "R1", "balance": START}))
        assert r.finish() == []


class TestMismatchDetection:
    def test_client_balance_wrong(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 10.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 25.0,
                                "balance": START + 15}))
        r.feed(ev(3, "ROUND_END", {"round_id": "R1", "balance": START + 14}))
        kinds = [f.kind for f in r.finish()]
        assert "BALANCE_MISMATCH" in kinds

    def test_missed_recall_two_findings(self):
        """客户端漏收召回：余额错 + 与服务端权威漂移."""
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 10.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 20.0,
                                "balance": START + 10}))
        r.feed(ev(3, "RECALL", {"round_id": "R1", "corrected_payout": 10.0,
                                "balance": START}))
        r.feed(ev(4, "ROUND_END", {"round_id": "R1",
                                   "balance": START + 10}))  # 漏掉更正
        kinds = [f.kind for f in r.finish()]
        assert "BALANCE_MISMATCH" in kinds
        assert "CLIENT_DRIFT" in kinds

    def test_heartbeat_drift(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 10.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 5.0,
                                "balance": START - 5}))
        r.feed(ev(3, "HB", {"balance": START - 100}))
        kinds = [f.kind for f in r.finish()]
        assert "CLIENT_DRIFT" in kinds


class TestAbnormalEvents:
    def test_duplicate_bet(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 5.0}))
        r.feed(ev(2, "BET", {"round_id": "R1", "bet": 5.0}))
        assert any(f.kind == "DUPLICATE_BET" for f in r.finish())

    def test_duplicate_result(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 5.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 0.0,
                                "balance": START - 5}))
        r.feed(ev(3, "RESULT", {"round_id": "R1", "payout": 0.0,
                                "balance": START - 5}))
        assert any(f.kind == "DUPLICATE_RESULT" for f in r.finish())

    def test_unknown_round(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "RESULT", {"round_id": "RX", "payout": 1.0, "balance": 1.0}))
        assert any(f.kind == "UNKNOWN_ROUND" for f in r.finish())

    def test_out_of_order(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(5, "BET", {"round_id": "R1", "bet": 5.0}))
        r.feed(ev(3, "RESULT", {"round_id": "R1", "payout": 0.0,
                                "balance": START - 5}))
        assert any(f.kind == "OUT_OF_ORDER" for f in r.finish())

    def test_missing_round_end_on_finish(self):
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 5.0}))
        kinds = [f.kind for f in r.finish()]
        assert "MISSING_ROUND_END" in kinds


class TestModelDivergence:
    def test_incremental_vs_authoritative(self):
        """服务端余额与增量累加结果不一致 → 双模型分歧（生产 bug 模式）."""
        r = Reconciler(start_balance=START)
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 10.0}))
        # 服务端推送的余额多了一笔客户端不知道的调整
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 0.0,
                                "balance": START - 10 + 7.5}))
        kinds = [f.kind for f in r.findings]
        assert "MODEL_DIVERGENCE" in kinds


class TestStrictMode:
    def test_raises_immediately(self):
        r = Reconciler(start_balance=START, strict=True)
        with pytest.raises(ReconciliationError) as ei:
            r.feed(ev(1, "BET", {"round_id": "R1", "bet": 5.0}))
            r.feed(ev(2, "BET", {"round_id": "R1", "bet": 5.0}))
        assert ei.value.kind == "DUPLICATE_BET"


class TestDbCrossCheck:
    def _rounds(self, r):
        r.feed(ev(1, "BET", {"round_id": "R1", "bet": 10.0}))
        r.feed(ev(2, "RESULT", {"round_id": "R1", "payout": 25.0,
                                "balance": START + 15}))
        r.feed(ev(3, "ROUND_END", {"round_id": "R1", "balance": START + 15}))

    def test_db_consistent(self):
        r = Reconciler(start_balance=START)
        self._rounds(r)
        db = MemoryLedger({"R1": {"round_id": "R1", "bet": 10.0,
                                  "payout_final": 25.0,
                                  "balance_after": START + 15}})
        assert r.cross_check_db(db) == []

    def test_db_payout_diff(self):
        r = Reconciler(start_balance=START)
        self._rounds(r)
        db = MemoryLedger({"R1": {"round_id": "R1", "bet": 10.0,
                                  "payout_final": 99.0,
                                  "balance_after": START + 15}})
        findings = r.cross_check_db(db)
        assert any(f.kind == "DB_MISMATCH" and "派彩" in f.detail
                   for f in findings)

    def test_db_missing_round(self):
        r = Reconciler(start_balance=START)
        self._rounds(r)
        findings = r.cross_check_db(MemoryLedger({}))
        assert any(f.kind == "DB_MISMATCH" and "无此回合" in f.detail
                   for f in findings)
