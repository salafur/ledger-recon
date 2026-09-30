"""对账引擎：消费事件流，维护双模型账本，产出 Finding.

设计动机（生产 bug 复盘）：客户端曾用"增量累加"模型算余额，
服务端用"权威推送"模型下发余额。两套模型在召回/重复结算等
边界场景下会出现分歧——单看任何一个模型都对不出来，必须
双模型并行核算并检测分歧点。
"""

from typing import Dict, List, Optional

from .exceptions import ReconciliationError
from .models import (Event, Finding, RoundRecord, EV_BET, EV_HEARTBEAT,
                     EV_RECALL, EV_RESULT, EV_ROUND_END)


class Model:
    """余额核算模型标识."""
    INCREMENTAL = "INCREMENTAL"      # 起始余额 + Σ事件增量
    AUTHORITATIVE = "AUTHORITATIVE"  # 服务端结算推送为真值


class Reconciler:
    """流式对账引擎。feed() 一条条喂事件，finish() 收尾。"""

    def __init__(self, start_balance: float = 0.0,
                 tol: float = 1e-6, drift_threshold: float = 0.0,
                 strict: bool = False):
        """
        :param tol: 金额比较容忍度（浮点）
        :param drift_threshold: 心跳余额漂移告警阈值
        :param strict: True 时任何 Finding 立即抛 ReconciliationError
        """
        self.start_balance = float(start_balance)
        self.tol = tol
        self.drift_threshold = drift_threshold
        self.strict = strict

        self.rounds: Dict[str, RoundRecord] = {}
        self.findings: List[Finding] = []

        self._seq_seen: List[int] = []
        self._incremental = float(start_balance)  # 增量模型账本
        self._server_balance: Optional[float] = None  # 权威模型账本
        self._client_last_balance: Optional[float] = None

    # ------------------------------------------------------------------
    # 事件消费
    # ------------------------------------------------------------------
    def feed(self, ev: Event) -> List[Finding]:
        """处理一条事件，返回本次新产生的 Finding 列表."""
        self._check_order(ev)
        handler = {
            EV_BET: self._on_bet,
            EV_RESULT: self._on_result,
            EV_RECALL: self._on_recall,
            EV_ROUND_END: self._on_round_end,
            EV_HEARTBEAT: self._on_heartbeat,
        }[ev.type]
        before = len(self.findings)
        handler(ev)
        new = self.findings[before:]
        if self.strict:
            for f in new:
                raise ReconciliationError(f.kind, f.round_id, f.detail)
        return new

    def feed_all(self, events) -> List[Finding]:
        out: List[Finding] = []
        for ev in events:
            out.extend(self.feed(ev))
        return out

    def finish(self) -> List[Finding]:
        """流结束：检查未关闭回合。"""
        for r in self.rounds.values():
            if not r.closed:
                self._add(Finding("MISSING_ROUND_END", r.round_id,
                                  f"回合未关闭（bet={r.bet}）: 弱网丢事件或客户端卡死"))
        return self.findings

    # ------------------------------------------------------------------
    # 各事件处理器
    # ------------------------------------------------------------------
    def _on_bet(self, ev: Event) -> None:
        rid = ev.payload["round_id"]
        if rid in self.rounds:
            self._add(Finding("DUPLICATE_BET", rid,
                              f"重复下注 {ev.payload['bet']}"))
            return
        r = RoundRecord(round_id=rid, bet=float(ev.payload["bet"]))
        self.rounds[rid] = r
        self._incremental -= r.bet  # 增量模型：下注扣减

    def _on_result(self, ev: Event) -> None:
        rid = ev.payload["round_id"]
        r = self.rounds.get(rid)
        if r is None:
            self._add(Finding("UNKNOWN_ROUND", rid, "结算推送先于下注"))
            self._server_balance = float(ev.payload.get("balance", 0))
            return
        if r.server_balance is not None:
            self._add(Finding("DUPLICATE_RESULT", rid,
                              f"回合已有结算（payout={r.payout}），收到重复推送 "
                              f"payout={ev.payload['payout']}"))
        r.payout = float(ev.payload["payout"])
        r.server_balance = float(ev.payload.get("balance", r.server_balance or 0))
        self._server_balance = r.server_balance
        if not r.recalled:
            r.payout_effective = r.payout
        self._incremental = (self.start_balance
                             + sum(self._deltas(r2) for r2 in self.rounds.values()))
        self._check_divergence(r)

    def _on_recall(self, ev: Event) -> None:
        """召回/更正：payload 带 corrected_payout（更正后的最终派彩）."""
        rid = ev.payload["round_id"]
        r = self.rounds.get(rid)
        if r is None:
            self._add(Finding("UNKNOWN_ROUND", rid, "召回指向不存在的回合"))
            return
        r.recalled = True
        r.payout_effective = float(ev.payload["corrected_payout"])
        r.server_balance = float(ev.payload.get("balance", r.server_balance or 0))
        self._server_balance = r.server_balance
        self._incremental = (self.start_balance
                             + sum(self._deltas(r2) for r2 in self.rounds.values()))
        self._check_divergence(r)

    def _on_round_end(self, ev: Event) -> None:
        rid = ev.payload["round_id"]
        r = self.rounds.get(rid)
        if r is None:
            self._add(Finding("UNKNOWN_ROUND", rid, "回合关闭先于下注"))
            return
        r.client_end_balance = float(ev.payload["balance"])
        r.closed = True
        expected_inc = self.start_balance + sum(
            self._deltas(r2) for r2 in self.rounds.values())
        # 客户端上报余额 vs 增量模型期望
        if abs(r.client_end_balance - expected_inc) > self.tol:
            self._add(Finding(
                "BALANCE_MISMATCH", rid,
                f"客户端回合末余额 {r.client_end_balance} != 增量模型期望 "
                f"{expected_inc:.6f} (bet={r.bet}, payout_eff={r.payout_effective})"))
        # 客户端上报余额 vs 服务端权威余额
        if r.server_balance is not None and \
                abs(r.client_end_balance - r.server_balance) > self.tol:
            self._add(Finding(
                "CLIENT_DRIFT", rid,
                f"客户端 {r.client_end_balance} 漂移于服务端权威余额 "
                f"{r.server_balance}，差值 {r.client_end_balance - r.server_balance:+.6f}"))

    def _on_heartbeat(self, ev: Event) -> None:
        """心跳余额与增量模型期望比对.

        心跳发生在回合中间（下注已扣、结算未到），与"上一次服务端
        推送"比对必然有差；正确的锚点是增量模型的实时期望。
        """
        self._client_last_balance = float(ev.payload["balance"])
        expected = self.start_balance + sum(
            self._deltas(r2) for r2 in self.rounds.values())
        if abs(self._client_last_balance - expected) > \
                max(self.drift_threshold, self.tol):
            self._add(Finding(
                "CLIENT_DRIFT", "-",
                f"心跳余额 {self._client_last_balance} 与增量期望 "
                f"{expected:.6f} 漂移 "
                f"{self._client_last_balance - expected:+.6f}"))

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _deltas(self, r: RoundRecord) -> float:
        """一个回合对增量模型的净贡献（相对起始余额）。"""
        return -r.bet + r.payout_effective

    def _check_divergence(self, r: RoundRecord) -> None:
        """双模型分歧检测——框架的灵魂。"""
        if r.server_balance is None:
            return
        expected_inc = self.start_balance + sum(
            self._deltas(r2) for r2 in self.rounds.values())
        if abs(expected_inc - r.server_balance) > self.tol:
            self._add(Finding(
                "MODEL_DIVERGENCE", r.round_id,
                f"增量模型 {expected_inc:.6f} != 服务端权威 {r.server_balance}"
                f"（差 {expected_inc - r.server_balance:+.6f}）——"
                f"两套余额模型不一致，典型于召回/重复结算场景"))

    def _check_order(self, ev: Event) -> None:
        if self._seq_seen and ev.seq < self._seq_seen[-1]:
            self._add(Finding("OUT_OF_ORDER", ev.payload.get("round_id", "-"),
                              f"seq {ev.seq} 小于前值 {self._seq_seen[-1]}"))
        self._seq_seen.append(ev.seq)

    def _add(self, f: Finding) -> None:
        self.findings.append(f)

    def report(self) -> "ReconReport":
        """生成对账报告."""
        from .report import ReconReport
        return ReconReport(list(self.findings), len(self.rounds))

    # ------------------------------------------------------------------
    # DB 交叉校验
    # ------------------------------------------------------------------
    def cross_check_db(self, db) -> List[Finding]:
        """已关闭回合与服务端数据库账本逐条比对.

        :param db: LedgerDB 实现
        """
        out: List[Finding] = []
        for r in self.rounds.values():
            if not r.closed:
                continue
            row = db.get_round(r.round_id)
            if row is None:
                out.append(Finding("DB_MISMATCH", r.round_id, "数据库中无此回合"))
                continue
            if abs(row["payout_final"] - r.payout_effective) > self.tol:
                out.append(Finding(
                    "DB_MISMATCH", r.round_id,
                    f"有效派彩 {r.payout_effective} != DB {row['payout_final']}"))
            if r.server_balance is not None and \
                    abs(row["balance_after"] - r.server_balance) > self.tol:
                out.append(Finding(
                    "DB_MISMATCH", r.round_id,
                    f"结算余额 {r.server_balance} != DB {row['balance_after']}"))
        self.findings.extend(out)
        return out
