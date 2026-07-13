from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ScienceCanaryCliTestCase(unittest.TestCase):
    def test_help_starts_without_runtime_errors(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/run_science_canary.py", "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Run three real, auditable Science 125 Qwen canaries.", result.stdout)


if __name__ == "__main__":
    unittest.main()
