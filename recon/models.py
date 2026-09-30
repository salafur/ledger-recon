"""核心数据模型：事件、回合记录、对账发现."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# 事件类型常量（脱敏后的通用命名）
EV_HEARTBEAT = "HB"             # 心跳余额：客户端周期性上报当前余额
EV_BET = "BET"                  # 下注：玩家在某回合投入
EV_RESULT = "RESULT"            # 服务端结算推送（权威真值）
EV_RECALL = "RECALL"            # 结算召回/更正（如重复派彩冲正）
EV_ROUND_END = "ROUND_END"      # 回合关闭：客户端上报回合末余额

EVENT_TYPES = (EV_HEARTBEAT, EV_BET, EV_RESULT, EV_RECALL, EV_ROUND_END)


@dataclass(frozen=True)
class Event:
    """一条解析后的日志事件."""
    ts: str                 # ISO 时间戳
    seq: int                # 流内序号（用于乱序/丢事件检测）
    type: str               # EVENT_TYPES 之一
    payload: Dict[str, Any] # 解码后的业务字段


@dataclass
class RoundRecord:
    """一个回合在对账引擎中的完整画像."""
    round_id: str
    bet: float = 0.0
    payout: float = 0.0                 # 最新一次 RESULT 的派彩
    recalled: bool = False
    payout_effective: float = 0.0       # 召回更正后的有效派彩
    server_balance: Optional[float] = None   # 服务端结算推送的余额（权威）
    client_end_balance: Optional[float] = None  # ROUND_END 客户端上报余额
    closed: bool = False


@dataclass
class Finding:
    """对账引擎的一次发现（失衡/异常/可疑点）."""
    kind: str          # 发现类型，见 Finding.KINDS
    round_id: str
    detail: str

    KINDS = (
        "BALANCE_MISMATCH",     # 回合末客户端余额 != 期望余额
        "MODEL_DIVERGENCE",     # 增量累加模型与服务端权威模型分歧
        "CLIENT_DRIFT",         # 心跳余额与服务端权威余额漂移超阈
        "UNKNOWN_ROUND",        # RESULT/RECALL 指向不存在的回合
        "DUPLICATE_BET",        # 同一回合重复下注
        "DUPLICATE_RESULT",     # 同一回合重复结算
        "OUT_OF_ORDER",         # 事件顺序异常（如结算先于下注）
        "MISSING_ROUND_END",    # 流结束时回合未关闭（弱网丢事件典型症状）
        "DB_MISMATCH",          # 与数据库账本记录不一致
    )
