"""Run the ULTRON WebSocket gateway without the interactive CLI."""

import asyncio
import os
import signal
from pathlib import Path


async def _serve(shutdown_requested: asyncio.Event) -> None:
    from ultron.application.runtime.interface_gateway import serve_interface
    from ultron.infrastructure.container import container

    await serve_interface(
        container,
        host="127.0.0.1",
        port=8080,
        shutdown_event=shutdown_requested,
    )


def main() -> None:
    project_root = Path(__file__).resolve().parents[3]
    default_operation_database = (
        project_root / "data" / "ultron_operations.db"
    )
    os.environ.setdefault("ULTRON_OPERATION_DB", str(default_operation_database))
    os.environ.setdefault(
        "ULTRON_CRE_DATABASE",
        str(project_root / "data" / "ultron_cre.db"),
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_requested = asyncio.Event()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop_requested.set)

    async def run() -> None:
        server_task = asyncio.create_task(_serve(stop_requested))
        stop_task = asyncio.create_task(stop_requested.wait())
        try:
            await asyncio.wait(
                (server_task, stop_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not stop_requested.is_set():
                await server_task
                return
            await server_task
        finally:
            if not stop_task.done():
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
            if not server_task.done():
                server_task.cancel()
                await asyncio.gather(server_task, return_exceptions=True)

    try:
        loop.run_until_complete(run())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
