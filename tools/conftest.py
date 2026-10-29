# The tool folders' own tests use the suite's fixtures (owner, admin, user, db …).
from tests.conftest import *  # noqa: F401,F403


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _real_tool_folders():
    """A tool folder's tests are about the folders that ship: point the engine at them, whatever an
    earlier test pointed it at."""
    from cbx.config import ROOT
    from cbx.engine import loader

    if loader.registry().root != ROOT / "tools":
        loader.reload(ROOT / "tools")
    yield
