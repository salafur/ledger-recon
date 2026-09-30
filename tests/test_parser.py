"""编解码与解析器测试."""

import pytest

from recon.codec import decode_payload, encode_payload
from recon.exceptions import LogFormatError
from recon.parser import LogParser


class TestCodec:
    def test_roundtrip_plain(self):
        payload = {"round_id": "R1", "bet": 9.5, "nested": {"a": 1}}
        token = encode_payload(payload)
        assert decode_payload(token) == payload

    def test_roundtrip_xor(self):
        payload = {"round_id": "R2", "balance": 1234.56}
        token = encode_payload(payload, xor_key=b"k3y")
        assert decode_payload(token, xor_key=b"k3y") == payload

    def test_xor_wrong_key_garbage(self):
        token = encode_payload({"a": 1}, xor_key=b"kkk")
        with pytest.raises(LogFormatError):
            decode_payload(token, xor_key=b"zzz")

    def test_corrupt_base64(self):
        with pytest.raises(LogFormatError):
            decode_payload("!!!not-base64!!!")


class TestParser:
    def setup_method(self):
        self.p = LogParser()

    def _line(self, seq, etype, payload, ts="2026-09-30T10:00:00.000"):
        return f"{ts}|{seq}|{etype}|{encode_payload(payload)}"

    def test_parse_valid(self):
        ev = self.p.parse_line(self._line(7, "BET", {"round_id": "R1", "bet": 5.0}))
        assert ev.seq == 7 and ev.type == "BET"
        assert ev.payload["round_id"] == "R1"
        assert ev.ts.startswith("2026-09-30")

    def test_too_few_fields(self):
        with pytest.raises(LogFormatError, match="字段数不足"):
            self.p.parse_line("2026|1|BET")

    def test_unknown_type(self):
        with pytest.raises(LogFormatError, match="未知事件类型"):
            self.p.parse_line("t|1|HACK|e30=")

    def test_bad_seq(self):
        with pytest.raises(LogFormatError, match="序号非整数"):
            self.p.parse_line("t|x|BET|e30=")

    def test_bad_payload(self):
        with pytest.raises(LogFormatError, match="解码失败"):
            self.p.parse_line("t|1|BET|###")

    def test_stream_skips_blank(self):
        lines = ["", self._line(1, "BET", {"round_id": "R1", "bet": 1}), "  "]
        events = list(self.p.parse_stream(iter(lines)))
        assert len(events) == 1

    def test_stream_fail_fast_by_default(self):
        lines = [self._line(1, "BET", {}), "garbage"]
        with pytest.raises(LogFormatError):
            list(self.p.parse_stream(iter(lines)))

    def test_stream_on_error_callback(self):
        good = self._line(1, "BET", {"round_id": "R1", "bet": 2})
        bad = ["garbage1", "garbage2"]
        seen = []
        events = list(self.p.parse_stream(
            iter(bad + [good]), on_error=lambda l, e: seen.append(l)))
        assert len(seen) == 2 and len(events) == 1
