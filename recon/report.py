"""对账报告：汇总 Finding，输出人可读结果."""

from typing import List

from .models import Finding


class ReconReport:
    """一次对账会话的报告."""

    def __init__(self, findings: List[Finding], total_rounds: int = 0):
        self.findings = findings
        self.total_rounds = total_rounds

    @property
    def ok(self) -> bool:
        return not self.findings

    def by_kind(self) -> dict:
        out = {}
        for f in self.findings:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out

    def to_table(self) -> str:
        if self.ok:
            return (f"OK — {self.total_rounds} 个回合全部对平，"
                    f"双模型一致，无异常事件")
        lines = [f"FAIL — {len(self.findings)} 条发现 / {self.total_rounds} 回合"]
        counts = self.by_kind()
        lines.append("  分布: " + ", ".join(f"{k}x{n}" for k, n in counts.items()))
        for f in self.findings[:20]:
            lines.append(f"  [{f.kind}] {f.round_id}: {f.detail}")
        if len(self.findings) > 20:
            lines.append(f"  ... 其余 {len(self.findings) - 20} 条省略")
        return "\n".join(lines)

    def assert_ok(self):
        """测试断言辅助：有不平衡直接抛 AssertionError."""
        if not self.ok:
            raise AssertionError(self.to_table())
