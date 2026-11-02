import asyncio
import os
import threading
from pathlib import Path

from ultron.application.runtime.interface_gateway import serve_interface
from ultron.application.runtime.events import RuntimeEvent


def start_interface_server(runtime_container):
    asyncio.run(
        serve_interface(
            runtime_container,
            host="127.0.0.1",
            port=8080,
        )
    )


def main():
    default_operation_database = (
        Path(__file__).resolve().parent / "data" / "ultron_operations.db"
    )
    os.environ.setdefault(
        "ULTRON_OPERATION_DB",
        str(default_operation_database),
    )

    from ultron.infrastructure.container import container

    print("=" * 60)
    print("ULTRON ONLINE")
    print("Interface: ws://127.0.0.1:8080/ultron/stream")
    print("Type 'exit' to quit.")
    print("=" * 60)

    interface_thread = threading.Thread(
        target=start_interface_server,
        name="ultron-interface",
        daemon=True,
        args=(container,),
    )
    interface_thread.start()

    while True:
        user_input = input("\nYou > ").strip()

        if user_input.lower() in ("exit", "quit"):
            print("ULTRON shutting down...")
            break

        try:
            cycle = container.runtime.run_cycle(
                RuntimeEvent(
                    source="cli",
                    payload=user_input,
                )
            )

            if hasattr(container, "task_service"):
                pending_task = container.task_service.run_pending_tasks(limit=1)
                if pending_task:
                    print("\nULTRON TASK >")
                    print(pending_task[0])

            print("\nULTRON >")
            print(cycle)

        except Exception:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
