"""确保从任意目录运行 pytest 时都能导入项目根目录下的 recon 包。

CI 上 pytest 从 tests/ 收集用例时，项目根目录不在 sys.path 中，
会导致 ModuleNotFoundError: No module named 'recon'。
pytest 会自动把 conftest.py 所在目录加入 sys.path，因此放在根目录即可修复。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
