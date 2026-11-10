"""Build the distributable ZIP (source, config, docs, tests, sample data, models, reports).

Usage: python scripts/package.py [output.zip]
Excludes virtual environments, caches, local databases and previous build outputs.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "htmlcov",
    ".git",
}
SKIP_SUFFIXES = (".pyc", ".db", ".zip")
SKIP_FILES = {".coverage", ".env"}


def build(out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for directory, dirs, files in os.walk(ROOT):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(files):
                if name.endswith(SKIP_SUFFIXES) or name in SKIP_FILES:
                    continue
                path = Path(directory) / name
                info = zipfile.ZipInfo.from_file(path, Path("flexdrop") / path.relative_to(ROOT))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, path.read_bytes())
                count += 1
    return count


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "FlexDrop_Nexara9.zip"
    n = build(target)
    print(f"wrote {target} ({n} files, {target.stat().st_size // 1024} KB)")
