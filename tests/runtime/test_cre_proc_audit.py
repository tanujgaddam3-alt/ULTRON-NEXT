from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "scripts"),
)

from ultron_cre_proc_audit import _scan_fd_directory


class CreProcAuditRetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "ultron_cre.db"
        self.database.write_bytes(b"fixture")
        self.proc_root = self.root / "proc"

        process_fd = self.proc_root / "4242" / "fd"
        process_fd.mkdir(parents=True)
        (process_fd / "7").symlink_to(self.database)

        self.targets = {str(self.database)}

    def tearDown(self):
        self.temporary.cleanup()

    def test_disappearing_descriptor_is_reenumerated_and_resolved(self):
        import ultron_cre_proc_audit as audit

        real_readlink = audit.os.readlink
        calls = {"count": 0}

        def disappear_once(path):
            calls["count"] += 1
            if calls["count"] == 1:
                raise FileNotFoundError()
            return real_readlink(path)

        with patch.object(audit, "PROC_ROOT", self.proc_root):
            with patch.object(audit.os, "readlink", side_effect=disappear_once):
                result = _scan_fd_directory(4242, self.targets, max_attempts=3)

        self.assertEqual(result["status"], "CHECKED")
        self.assertEqual(result["resolvedDescriptorRaceCount"], 1)
        self.assertEqual(result["unresolvedDescriptorRaceCount"], 0)
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["descriptorRaceEvents"][0]["outcome"],
            "RESOLVED",
        )
        self.assertEqual(result["matches"][0]["targetKind"], "database")

    def test_persistent_descriptor_race_remains_incomplete(self):
        import ultron_cre_proc_audit as audit

        with patch.object(audit, "PROC_ROOT", self.proc_root):
            with patch.object(
                audit.os,
                "readlink",
                side_effect=FileNotFoundError(),
            ):
                result = _scan_fd_directory(
                    4242,
                    self.targets,
                    max_attempts=3,
                )

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["unresolvedDescriptorRaceCount"], 1)
        self.assertEqual(result["matches"], [])
        self.assertEqual(len(result["descriptorRaceEvents"]), 3)
        self.assertTrue(
            all(
                event["outcome"] == "UNRESOLVED"
                and event["pid"] == 4242
                and event["fd"] == "7"
                for event in result["descriptorRaceEvents"]
            )
        )

    def test_permission_error_is_not_reclassified_as_a_race(self):
        import ultron_cre_proc_audit as audit

        with patch.object(audit, "PROC_ROOT", self.proc_root):
            with patch.object(
                audit.os,
                "readlink",
                side_effect=PermissionError(),
            ):
                result = _scan_fd_directory(4242, self.targets)

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["reason"], "descriptor_permission_denied")




class CreProcAuditLifecycleRaceTests(unittest.TestCase):
    def _snapshot(self):
        return {
            "status": "INCOMPLETE",
            "reason": "descriptor_race_unresolved",
            "matches": [],
            "resolvedDescriptorRaceCount": 0,
            "unresolvedDescriptorRaceCount": 1,
        }

    def _proc_root(self, root):
        proc = root / "proc"
        (proc / "4242").mkdir(parents=True)
        return proc

    def test_collect_classifies_process_exit_during_descriptor_race(self):
        import ultron_cre_proc_audit as audit

        with tempfile.TemporaryDirectory() as temporary:
            proc = self._proc_root(Path(temporary))
            with patch.object(audit, "PROC_ROOT", proc):
                with patch.object(
                    audit, "read_start_ticks",
                    side_effect=[12345, FileNotFoundError()],
                ):
                    with patch.object(
                        audit, "_scan_fd_directory",
                        return_value=self._snapshot(),
                    ):
                        result = audit.collect()

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["processRaceCount"], 1)
        self.assertEqual(result["descriptorRaceCount"], 0)
        self.assertEqual(result["failureCounts"], {})
        self.assertEqual(result["databaseDescriptorMatches"], [])

    def test_persistent_descriptor_race_for_live_process_blocks_collection(self):
        import ultron_cre_proc_audit as audit

        with tempfile.TemporaryDirectory() as temporary:
            proc = self._proc_root(Path(temporary))
            with patch.object(audit, "PROC_ROOT", proc):
                with patch.object(
                    audit, "read_start_ticks",
                    side_effect=[12345, 12345],
                ):
                    with patch.object(
                        audit, "_scan_fd_directory",
                        return_value=self._snapshot(),
                    ):
                        result = audit.collect()

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["processRaceCount"], 0)
        self.assertEqual(result["descriptorRaceCount"], 1)
        self.assertEqual(
            result["failureCounts"],
            {"descriptor_race_unresolved": 1},
        )

    def test_permission_error_during_identity_recheck_fails_closed(self):
        import ultron_cre_proc_audit as audit

        with tempfile.TemporaryDirectory() as temporary:
            proc = self._proc_root(Path(temporary))
            with patch.object(audit, "PROC_ROOT", proc):
                with patch.object(
                    audit, "read_start_ticks",
                    side_effect=[12345, PermissionError()],
                ):
                    with patch.object(
                        audit, "_scan_fd_directory",
                        return_value=self._snapshot(),
                    ):
                        result = audit.collect()

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["processRaceCount"], 0)
        self.assertEqual(result["descriptorRaceCount"], 0)
        self.assertEqual(
            result["failureCounts"],
            {"process_stat_permission_denied": 1},
        )


class CreProcAuditScandirLifetimeTests(unittest.TestCase):
    def test_fd_links_are_read_while_scandir_is_open(self):
        import ultron_cre_proc_audit as audit

        with tempfile.TemporaryDirectory() as temporary:
            proc_root = Path(temporary) / "proc"
            (proc_root / "4242" / "fd").mkdir(parents=True)

            state = {"scandir_open": False}

            class FakeEntry:
                name = "3"

            class FakeScandir:
                def __enter__(self):
                    state["scandir_open"] = True
                    return iter([FakeEntry()])

                def __exit__(self, exc_type, exc_value, traceback):
                    state["scandir_open"] = False
                    return False

            def fake_readlink(path):
                if not state["scandir_open"]:
                    raise FileNotFoundError(
                        "scandir descriptor closed before readlink"
                    )
                return "/non-database-descriptor"

            with patch.object(audit, "PROC_ROOT", proc_root):
                with patch.object(
                    audit.os, "scandir", side_effect=lambda path: FakeScandir()
                ):
                    with patch.object(
                        audit.os, "readlink", side_effect=fake_readlink
                    ):
                        result = audit._scan_fd_directory(
                            4242,
                            {"/home/ultron/data/ultron_cre.db"},
                        )

            self.assertEqual(result["status"], "CHECKED")
            self.assertEqual(result["unresolvedDescriptorRaceCount"], 0)
            self.assertEqual(result["matches"], [])
            self.assertFalse(state["scandir_open"])

