import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from patchitright_mcp.patch_file import trigger_jcodemunch_sync


def test_trigger_jcodemunch_sync_disabled(monkeypatch):
    monkeypatch.setenv("PATCHITRIGHT_SYNC_JCODEMUNCH", "false")
    with patch("shutil.which", return_value="/usr/bin/jcodemunch-mcp"):
        with patch("subprocess.run") as mock_run:
            trigger_jcodemunch_sync(Path("dummy.py"))
            time.sleep(0.05)
            mock_run.assert_not_called()


def test_trigger_jcodemunch_sync_cli_missing(monkeypatch):
    monkeypatch.setenv("PATCHITRIGHT_SYNC_JCODEMUNCH", "true")
    with patch("shutil.which", return_value=None):
        with patch("subprocess.run") as mock_run:
            trigger_jcodemunch_sync(Path("dummy.py"))
            time.sleep(0.05)
            mock_run.assert_not_called()


def test_trigger_jcodemunch_sync_enabled(monkeypatch):
    monkeypatch.setenv("PATCHITRIGHT_SYNC_JCODEMUNCH", "true")
    cli_path = "/usr/local/bin/jcodemunch-mcp"
    with patch("shutil.which", return_value=cli_path):
        with patch("subprocess.run") as mock_run:
            trigger_jcodemunch_sync(Path("dummy.py"), storage_path="some_db")

            retries = 20
            while mock_run.call_count == 0 and retries > 0:
                time.sleep(0.05)
                retries -= 1

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args[0] == [
                cli_path,
                "index-file",
                str(Path("dummy.py").resolve()),
                "--db",
                "some_db",
            ]
            assert kwargs["stdout"] is subprocess.DEVNULL
            assert kwargs["stderr"] is subprocess.DEVNULL
