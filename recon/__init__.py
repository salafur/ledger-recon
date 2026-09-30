"""ledger-recon: 日志驱动的自动化对账测试框架.

核心思想：以客户端产生的加密日志流为唯一输入，重建每一回合的
资金流（下注/派彩/召回），用两种独立模型核算余额——

  1. INCREMENTAL    增量累加模型：起始余额 + Σ(事件增量)
  2. AUTHORITATIVE  服务端权威模型：以服务端结算推送为真值

两模型的任何分歧、客户端上报余额与服务端的漂移、以及与数据库
账本记录的交叉比对，都会被捕获为 Finding（或直接抛出
ReconciliationError）。

本仓库为通用化开源版本，源自真实生产项目经验的抽象，不含任何
公司业务代码与私有协议细节。
"""

from .exceptions import ReconciliationError
from .models import Event, RoundRecord, Finding
from .codec import decode_payload, encode_payload
from .parser import LogParser
from .reconciler import Reconciler, Model
from .db import LedgerDB, SQLiteLedger
from .report import ReconReport
from .simulator import GameSimulator

__version__ = "1.0.0"

__all__ = [
    "ReconciliationError", "Event", "RoundRecord", "Finding",
    "decode_payload", "encode_payload", "LogParser", "Reconciler",
    "Model", "LedgerDB", "SQLiteLedger", "ReconReport",
    "GameSimulator",
]
