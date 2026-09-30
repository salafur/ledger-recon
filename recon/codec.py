"""编解码层：加密日志流的解码与编码.

真实项目里日志通常经过 Base64（可选叠加 XOR/其他对称变换）后
通过 WebSocket 流式下发；本模块抽象出这一层，方便接入自定义编码。
"""

import base64
import json
from typing import Any, Dict


def encode_payload(payload: Dict[str, Any], xor_key: bytes = b"") -> str:
    """dict -> JSON -> (可选 XOR) -> Base64 字符串."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if xor_key:
        raw = _xor(raw, xor_key)
    return base64.b64encode(raw).decode("ascii")


def decode_payload(token: str, xor_key: bytes = b"") -> Dict[str, Any]:
    """Base64 字符串 -> (可选 XOR) -> JSON -> dict. 损坏数据抛 LogFormatError."""
    from .exceptions import LogFormatError

    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
        if xor_key:
            raw = _xor(raw, xor_key)
        return json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - 编码层任何失败都归一为格式错误
        raise LogFormatError(f"payload 解码失败: {e}") from e


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
