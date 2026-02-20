"""Tests for model distribution."""

from __future__ import annotations

from pathlib import Path

import pytest

from cellswarm.core.errors import ModelNotFoundError
from cellswarm.model.distributor import ModelDistributor


@pytest.mark.asyncio
async def test_distribute_file_not_found(mock_device_infos):
    dist = ModelDistributor()
    with pytest.raises(ModelNotFoundError):
        await dist.distribute("/nonexistent/model.gguf", mock_device_infos)


@pytest.mark.asyncio
async def test_distribute_to_devices(mock_adb, mock_device_infos, tmp_gguf):
    dist = ModelDistributor()
    results = await dist.distribute(str(tmp_gguf), mock_device_infos)
    assert len(results) == 9
    assert all(r.success for r in results)
    assert mock_adb["push"].call_count == 9


@pytest.mark.asyncio
async def test_distribute_skip_existing(mock_adb, mock_device_infos, tmp_gguf):
    # Mark first device as already having the model
    mock_device_infos[0].models = [tmp_gguf.name]

    dist = ModelDistributor()
    results = await dist.distribute(str(tmp_gguf), mock_device_infos, skip_existing=True)
    assert len(results) == 8  # Skipped one
    assert mock_adb["push"].call_count == 8
