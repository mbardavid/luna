from __future__ import annotations

import pytest

from web3_infra.rpc_manager import RPCManager


@pytest.mark.asyncio
async def test_stop_disconnects_all_providers(monkeypatch) -> None:
    manager = RPCManager(endpoints=["https://rpc-1.example", "https://rpc-2.example"])
    await manager.start()

    disconnected: list[str] = []

    for url, w3 in manager._web3_instances.items():
        provider = w3.provider

        async def _disconnect(url=url) -> None:
            disconnected.append(url)

        monkeypatch.setattr(provider, "disconnect", _disconnect)

    await manager.stop()

    assert sorted(disconnected) == sorted(["https://rpc-1.example", "https://rpc-2.example"])
    assert manager._web3_instances == {}
