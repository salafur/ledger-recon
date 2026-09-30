"""日志源测试."""

import gzip

import pytest

from recon.codec import encode_payload
from recon.parser import LogParser
from recon.sources import FileSource


def test_file_source_roundtrip(tmp_path):
    lines = [
        f"t|1|BET|{encode_payload({'round_id': 'R1', 'bet': 1.0})}",
        "",
        f"t|2|RESULT|{encode_payload({'round_id': 'R1', 'payout': 2.0, 'balance': 1.0})}",
    ]
    p = tmp_path / "game.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    events = list(LogParser().parse_stream(FileSource(p).lines()))
    assert len(events) == 2
    assert events[0].payload["round_id"] == "R1"


def test_file_source_gzip(tmp_path):
    p = tmp_path / "game.log.gz"
    content = f"t|1|BET|{encode_payload({'round_id': 'R9', 'bet': 3.0})}\n"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(content)

    events = list(LogParser().parse_stream(FileSource(p).lines()))
    assert len(events) == 1 and events[0].payload["bet"] == 3.0


def test_adb_source_bad_path_raises():
    """AdbSource 是真机骨架：adb 不存在时应抛错而非静默."""
    from recon.sources import AdbSource
    src = AdbSource(serial="1.2.3.4:5555", adb_path="definitely-no-adb-here")
    with pytest.raises(Exception):
        list(src.lines())


def test_adb_source_builds_command():
    from recon.sources import AdbSource
    src = AdbSource(serial="192.168.3.109:5555", tag="MyLog")
    assert src._adb() == "adb"  # 命令构造即可，不真正启动 logcat
