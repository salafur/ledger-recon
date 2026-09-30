"""对账异常定义."""


class ReconciliationError(Exception):
    """对账失衡：实际值与期望值的偏差超出容忍度.

    这是框架最核心的异常。生产实践中任何一条 Finding 都不应被静默
    吞掉——测试脚本应捕获本异常并触发告警/截图/日志留档。
    """

    def __init__(self, kind: str, round_id: str, detail: str):
        self.kind = kind
        self.round_id = round_id
        self.detail = detail
        super().__init__(f"[{kind}] round={round_id}: {detail}")


class LogFormatError(Exception):
    """日志行无法解析（格式、字段缺失、payload 损坏）。"""
