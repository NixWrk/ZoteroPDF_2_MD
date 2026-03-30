from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


RUNTIME_TEMP_DIRNAME = "_z2m_runtime_tmp"


def runtime_temp_root(output_dir: Path) -> Path:
    root = output_dir / RUNTIME_TEMP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_temp_dir(root: Path, prefix: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))


def cleanup_runtime_temp_root(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)

