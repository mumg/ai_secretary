from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "windows/setup"))
import check_runtime
import manage


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "AI Secretary"
        (self.root / "vendor").mkdir(parents=True)
        (self.root / "vendor/WinSW.exe").write_bytes(b"mock executable")

    def test_all_bundled_engines_are_probed_without_service_mutations(self):
        def probe(command, **kwargs):
            if command[0].endswith("WinSW.exe"):
                xml = ElementTree.parse(Path(command[0]).with_suffix(".xml"))
                self.assertEqual(xml.findtext("id"), "AISecretaryRuntimeProbe")
                self.assertEqual(xml.findtext("executable"), str(self.root / "python/python.exe"))
                self.assertTrue(Path(command[0]).read_bytes())
            return subprocess.CompletedProcess([], 0, "", "")
        with patch.object(check_runtime.subprocess, "run", side_effect=probe) as run:
            check_runtime.check_runtime(self.root)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 7)
        self.assertEqual(commands[0][1:3], ["-B", "-c"])
        self.assertTrue(any(command[0].endswith("WinSW.exe") and command[-1] == "version" for command in commands))
        self.assertFalse(any("install" in command or "start" in command for command in commands))
        wrapper = next(Path(command[0]) for command in commands if command[0].endswith("WinSW.exe"))
        self.assertFalse(wrapper.parent.exists())

    def test_dll_load_failure_stops_checks(self):
        failed = subprocess.CompletedProcess([], 3221225781, "", "DLL not found")
        with patch.object(check_runtime.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(RuntimeError, "Python dependencies.*DLL not found"):
                check_runtime.check_runtime(self.root)
        self.assertEqual(run.call_count, 1)

    def test_failed_runtime_does_not_create_database_or_change_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            with patch.object(manage, "run", side_effect=RuntimeError("unsupported OS")), \
                 patch.object(manage, "secure_directory") as secure, \
                 patch.object(manage, "stop") as stop:
                with self.assertRaisesRegex(RuntimeError, "unsupported OS"):
                    manage.configure(Path(tmp) / "program", data, None)
            secure.assert_not_called()
            stop.assert_not_called()
            self.assertFalse(data.exists())

    def test_process_launch_failure_and_timeout_name_the_component(self):
        for error in (OSError("bad executable"), subprocess.TimeoutExpired("probe", 60)):
            with self.subTest(error=type(error).__name__), patch.object(check_runtime.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "Python dependencies"):
                    check_runtime.check_runtime(self.root)
