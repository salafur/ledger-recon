"""游戏模拟器：生成带故障注入的日志流 + 服务端真值账本.

用途：
  1. 给框架提供可复现的测试数据（不用依赖真实设备/生产日志）
  2. 演示"每类故障应被哪种 Finding 捕获"——框架的验收标准
  3. 弱网场景回放：丢心跳/丢召回/乱序 都是生产真实出现过的模式
"""

import random
from typing import Dict, List, Optional, Tuple

from .codec import encode_payload
from .models import (EV_BET, EV_HEARTBEAT, EV_RECALL, EV_RESULT, EV_ROUND_END)


class GameSimulator:
    """生成 N 个回合的客户端日志行 + 服务端账本行.

    faults 可选值：
      ``missed_recall``     客户端漏收 RECALL（弱网丢包）
                            → BALANCE_MISMATCH + CLIENT_DRIFT
      ``incremental_bug``   客户端增量累加漏算召回更正（生产 bug 复刻）
                            → BALANCE_MISMATCH + CLIENT_DRIFT
      ``dropped_heartbeat`` 丢一次心跳（日志流中无 HB 事件，仅观察）
      ``duplicate_result``  服务端重复推送结算 → DUPLICATE_RESULT
      ``out_of_order``      ROUND_END 先于 RESULT → OUT_OF_ORDER
    """

    def __init__(self, rounds: int = 10, start_balance: float = 1000.0,
                 seed: Optional[int] = None, faults: Tuple[str, ...] = ()):
        if rounds < 1:
            raise ValueError("至少 1 个回合")
        self.rounds = rounds
        self.start_balance = start_balance
        self.rng = random.Random(seed)
        self.faults = faults

    # ------------------------------------------------------------------
    def generate(self) -> Tuple[List[str], Dict[str, Dict]]:
        """返回 (日志行列表, 服务端账本 {round_id: row})."""
        lines: List[str] = []
        ledger: Dict[str, Dict] = {}
        seq = 0
        balance = self.start_balance  # 服务端视角余额（真值）
        client_balance = self.start_balance  # 客户端视角余额

        hb_every = max(1, self.rounds // 3)
        hb_count = 0

        for i in range(self.rounds):
            rid = f"R{i:04d}"
            bet = round(self.rng.uniform(1, 10), 2)

            # --- 下注 ---
            balance -= bet
            client_balance -= bet
            seq += 1
            lines.append(self._line(seq, EV_BET, {
                "round_id": rid, "bet": bet}))

            # --- 心跳 ---
            hb_count += 1
            if hb_count % hb_every == 0 and \
                    "dropped_heartbeat" not in self.faults:
                seq += 1
                lines.append(self._line(seq, EV_HEARTBEAT, {
                    "balance": client_balance}))

            # --- 服务端结算 ---
            win = self.rng.random() < 0.45
            payout = round(bet * self.rng.uniform(1.5, 3.0), 2) if win else 0.0
            balance += payout
            seq += 1
            result_seq = seq
            lines.append(self._line(seq, EV_RESULT, {
                "round_id": rid, "payout": payout, "balance": balance}))

            if "duplicate_result" in self.faults and i == self.rounds // 2:
                seq += 1
                lines.append(self._line(seq, EV_RESULT, {
                    "round_id": rid, "payout": payout, "balance": balance}))

            payout_final = payout
            recalled = False

            # --- 召回更正（30% 概率，模拟重复派彩冲正）---
            if win and self.rng.random() < 0.3:
                corrected = round(payout * 0.5, 2)
                balance -= (payout - corrected)
                recalled = True
                seq += 1
                lines.append(self._line(seq, EV_RECALL, {
                    "round_id": rid, "corrected_payout": corrected,
                    "balance": balance}))
                payout_final = corrected

            ledger[rid] = {
                "round_id": rid, "bet": bet,
                "payout_final": payout_final,
                "balance_after": balance,
            }

            # --- 回合关闭（客户端上报余额）---
            # 正常路径：客户端余额 = 服务端余额（权威模型一致）
            client_balance = self.start_balance + (balance - self.start_balance)
            if "incremental_bug" in self.faults and recalled:
                # 复刻生产 bug：客户端增量累加漏算召回更正
                client_balance = balance + (payout - payout_final)
            if "missed_recall" in self.faults and recalled:
                # 弱网丢了 RECALL：客户端仍按原派彩计
                client_balance = balance + (payout - payout_final)

            if "out_of_order" in self.faults and i == 0:
                # 乱序：ROUND_END 抢在 RESULT 前（seq 回拨）
                seq += 1
                lines.append(self._line(result_seq - 1, EV_ROUND_END, {
                    "round_id": rid, "balance": client_balance}))
                continue

            seq += 1
            lines.append(self._line(seq, EV_ROUND_END, {
                "round_id": rid, "balance": round(client_balance, 6)}))

        return lines, ledger

    # ------------------------------------------------------------------
    def _line(self, seq: int, etype: str, payload: dict) -> str:
        ts = f"2026-09-30T10:{seq // 60:02d}:{seq % 60:02d}.000"
        return f"{ts}|{seq}|{etype}|{encode_payload(payload)}"
