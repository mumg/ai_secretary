import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build


class DetachImageTests(unittest.TestCase):
    @patch.object(build.time, 'sleep')
    @patch.object(build, 'run')
    def test_retries_busy_image(self, run, sleep):
        run.side_effect = [subprocess.CalledProcessError(16, 'hdiutil'), None]
        build.detach_image('/test/image')
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch.object(build.time, 'sleep')
    @patch.object(build, 'run')
    def test_stops_after_five_attempts(self, run, sleep):
        run.side_effect = subprocess.CalledProcessError(16, 'hdiutil')
        with self.assertRaises(subprocess.CalledProcessError):
            build.detach_image('/test/image')
        self.assertEqual(run.call_count, 5)
        self.assertEqual(sleep.call_count, 4)

    @patch.object(build.time, 'sleep')
    @patch.object(build, 'run')
    def test_other_error_is_not_retried(self, run, sleep):
        run.side_effect = subprocess.CalledProcessError(1, 'hdiutil')
        with self.assertRaises(subprocess.CalledProcessError):
            build.detach_image('/test/image')
        run.assert_called_once()
        sleep.assert_not_called()
