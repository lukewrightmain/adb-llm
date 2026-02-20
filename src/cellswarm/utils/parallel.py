"""Async parallel-over-devices helpers."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, TypeVar

from loguru import logger

from cellswarm.core.device import DeviceInfo

T = TypeVar("T")


async def run_on_all(
    devices: list[DeviceInfo],
    func: Callable[[DeviceInfo], Coroutine[Any, Any, T]],
    label: str = "operation",
    stop_on_error: bool = False,
) -> dict[str, T | Exception]:
    """Run an async function on all devices in parallel.

    Returns a dict mapping serial -> result or Exception.
    """

    async def _wrapped(device: DeviceInfo) -> tuple[str, T | Exception]:
        try:
            result = await func(device)
            return device.serial, result
        except Exception as e:
            logger.error("{} failed on {}: {}", label, device.serial, e)
            return device.serial, e

    if stop_on_error:
        # Use TaskGroup so first exception cancels the rest
        results: dict[str, T | Exception] = {}
        tasks: list[asyncio.Task[tuple[str, T | Exception]]] = []
        async with asyncio.TaskGroup() as tg:
            for dev in devices:
                tasks.append(tg.create_task(_wrapped(dev)))
        for task in tasks:
            serial, result = task.result()
            results[serial] = result
        return results

    # Default: gather all, don't stop on individual failures
    coros = [_wrapped(dev) for dev in devices]
    raw_results = await asyncio.gather(*coros)
    return {serial: result for serial, result in raw_results}
