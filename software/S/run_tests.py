"""Run the whole S / ComfyBatch V2.14 suite.

Usage::

    python run_tests.py            # everything
    python run_tests.py -v         # verbose

Deliberately dependency-free (stdlib ``unittest``) so the suite runs anywhere the
application itself runs.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
TESTS = ROOT / "tests"

sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    verbosity = 2 if "-v" in sys.argv else 1
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(TESTS), pattern="test_*.py", top_level_dir=str(TESTS))
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    total = result.testsRun
    skipped = len(result.skipped)
    failed = len(result.failures) + len(result.errors)
    print()
    print(f"合计 {total} 项：通过 {total - failed - skipped}，失败 {failed}，跳过 {skipped}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
