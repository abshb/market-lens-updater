import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import worker


class StatePublicationTests(unittest.TestCase):
    def test_failed_refresh_never_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = types.SimpleNamespace(pack_state=lambda *args: None)
            with patch.object(worker, 'PRIVATE', Path(folder)), patch.dict('sys.modules', {'cloud_refresh': fake}), patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)), patch.object(worker, 'gh') as gh:
                with self.assertRaises(RuntimeError):
                    worker.refresh()
                gh.assert_not_called()

    def test_failed_upload_never_prunes(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = types.SimpleNamespace(pack_state=lambda *args: None)
            with patch.object(worker, 'PRIVATE', Path(folder)), patch.dict('sys.modules', {'cloud_refresh': fake}), patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)), patch.object(worker, 'gh', side_effect=RuntimeError('upload failed')) as gh:
                with self.assertRaises(RuntimeError):
                    worker.refresh()
                self.assertEqual(gh.call_count, 1)
                self.assertEqual(gh.call_args.args[:2], ('release', 'upload'))

    def test_missing_token_stops_before_private_access(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(worker, 'release') as release:
            with self.assertRaises(RuntimeError):
                worker.restore()
            release.assert_not_called()


if __name__ == '__main__':
    unittest.main()
