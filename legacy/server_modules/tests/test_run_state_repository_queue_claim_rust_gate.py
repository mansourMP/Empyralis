import asyncio
from unittest.mock import patch

import pytest

from server_modules import run_state_repository


def test_claim_run_blocks_before_pool_when_rust_denies() -> None:
    async def run() -> None:
        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel, patch.object(
            run_state_repository,
            "_require_pool",
        ) as require_pool:
            with pytest.raises(RuntimeError, match="rust denied"):
                await run_state_repository.claim_run(
                    "run-1",
                    "worker-1",
                    30,
                    "trace-1",
                    lease_id="lease-1",
                )

        command, payload = kernel.call_args.args
        assert command == "queue-transition-decision"
        assert payload["operation"] == "claim"
        assert payload["run_id"] == "run-1"
        assert payload["worker_id"] == "worker-1"
        assert payload["status"] == "queued"
        require_pool.assert_not_called()

    asyncio.run(run())


def test_release_claim_blocks_before_pool_when_rust_denies() -> None:
    async def run() -> None:
        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel, patch.object(
            run_state_repository,
            "_require_pool",
        ) as require_pool:
            with pytest.raises(RuntimeError, match="rust denied"):
                await run_state_repository.release_claim("run-1", lease_id="lease-1")

        command, payload = kernel.call_args.args
        assert command == "queue-transition-decision"
        assert payload["operation"] == "release"
        assert payload["run_id"] == "run-1"
        assert payload["status"] == "claimed"
        require_pool.assert_not_called()

    asyncio.run(run())
