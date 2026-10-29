"""1.0.72: the studio voice setup runs its commands from inside its own folder, so its paths must be absolute
even when the data folder is set as a relative path (CBX_DATA_DIR=data) — else the new environment's python
"cannot be found" ([WinError 2])."""
from pathlib import Path

from cbx.config import settings
from cbx.voice import studio


def test_studio_paths_are_absolute_with_a_relative_data_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "data_dir", Path("data"))
    assert studio.home().is_absolute() and studio.home() == (tmp_path / "data" / "voice" / "studio")
    assert studio.venv_python().is_absolute() and studio.prints().is_absolute()
