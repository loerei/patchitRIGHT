import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from patchitright_mcp.patch_file import trigger_jcodemunch_sync, write_file


def test_trigger_jcodemunch_sync_disabled():
    with patch("os.environ.get", return_value="false"):
        with patch("jcodemunch_mcp.tools.index_file.index_file") as mock_index:
            trigger_jcodemunch_sync(Path("dummy.py"))
            time.sleep(0.1)  # Allow thread worker to run
            mock_index.assert_not_called()


def test_trigger_jcodemunch_sync_enabled():
    with patch("os.environ.get", side_effect=lambda k, default=None: "true" if k == "PATCHITRIGHT_SYNC_JCODEMUNCH" else default):
        with patch("jcodemunch_mcp.tools.index_file.index_file") as mock_index:
            trigger_jcodemunch_sync(Path("dummy.py"), storage_path="some_db")
            
            # Wait for thread to finish
            retries = 10
            while mock_index.call_count == 0 and retries > 0:
                time.sleep(0.05)
                retries -= 1
                
            mock_index.assert_called_once_with(
                path=str(Path("dummy.py").resolve()),
                use_ai_summaries=False,
                storage_path="some_db"
            )
