from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from api.routes import ingest


class TestDWGConversionCleanup:
    def test_convert_dwg_cleans_staging_dirs_and_returns_copied_dxf(self, monkeypatch, tmp_path):
        oda_path = tmp_path / "ODAFileConverter.exe"
        oda_path.write_text("fake", encoding="utf-8")
        dwg_path = tmp_path / "robot.dwg"
        dwg_path.write_bytes(b"DWG")

        created_dirs: list[str] = []

        def fake_mkdtemp(suffix=None, prefix=None, dir=None):
            prefix = prefix or "tmp"
            path = tmp_path / f"{prefix}{len(created_dirs)}"
            path.mkdir()
            created_dirs.append(str(path))
            return str(path)

        def fake_run(cmd, **kwargs):
            output_dir = Path(cmd[2])
            (output_dir / "robot.dxf").write_text("DXF", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ingest, "_get_oda_path", lambda: str(oda_path))
        monkeypatch.setattr(ingest.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(ingest.subprocess, "run", fake_run)

        result_path = ingest._convert_dwg_to_dxf(str(dwg_path))

        input_dir, output_dir, result_dir = created_dirs
        assert not os.path.exists(input_dir)
        assert not os.path.exists(output_dir)
        assert os.path.exists(result_path)
        assert Path(result_path).parent == Path(result_dir)

        ingest._safe_remove_dir(result_dir)

    def test_convert_dwg_cleans_staging_dirs_when_no_dxf_output(self, monkeypatch, tmp_path):
        oda_path = tmp_path / "ODAFileConverter.exe"
        oda_path.write_text("fake", encoding="utf-8")
        dwg_path = tmp_path / "robot.dwg"
        dwg_path.write_bytes(b"DWG")

        created_dirs: list[str] = []

        def fake_mkdtemp(suffix=None, prefix=None, dir=None):
            prefix = prefix or "tmp"
            path = tmp_path / f"{prefix}{len(created_dirs)}"
            path.mkdir()
            created_dirs.append(str(path))
            return str(path)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ingest, "_get_oda_path", lambda: str(oda_path))
        monkeypatch.setattr(ingest.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(ingest.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="No DXF output"):
            ingest._convert_dwg_to_dxf(str(dwg_path))

        input_dir, output_dir = created_dirs
        assert not os.path.exists(input_dir)
        assert not os.path.exists(output_dir)
