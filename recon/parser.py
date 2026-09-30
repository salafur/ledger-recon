"""日志解析器：原始日志行 -> Event 对象流.

行格式（单行一事件）::

    <ISO时间戳>|<序号>|<事件类型>|<Base64 payload>

解析器职责只有一件事：把不可信的原始文本变成结构化事件，
任何格式问题都抛 LogFormatError，绝不静默跳过——对账框架里
"看不懂的行"本身就是风险信号。
"""

from typing import Iterator, Optional

from .codec import decode_payload
from .exceptions import LogFormatError
from .models import Event, EVENT_TYPES


class LogParser:
    """支持自定义编码密钥与事件类型白名单."""

    def __init__(self, xor_key: bytes = b""):
        self.xor_key = xor_key

    def parse_line(self, line: str) -> Event:
        parts = line.rstrip("\n").split("|", 3)
        if len(parts) != 4:
            raise LogFormatError(f"字段数不足(应为4): {line[:80]!r}")
        ts, seq_s, etype, token = parts
        if not etype in EVENT_TYPES:
            raise LogFormatError(f"未知事件类型: {etype!r}")
        try:
            seq = int(seq_s)
        except ValueError as e:
            raise LogFormatError(f"序号非整数: {seq_s!r}") from e
        payload = decode_payload(token, self.xor_key)
        return Event(ts=ts, seq=seq, type=etype, payload=payload)

    def parse_stream(self, lines: Iterator[str],
                     on_error: Optional[callable] = None) -> Iterator[Event]:
        """逐行解析。on_error(line, exc) 提供时，坏行回调后继续；
        未提供则首个坏行直接抛出（fail-fast，推荐测试场景使用）。"""
        for line in lines:
            if not line.strip():
                continue
            try:
                yield self.parse_line(line)
            except LogFormatError as e:
                if on_error is None:
                    raise
                on_error(line, e)
