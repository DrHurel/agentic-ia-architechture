"""
Project Advancement Context - Tracks expert outputs and enables progressive task generation.

This module implements the concept where:
1. Projects are planned progressively based on expert outputs
2. Expert agents (e.g., Architect) produce outputs that inform subsequent task generation
3. Tasks are generated dynamically based on what experts produce, not pre-planned upfront

Example flow:
1. Initial request -> Architect task (only task initially scheduled)
2. Architect produces architecture -> Code writer tasks generated based on architecture
3. Code writer produces code -> Tester tasks generated based on actual code
"""

import asyncio
from typing import Optional, Any
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import structlog

from src.core.interfaces import (
    IProjectAdvancementContext,
    AgentType,
    Task,
)


logger = structlog.get_logger()


class ExpertPhase(str, Enum):
    """Phases of project advancement based on expert work."""

    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    QUALITY_REVIEW = "quality_review"
    DEPLOYMENT = "deployment"


# Define the workflow: which experts must complete before others can start
EXPERT_WORKFLOW = {
    # Architecture phase comes first
    AgentType.ARCHITECT: {
        "phase": ExpertPhase.ARCHITECTURE,
        "prerequisites": [],  # No prerequisites
        "enables": [AgentType.CODE_WRITER],  # Enables code writing
    },
    # Implementation phase
    AgentType.CODE_WRITER: {
        "phase": ExpertPhase.IMPLEMENTATION,
        "prerequisites": [AgentType.ARCHITECT],
        "enables": [
            AgentType.TESTER_WHITEBOX,
            AgentType.TESTER_BLACKBOX,
            AgentType.CODE_QUALITY,
        ],
    },
    # Testing phase
    AgentType.TESTER_WHITEBOX: {
        "phase": ExpertPhase.TESTING,
        "prerequisites": [AgentType.CODE_WRITER],
        "enables": [AgentType.CICD],
    },
    AgentType.TESTER_BLACKBOX: {
        "phase": ExpertPhase.TESTING,
        "prerequisites": [AgentType.CODE_WRITER],
        "enables": [AgentType.CICD],
    },
    # Quality review phase
    AgentType.CODE_QUALITY: {
        "phase": ExpertPhase.QUALITY_REVIEW,
        "prerequisites": [AgentType.CODE_WRITER],
        "enables": [],
    },
    # Deployment phase
    AgentType.CICD: {
        "phase": ExpertPhase.DEPLOYMENT,
        "prerequisites": [AgentType.CODE_WRITER],
        "enables": [],
    },
}


@dataclass
class ExpertOutput:
    """Represents output from an expert agent."""

    task_id: str
    agent_type: AgentType
    output: Any
    timestamp: datetime = field(default_factory=datetime.now)
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class ProjectAdvancementState:
    """Tracks the advancement state of a project."""

    project_id: str
    original_request: str
    current_phase: ExpertPhase = ExpertPhase.ARCHITECTURE

    # Expert outputs by agent type
    expert_outputs: dict[AgentType, list[ExpertOutput]] = field(
        default_factory=lambda: defaultdict(list)
    )

    # Track which agent types have produced output
    completed_agent_types: set[AgentType] = field(default_factory=set)

    # Track which follow-up agent types have been triggered
    triggered_follow_ups: set[AgentType] = field(default_factory=set)

    # Architecture context (extracted from architect output)
    architecture_context: dict = field(default_factory=dict)

    # Files created/modified during project
    project_files: dict[str, dict] = field(
        default_factory=dict
    )  # path -> {created_by, content_summary}


