"""日志源适配：文件 / adb 设备.

生产上日志从 Android 设备 logcat（经 adb）或 WebSocket 抓包落盘；
开源版提供 FileSource 参考实现与 AdbSource 骨架（需真机环境）。
"""

import subprocess
from pathlib import Path
from typing import Iterator, Optional


class FileSource:
    """从本地日志文件逐行读取（含 gzip 透明解压）."""

    def __init__(self, path):
        self.path = Path(path)

    def lines(self) -> Iterator[str]:
        opener = __import__("gzip").open if self.path.suffix == ".gz" else open
        with opener(self.path, "rt", encoding="utf-8", errors="replace") as f:
            yield from f


class AdbSource:
    """adb logcat 实时流（骨架，需连接真机）.

    用法::

        src = AdbSource(serial="192.168.3.109:5555", tag="GameLog")
        for line in src.lines():
            ...
    """

    def __init__(self, serial: Optional[str] = None, tag: str = "GameLog",
                 adb_path: str = "adb"):
        self.serial = serial
        self.tag = tag
        self.adb_path = adb_path

    def _adb(self) -> str:
        return self.adb_path

    def lines(self) -> Iterator[str]:
        cmd = [self._adb()]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["logcat", "-s", f"{self.tag}:V"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        try:
            for line in proc.stdout:
                # logcat 前缀 "09-30 10:00:00.000 I/GameLog: <payload>"
                _, _, _, msg = line.split(": ", 3) if line.count(": ") >= 3 \
                    else ("", "", "", line)
                yield msg.rstrip("\n")
        finally:
            proc.terminate()
