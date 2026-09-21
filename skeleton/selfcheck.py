#!/usr/bin/env python3
"""自检脚本：列出还没实现的 TODO，然后跑一遍测试。

    python selfcheck.py

退出码 0 表示测试全绿（TODO 也应当全部实现）。
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from typing import List, Tuple

ROOT = pathlib.Path(__file__).resolve().parent


def pending_functions() -> List[Tuple[str, str, int]]:
    """用 AST 找出「函数体第一句就是 raise NotImplementedError」的函数。"""
    found: List[Tuple[str, str, int]] = []
    for path in sorted((ROOT / "gateway").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for statement in node.body:
                if isinstance(statement, ast.Raise) and isinstance(statement.exc, ast.Call):
                    name = getattr(statement.exc.func, "id", "")
                    if name == "NotImplementedError":
                        found.append((path.name, node.name, node.lineno))
                        break
    return found


def main() -> int:
    pending = pending_functions()
    if pending:
        print("还有 %d 个函数没实现：" % len(pending))
        for filename, func, line in pending:
            print("  - %-16s 第 %3d 行  %s()" % (filename, line, func))
    else:
        print("所有 TODO 都实现完了")

    print("\n开始跑测试 ...\n")
    sys.stdout.flush()
    code = subprocess.call([sys.executable, "-m", "pytest", "tests", "-q"], cwd=str(ROOT))
    print("\n" + ("测试全绿，恭喜！" if code == 0 else "测试还有失败，回到上面的 TODO 列表继续。"))
    return code


if __name__ == "__main__":
    sys.exit(main())
