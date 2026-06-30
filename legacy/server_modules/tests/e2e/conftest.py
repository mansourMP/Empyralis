from __future__ import annotations

from pathlib import Path

import pytest

from server_modules.blackbox_runtime_support import blackbox_runtime_context


@pytest.fixture
def blackbox_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    with blackbox_runtime_context(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        db_pool_size=1,
    ) as harness:
        yield harness
