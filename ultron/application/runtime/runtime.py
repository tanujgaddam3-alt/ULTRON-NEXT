from ultron.core.event_bus import EventBus
from ultron.domain.event import Event, EventType

from .cycle import CognitiveCycle
from .events import RuntimeEvent
from .state import RuntimeState


class CognitiveRuntime:
    """
    Controls ULTRON cognitive cycles.
    """

    def __init__(
        self,
        cognitive_service,
        perception_engine,
        understanding_engine,
        context_engine,
        smith_service,
        event_bus: EventBus | None = None,
    ):

        self.cognitive_service = cognitive_service

        self.perception_engine = perception_engine

        self.understanding_engine = understanding_engine

        self.context_engine = context_engine

        self.smith = smith_service

        self.event_bus = event_bus

        self.state = RuntimeState()

    def run_cycle(
        self,
        event: RuntimeEvent,
    ) -> CognitiveCycle:

        cycle = CognitiveCycle(
            event=event,
        )

        # Perception
        perception = self.perception_engine.perceive(event.payload)

        cycle.perception = perception

        # Understanding
        understanding = self.understanding_engine.understand(perception)

        cycle.understanding = understanding

        # Context
        context = self.context_engine.build(understanding)

        cycle.context = context

        # Cognition
        result = self.cognitive_service.think(context)

        cycle.reasoning = result["reasoning"]

        cycle.decision = result["decision"]

        cycle.plan = result["plan"]

        cycle.execution = result["execution"]

        cycle.reflection = result["reflection"]

        cycle.learning = result["experience"]

        self.state.cycle_count += 1

        self.state.last_cycle = cycle

        self._update_schema_learning(cycle)

        self.smith.analyze_cycle(cycle)

        if self.event_bus:
            self.event_bus.publish(
                Event(
                    type=EventType.SYSTEM,
                    source="cognitive_runtime",
                    payload={
                        "cycleCount": self.state.cycle_count,
                        "source": event.source,
                        "world": cycle.context.world,
                        "decision": {
                            "action": (
                                cycle.decision.decision.action.value
                                if hasattr(cycle.decision.decision.action, "value")
                                else str(cycle.decision.decision.action)
                            ),
                            "confidence": cycle.decision.decision.confidence,
                            "reason": cycle.decision.decision.reason,
                        },
                        "cognitiveState": {
                            "behavior": (
                                "EXECUTION"
                                if not cycle.execution.record.success
                                else (
                                    "ACTIVE"
                                    if cycle.plan.plan.steps
                                    else "IDLE"
                                )
                            ),
                            "headline": (
                                "EXECUTION REQUIRED"
                                if not cycle.execution.record.success
                                else (
                                    "COGNITIVE CYCLE ACTIVE"
                                    if cycle.plan.plan.steps
                                    else "COGNITIVE CYCLE COMPLETE"
                                )
                            ),
                            "description": (
                                cycle.decision.decision.reason
                                or cycle.execution.record.message
                            ),
                            "tensorInferenceRateHz": 0,
                            "agents": [],
                            "cycleCount": self.state.cycle_count,
                        },
                        "plan": {
                            "id": cycle.plan.plan.id,
                            "goal": cycle.plan.plan.goal,
                            "steps": len(cycle.plan.plan.steps),
                        },
                        "execution": {
                            "success": cycle.execution.record.success,
                            "completedSteps": (
                                cycle.execution.record.completed_steps
                            ),
                            "message": cycle.execution.record.message,
                        },
                        "reflection": {
                            "success": cycle.reflection.success,
                            "confidence": cycle.reflection.confidence,
                            "evaluationScore": (
                                cycle.reflection.evaluation_score
                            ),
                            "quality": cycle.reflection.quality,
                            "recommendation": (
                                cycle.reflection.recommendation
                            ),
                        },
                    },
                )
            )

        return cycle
    def _update_schema_learning(
    self,
    cycle,
):

     schemas = cycle.context.schemas

     if not schemas:
        return


     success = (
        cycle.execution.record.success
    )


     
