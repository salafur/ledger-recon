"""数据库账本适配层：对账引擎与"服务端真值"的桥.

生产上对接的是只读 SQL 权限的生产流水表；开源版提供抽象接口 +
SQLite 参考实现（模拟器用它落库），接入真实 DB 只需实现 get_round。
"""

import sqlite3
from typing import Dict, Optional


class LedgerDB:
    """账本读取抽象。row: {round_id, bet, payout_final, balance_after}."""

    def get_round(self, round_id: str) -> Optional[Dict]:
        raise NotImplementedError


class MemoryLedger(LedgerDB):
    """进程内字典账本，测试用."""

    def __init__(self, rows: Dict[str, Dict]):
        self.rows = rows

    def get_round(self, round_id):
        return self.rows.get(round_id)


class SQLiteLedger(LedgerDB):
    """SQLite 参考实现（模拟模拟器/服务端流水表）."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self):
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS round_ledger (
                   round_id      TEXT PRIMARY KEY,
                   bet           REAL NOT NULL,
                   payout_final  REAL NOT NULL,
                   balance_after REAL NOT NULL)""")
        self._conn.commit()

    def insert(self, round_id: str, bet: float,
               payout_final: float, balance_after: float) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO round_ledger VALUES (?,?,?,?)",
            (round_id, bet, payout_final, balance_after))
        self._conn.commit()

    def get_round(self, round_id: str) -> Optional[Dict]:
        cur = self._conn.execute(
            "SELECT round_id, bet, payout_final, balance_after "
            "FROM round_ledger WHERE round_id = ?", (round_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def close(self):
        self._conn.close()
