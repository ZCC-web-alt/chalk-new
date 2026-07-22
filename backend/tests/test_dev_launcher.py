from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]


class DevLauncherTestCase(unittest.TestCase):
    def test_windows_launcher_uses_stable_pinned_frontend_tooling(self) -> None:
        package = json.loads((PROJECT_DIR / "frontend" / "package.json").read_text(encoding="utf-8"))
        launcher = (PROJECT_DIR / "scripts" / "dev.ps1").read_text(encoding="utf-8")

        self.assertEqual(package["packageManager"], "pnpm@11.11.0")
        self.assertEqual(package["scripts"]["dev"], "next dev --webpack")
        self.assertIn("corepack pnpm --version", launcher)
        self.assertIn("Expected pnpm", launcher)
        self.assertIn('$ErrorActionPreference = "Continue"', launcher)
        self.assertLess(launcher.index("Push-Location $Frontend"), launcher.index("corepack pnpm --version"))
        self.assertIn("https://github.com/vercel/next.js/issues/92534", launcher)
        self.assertIn("python -m uvicorn app.main:app", launcher)
        self.assertGreaterEqual(launcher.count("cmd.exe /d /s /c"), 2)
        self.assertGreaterEqual(launcher.count("2>&1"), 2)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell behavior")
    def test_windows_job_receives_merged_native_stderr_as_plain_output(self) -> None:
        probe = r'''
$job = Start-Job -ScriptBlock {
  cmd.exe /d /s /c "(echo CHALK_NATIVE_STDERR 1>&2) 2>&1"
}
Wait-Job -Job $job | Out-Null
$records = @(Receive-Job -Job $job *>&1)
Remove-Job -Job $job -Force
if ($records.Count -ne 1 -or -not ($records[0] -is [string])) { exit 2 }
$rendered = $records | Out-String
if ($rendered -notmatch "CHALK_NATIVE_STDERR") { exit 3 }
if ($rendered -match "CategoryInfo|RemoteException|NativeCommandError") { exit 4 }
'''
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", probe],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
