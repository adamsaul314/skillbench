"""Creating the fresh temp workspace each run happens in."""

import shutil
import subprocess
import tempfile
from pathlib import Path

_WORKSPACE_PREFIX = "skillbench-ws-"


def create_workspace(fixture: Path | None) -> Path:
    """Make a new temp dir (kept after the run), copy the fixture into it, and `git init` it."""
    workspace = Path(tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX))
    if fixture is not None:
        shutil.copytree(fixture, workspace, dirs_exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
    return workspace
