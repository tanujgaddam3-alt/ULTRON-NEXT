from ultron.core.event_bus import event_bus
from ultron.domain.event import Event, EventType
from ultron.executive.context import ExecutiveContext
from ultron.executive.state import ExecutiveState
from ultron.infrastructure.container import container


class Executive:
    """
    Coordinates ULTRON's cognitive pipeline.
    """

    def __init__(self):
        self.state = ExecutiveState.IDLE

        # Core systems
        self.memory = container.memory
        self.blackboard = container.blackboard
        self.world = container.world

        # Cognitive engines
        self.perception = container.perception
        self.understanding = container.understanding
        self.context_engine = container.context
        self.reasoning = container.reasoning
        self.decision = container.decision
        self.planning = container.planning
        self.execution = container.execution
        self.reflection = container.reflection
        self.learning = container.learning

    def process(self, text: str):
        """
        Process a user request through the cognitive pipeline.
        """
        print("=" * 60)
        print("EXECUTIVE PROCESS START")
        print(f"Request: {text}")
        # --------------------------------------------------
        # Refresh ULTRON's understanding of the environment
        # --------------------------------------------------
        world = self.world.refresh()

        # Store world snapshot in the blackboard
        # self.blackboard.update(world=world)

        # --------------------------------------------------
        # Perception
        # --------------------------------------------------
        self.state = ExecutiveState.PERCEIVING

        perception = self.perception.perceive(text)

        self.memory.sensory.add(perception)

        event_bus.publish(
            Event(
                type=EventType.USER_MESSAGE,
                source="executive",
                payload={
                    "text": text,
                },
            )
        )

        # --------------------------------------------------
        # Understanding
        # --------------------------------------------------
        self.state = ExecutiveState.UNDERSTANDING

        understanding = self.understanding.understand(perception)
        # --------------------------------------------------
        # Context
        # --------------------------------------------------

        cognitive_context = self.context_engine.build(
            understanding,
        )
        # --------------------------------------------------
        reasoning = self.reasoning.reason(cognitive_context)

        hypotheses = reasoning.hypotheses
        # --------------------------------------------------
        # Decision
        # --------------------------------------------------

        decision = self.decision.decide(
            hypotheses,
        )

        # --------------------------------------------------
        # Planning
        # --------------------------------------------------
        print("Decision:", decision.decision.action)
        planning = self.planning.plan(
            decision.decision,
        )

        # --------------------------------------------------
        # Execution
        # --------------------------------------------------

        if planning.valid:
            print("Calling ExecutionEngine.execute()")
            execution = self.execution.execute(
                planning.plan,
            )

        else:
            execution = None

        # --------------------------------------------------
        # Reflection
        # --------------------------------------------------

        reflection = None

        if execution is not None:
            reflection = self.reflection.reflect(
                execution,
            )
        # --------------------------------------------------
        # Learning
        # --------------------------------------------------

        learning = None

        if reflection is not None:
            learning = self.learning.learn(
                execution,
                reflection,
            )
        # --------------------------------------------------
        # Create executive context
        # --------------------------------------------------
        context = ExecutiveContext(
            perception=perception,
            understanding=understanding,
        )

        # Store in working memory
        self.memory.working.add(
            {
                "context": context,
            }
        )

        # Store latest perception & understanding
        self.blackboard.update(
            perception=perception,
            understanding=understanding,
            context=cognitive_context,
            reasoning=reasoning,
            hypotheses=hypotheses,
            decision=decision,
            planning=planning,
            execution=execution,
            reflection=reflection,
            learning=learning,
        )

        self.state = ExecutiveState.IDLE

        return context
