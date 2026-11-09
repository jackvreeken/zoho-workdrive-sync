from unittest.mock import MagicMock

import pytest

from workdrive_sync.state import StateDB
from workdrive_sync.sync import Action, SyncEngine, SyncItem


@pytest.mark.parametrize("run", [SyncEngine.execute, SyncEngine.quick_upload])
def test_upload_of_vanished_file_makes_no_api_calls(tmp_path, run):
    api = MagicMock()
    engine = SyncEngine(api, StateDB(path=tmp_path / "state.db"), tmp_path, "ROOT")
    item = SyncItem(rel_path="moved/away.txt", action=Action.UPLOAD)

    assert run(engine, [item]) == []
    assert api.method_calls == []
