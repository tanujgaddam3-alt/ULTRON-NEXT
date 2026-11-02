from __future__ import annotations

from typing import ClassVar

from ultron.cognition.attention.engine import AttentionEngine
from ultron.cognition.context.builder import CognitiveContextBuilder
from ultron.cognition.context.models import CognitiveContext
from ultron.cognition.schema.engine import SchemaEngine
from ultron.domain.understanding import Understanding
from ultron.goals.manager import GoalManager
from ultron.memory.manager import MemoryManager
from ultron.skills.manager import SkillManager


class ContextEngine:
    """
    Builds the CognitiveContext used by
    every reasoning cycle.
    """

    def __init__(
        self,
        memory_manager: MemoryManager,
        goal_manager: GoalManager,
        schema_engine: SchemaEngine,
        attention_engine: AttentionEngine,
        world_engine,
        skill_manager: SkillManager,
        knowledge_provider,
    ):
        self.memory = memory_manager
        self.goals = goal_manager
        self.schemas = schema_engine
        self.attention = attention_engine
        self.world = world_engine
        self.skills = skill_manager
        self.knowledge = knowledge_provider

    def build(
        self,
        understanding: Understanding,
    ) -> CognitiveContext:

        query = understanding.summary

        goals = self.goals.active_goals()

        memories = self.memory.recall(query)

        schemas = self.schemas.activate(query)

        attention: ClassVar = {}

        # Refresh the authoritative world model before constructing
        # cognitive context so reasoning receives current environment state.
        self.world.refresh()
        world = self.world.summary()

        skills = self.skills.available_skills()

        knowledge = self.knowledge.build(query)

        return (
            CognitiveContextBuilder()
            .begin(query, understanding)
            .goals(goals)
            .memories(memories)
            .schemas(schemas)
            .world(world)
            .skills(skills)
            .knowledge(knowledge)
            .attention(attention)
            .build()
        )
