"""S3 mirror of run directories (implemented in slice 5)."""

from __future__ import annotations

from pathlib import Path


def mirror(run_dir: Path, storage_cfg) -> None:
    if not storage_cfg.s3_uri:
        return
