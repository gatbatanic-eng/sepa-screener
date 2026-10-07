"""의존 방향 검사: strategies → engine 만 허용. engine/ 안의 어떤 파일도 strategies를 import하지 않는다."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestImportDirection(unittest.TestCase):
    def test_engine_never_imports_strategies(self):
        files = list((ROOT / "engine").glob("*.py"))
        self.assertGreater(len(files), 4)
        for f in files:
            bad = [m for m in _imports(f) if m.split(".")[:2] == ["golden_backtest", "strategies"] or m.split(".")[0] == "strategies"]
            self.assertEqual(bad, [], f"{f.name}이 strategies를 import한다: {bad}")

    def test_records_does_not_import_engine_or_strategies(self):
        for f in (ROOT / "records").glob("*.py"):
            bad = [m for m in _imports(f) if "strategies" in m or ".engine" in m]
            self.assertEqual(bad, [], f.name)

    def test_strategies_base_imports_intents_from_engine(self):
        self.assertIn("golden_backtest.engine.intents", _imports(ROOT / "strategies" / "base.py"))

    def test_strategy_files_do_not_compute_fills_or_costs(self):
        # 전략 파일 안에서 체결·비용 처리 금지(절대 원칙 3): fills/costs 모듈을 import하지 않는다
        for f in (ROOT / "strategies").glob("*.py"):
            bad = [m for m in _imports(f) if m.endswith(".fills") or m.endswith(".costs")]
            self.assertEqual(bad, [], f.name)


if __name__ == "__main__":
    unittest.main()
