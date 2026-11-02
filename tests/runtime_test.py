from ultron.infrastructure.container import container
from ultron.application.runtime.events import RuntimeEvent


event = RuntimeEvent(
    source="user",
    payload="open firefox",
)


cycle = container.runtime.run_cycle(
    event
)


print("Cycle completed")

print(
    "Perception:",
    cycle.perception,
)

print(
    "Understanding:",
    cycle.understanding,
)

print(
    "Decision:",
    cycle.decision,
)

print(
    "Plan:",
    cycle.plan,
)

print(
    "Execution:",
    cycle.execution,
)


metrics = container.smith.history()

print(
    "Smith records:",
    len(metrics)
)