class ProjectAdvancementContext(IProjectAdvancementContext):
    """
    Manages project advancement based on expert outputs.

    Key features:
    - Stores expert outputs for use in subsequent task generation
    - Determines when follow-up tasks should be generated
    - Provides context to task generators based on previous expert work
    """

    def __init__(self):
        self._logger = logger.bind(component="ProjectAdvancementContext")
        self._projects: dict[str, ProjectAdvancementState] = {}
        self._lock = asyncio.Lock()

    async def create_project(
        self, project_id: str, original_request: str
    ) -> ProjectAdvancementState:
        """Create a new project advancement state."""
        async with self._lock:
            state = ProjectAdvancementState(
                project_id=project_id, original_request=original_request
            )
            self._projects[project_id] = state

            self._logger.info(
                "Project advancement context created", project_id=project_id
            )
            return state

    async def get_project(self, project_id: str) -> Optional[ProjectAdvancementState]:
        """Get project advancement state."""
        return self._projects.get(project_id)

    async def store_expert_output(
        self, project_id: str, agent_type: AgentType, task_id: str, output: Any
    ) -> None:
        """Store output from an expert agent for future task generation."""
        async with self._lock:
            state = self._projects.get(project_id)
            if not state:
                self._logger.warning(
                    "Project not found for storing expert output", project_id=project_id
                )
                return

            # Extract files from output
            files_created = []
            files_modified = []
            if isinstance(output, dict):
                if output.get("file_path"):
                    files_created.append(output["file_path"])
                if output.get("files"):
                    files_created.extend([f.get("path", "") for f in output["files"]])

            # Create expert output record
            expert_output = ExpertOutput(
                task_id=task_id,
                agent_type=agent_type,
                output=output,
                files_created=files_created,
                files_modified=files_modified,
            )

            state.expert_outputs[agent_type].append(expert_output)
            state.completed_agent_types.add(agent_type)

            # Track files at project level
            for file_path in files_created:
                state.project_files[file_path] = {
                    "created_by": agent_type.value,
                    "task_id": task_id,
                }

            # Update architecture context if this is an architect output
            if agent_type == AgentType.ARCHITECT:
                state.architecture_context = self._extract_architecture_context(output)
                state.current_phase = ExpertPhase.IMPLEMENTATION

            # Update phase based on completed work
            self._update_phase(state, agent_type)

            self._logger.info(
                "Expert output stored",
                project_id=project_id,
                agent_type=agent_type.value,
                task_id=task_id,
                files_created=len(files_created),
                current_phase=state.current_phase.value,
            )

    def _extract_architecture_context(self, output: Any) -> dict:
        """Extract structured architecture context from architect output."""
        if not isinstance(output, dict):
            return {"raw_output": str(output)}

        return {
            "project_name": output.get("project_name", ""),
            "files": output.get("files", []),
            "components": output.get("components", []),
            "dependencies": output.get("dependencies", {}),
            "entry_point": output.get("entry_point", "/workspace/main.py"),
            "test_files": output.get("test_files", []),
            "raw_output": output,
        }

    def _update_phase(
        self, state: ProjectAdvancementState, completed_agent_type: AgentType
    ) -> None:
        """Update project phase based on completed work."""
        workflow = EXPERT_WORKFLOW.get(completed_agent_type, {})
        phase = workflow.get("phase")

        if phase:
            # Phase progresses when key agents complete
            phase_order = [
                ExpertPhase.ARCHITECTURE,
                ExpertPhase.IMPLEMENTATION,
                ExpertPhase.TESTING,
                ExpertPhase.QUALITY_REVIEW,
                ExpertPhase.DEPLOYMENT,
            ]

            current_idx = (
                phase_order.index(state.current_phase)
                if state.current_phase in phase_order
                else 0
            )
            new_idx = phase_order.index(phase) if phase in phase_order else 0

            if new_idx > current_idx:
                state.current_phase = phase

    async def get_expert_outputs(
        self, project_id: str, agent_types: Optional[list[AgentType]] = None
    ) -> dict[AgentType, list[dict]]:
        """Get stored expert outputs, optionally filtered by agent types."""
        state = self._projects.get(project_id)
        if not state:
            return {}

        result = {}
        for agent_type, outputs in state.expert_outputs.items():
            if agent_types is None or agent_type in agent_types:
                result[agent_type] = [
                    {
                        "task_id": o.task_id,
                        "output": o.output,
                        "timestamp": o.timestamp.isoformat(),
                        "files_created": o.files_created,
                    }
                    for o in outputs
                ]

        return result

    async def get_context_for_task_generation(
        self, project_id: str, target_agent_type: AgentType
    ) -> dict:
        """
        Get relevant context for generating tasks for a specific agent type.
        Returns outputs from prerequisite experts.
        """
        state = self._projects.get(project_id)
        if not state:
            return {"error": "Project not found"}

        workflow = EXPERT_WORKFLOW.get(target_agent_type, {})
        prerequisites = workflow.get("prerequisites", [])

        context = {
            "project_id": project_id,
            "original_request": state.original_request,
            "current_phase": state.current_phase.value,
            "target_agent_type": target_agent_type.value,
            "prerequisite_outputs": {},
            "architecture_context": state.architecture_context,
            "project_files": list(state.project_files.keys()),
        }

        # Include outputs from prerequisite agents
        for prereq_type in prerequisites:
            if prereq_type in state.expert_outputs:
                context["prerequisite_outputs"][prereq_type.value] = [
                    {"output": o.output, "files_created": o.files_created}
                    for o in state.expert_outputs[prereq_type]
                ]

        self._logger.debug(
            "Context prepared for task generation",
            project_id=project_id,
            target_agent_type=target_agent_type.value,
            prerequisite_count=len(prerequisites),
        )

        return context

    async def should_generate_follow_up_tasks(
        self, project_id: str, completed_agent_type: AgentType
    ) -> list[AgentType]:
        """
        Determine which agent types should have tasks generated after an expert completes.
        """
        async with self._lock:
            state = self._projects.get(project_id)
            if not state:
                return []

            workflow = EXPERT_WORKFLOW.get(completed_agent_type, {})
            enabled_types = workflow.get("enables", [])

            # Filter to types that haven't been triggered yet
            new_types = [
                agent_type
                for agent_type in enabled_types
                if agent_type not in state.triggered_follow_ups
            ]

            # Mark as triggered
            for agent_type in new_types:
                state.triggered_follow_ups.add(agent_type)

            if new_types:
                self._logger.info(
                    "Follow-up task generation triggered",
                    project_id=project_id,
                    completed_agent_type=completed_agent_type.value,
                    new_agent_types=[t.value for t in new_types],
                )

            return new_types

    async def get_files_for_agent(
        self, project_id: str, agent_type: AgentType
    ) -> list[str]:
        """Get list of files relevant for a specific agent type."""
        state = self._projects.get(project_id)
        if not state:
            return []

        # For testers/quality: return files created by code_writer
        if agent_type in (
            AgentType.TESTER_WHITEBOX,
            AgentType.TESTER_BLACKBOX,
            AgentType.CODE_QUALITY,
        ):
            return [
                path
                for path, info in state.project_files.items()
                if info.get("created_by") == AgentType.CODE_WRITER.value
            ]

        # For code_writer: return files defined in architecture
        if agent_type == AgentType.CODE_WRITER:
            return [
                f.get("path", "") for f in state.architecture_context.get("files", [])
            ]

        return list(state.project_files.keys())

    async def is_prerequisite_complete(
        self, project_id: str, target_agent_type: AgentType
    ) -> bool:
        """Check if all prerequisites for an agent type are complete."""
        state = self._projects.get(project_id)
        if not state:
            return False

        workflow = EXPERT_WORKFLOW.get(target_agent_type, {})
        prerequisites = workflow.get("prerequisites", [])

        return all(prereq in state.completed_agent_types for prereq in prerequisites)


class ProjectAdvancementContextFactory:
    """Factory for creating ProjectAdvancementContext instances."""

    _instance: Optional[ProjectAdvancementContext] = None

    @classmethod
    def get_instance(cls) -> ProjectAdvancementContext:
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = ProjectAdvancementContext()
        return cls._instance

    @classmethod
    def create(cls) -> ProjectAdvancementContext:
        """Create a new instance (for testing)."""
        return ProjectAdvancementContext()
