from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cre_writer_evidence import WriterEvidenceCollector  # noqa: E402


def _write_proc_identity(proc_root: Path, pid: int, start_ticks: int) -> Path:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    fields = ["0"] * 20
    fields[0] = "S"
    fields[2] = str(pid)
    fields[19] = str(start_ticks)
    (process / "stat").write_text(
        f"{pid} (python) {' '.join(fields)}",
        encoding="ascii",
    )
    return process


class CreWriterEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.state = self.base / "manager-state"
        self.proc = self.base / "proc"
        self.systemd = self.base / "systemd"
        self.cron = self.base / "cron"
        self.root.mkdir()
        self.state.mkdir()
        self.state.chmod(0o700)
        self.proc.mkdir()
        self.systemd.mkdir()
        self.cron.mkdir()
        for source in (
            "ultron/application/runtime/service_entrypoint.py",
            "ultron/application/content/store.py",
            "tools/migrate_cre_private_render_operations.py",
            "scripts/cre_schema_validator.py",
            "scripts/ultron_sqlite_backup.py",
        ):
            path = self.root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture source\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def collector(self) -> WriterEvidenceCollector:
        return WriterEvidenceCollector(
            project_root=self.root,
            state_directory=self.state,
            proc_root=self.proc,
            systemd_roots=(self.systemd,),
            cron_roots=(self.cron,),
            clock=lambda: "2026-11-02T03:04:05.000+00:00",
        )

    def test_reports_read_only_snapshot_and_unknown_continuous_quiescence(self) -> None:
        pid = 4242
        start_ticks = 9876
        database = self.root / "data/ultron_cre.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"fixture database bytes")
        process = _write_proc_identity(self.proc, pid, start_ticks)
        (process / "exe").symlink_to(self.root / ".venv/bin/python")
        (process / "cwd").symlink_to(self.root)
        fd_directory = process / "fd"
        fd_directory.mkdir()
        (fd_directory / "7").symlink_to(database)
        (fd_directory / "8").symlink_to(Path(f"{database}-wal"))
        manager_state = {
            "version": 1,
            "services": {
                "gateway": {"pid": pid, "start_ticks": start_ticks},
            },
            "gatewayQuiesce": {"status": "NOT_QUIESCED"},
        }
        state_file = self.state / "services.json"
        state_file.write_text(json.dumps(manager_state), encoding="utf-8")
        state_file.chmod(0o600)
        unit = self.systemd / "ultron.service"
        unit.write_text(
            "[Service]\nRestart=on-failure\nEnvironment=TOKEN=must-not-be-reported\n",
            encoding="utf-8",
        )
        before = {
            path: path.read_bytes()
            for path in (database, state_file, unit)
        }

        with patch("os.kill", side_effect=AssertionError("must not signal")), patch(
            "subprocess.Popen", side_effect=AssertionError("must not spawn")
        ):
            report = self.collector().collect()

        self.assertEqual(report["generatedAt"], "2026-11-02T03:04:05.000+00:00")
        self.assertEqual(report["overallStatus"], "NOT VERIFIED")
        self.assertEqual(report["quiescenceStatus"], "NOT VERIFIED")
        self.assertFalse(report["databaseTarget"]["accessed"])
        self.assertEqual(
            next(
                item["identityStatus"]
                for item in report["managerState"]["services"]
                if item["service"] == "gateway"
            ),
            "PARTIAL_MATCH_COMMAND_IDENTITY_NOT_INSPECTED",
        )
        self.assertEqual(
            len(report["processSnapshot"]["databaseDescriptorMatches"]),
            2,
        )
        self.assertEqual(
            report["restartPolicies"]["systemdServiceFiles"][0]["restartPolicy"],
            "on-failure",
        )
        self.assertEqual(
            report["scheduledAndExternalSources"]["sources"][0]["status"],
            "NOT_CHECKED",
        )
        self.assertNotIn("must-not-be-reported", json.dumps(report))
        self.assertEqual(
            before,
            {path: path.read_bytes() for path in before},
        )

    def test_unavailable_evidence_is_reported_incomplete(self) -> None:
        missing_state = self.state / "services.json"
        report = self.collector().collect()

        self.assertFalse(missing_state.exists())
        self.assertEqual(report["overallStatus"], "INCOMPLETE")
        self.assertEqual(report["managerState"]["status"], "COULD_NOT_VERIFY")
        self.assertEqual(report["managerState"]["gatewayQuiesce"], "UNKNOWN")
        self.assertEqual(report["processSnapshot"]["status"], "CHECKED")
        self.assertEqual(
            report["scheduledAndExternalSources"]["status"],
            "NOT_CHECKED",
        )
        self.assertEqual(report["quiescenceStatus"], "NOT VERIFIED")

    def test_unreadable_process_snapshot_is_not_reported_as_empty_verified(self) -> None:
        collector = WriterEvidenceCollector(
            project_root=self.root,
            state_directory=self.state,
            proc_root=self.base / "missing-proc",
            systemd_roots=(self.systemd,),
            cron_roots=(self.cron,),
            clock=lambda: "fixed",
        )
        report = collector.collect()

        self.assertEqual(report["overallStatus"], "INCOMPLETE")
        self.assertEqual(report["processSnapshot"]["status"], "COULD_NOT_VERIFY")
        self.assertIsNone(report["processSnapshot"]["unreadableProcessCount"])


if __name__ == "__main__":
    unittest.main()
