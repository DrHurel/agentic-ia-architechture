"""
Dependency Manager - Manages dynamic task dependencies.
Enables tasks to wait for multiple prerequisites and supports runtime dependency injection.
"""

import asyncio
from typing import Optional, Callable
from collections import defaultdict
import structlog

from src.core.interfaces import (
    IDependencyManager,
    Task,
    TaskStatus,
)


logger = structlog.get_logger()


class DependencyManager(IDependencyManager):
    """
    Manages task dependencies with support for:
    - Multiple dependencies per task (all must complete before task can start)
    - Dynamic dependency injection (add new tasks as dependencies of existing tasks)
    - Dependency resolution and tracking
    """

    def __init__(self):
        self._logger = logger.bind(component="DependencyManager")

        # task_id -> set of dependency task IDs
        self._task_dependencies: dict[str, set[str]] = defaultdict(set)

        # task_id -> TaskStatus cache
        self._task_status_cache: dict[str, TaskStatus] = {}

        # Reverse index: dependency_id -> set of task IDs waiting for it
        self._waiting_for: dict[str, set[str]] = defaultdict(set)

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        # Callback for task status lookup (injected by scheduler)
        self._status_lookup: Optional[Callable[[str], Optional[TaskStatus]]] = None

        # Callback for notifying when dependencies are met
        self._on_dependencies_met: Optional[Callable[[str], None]] = None

    def set_status_lookup(self, lookup: Callable[[str], Optional[TaskStatus]]) -> None:
        """Set callback for looking up task status from external source."""
        self._status_lookup = lookup

    def set_on_dependencies_met(self, callback: Callable[[str], None]) -> None:
        """Set callback to be called when all dependencies for a task are met."""
        self._on_dependencies_met = callback

    async def register_task(self, task: Task) -> None:
        """
        Register a task and its dependencies.
        Call this when a task is first scheduled.
        """
        async with self._lock:
            # Handle legacy parent_task_id
            dependencies = set(task.dependency_ids)
            if task.parent_task_id and task.parent_task_id not in dependencies:
                dependencies.add(task.parent_task_id)

            self._task_dependencies[task.id] = dependencies
            self._task_status_cache[task.id] = task.status

            # Update reverse index
            for dep_id in dependencies:
                self._waiting_for[dep_id].add(task.id)

            self._logger.debug(
                "Task registered with dependencies",
                task_id=task.id,
                dependency_count=len(dependencies),
                dependencies=list(dependencies),
            )

    async def add_dependency(self, task_id: str, dependency_id: str) -> None:
        """
        Add a dependency to an existing task.
        The task will wait for this dependency before starting.
        """
        async with self._lock:
            if task_id not in self._task_dependencies:
                self._task_dependencies[task_id] = set()

            self._task_dependencies[task_id].add(dependency_id)
            self._waiting_for[dependency_id].add(task_id)

            self._logger.info(
                "Dependency added to task",
                task_id=task_id,
                new_dependency=dependency_id,
                total_dependencies=len(self._task_dependencies[task_id]),
            )

    async def remove_dependency(self, task_id: str, dependency_id: str) -> None:
        """Remove a dependency from a task."""
        async with self._lock:
            if task_id in self._task_dependencies:
                self._task_dependencies[task_id].discard(dependency_id)
            if dependency_id in self._waiting_for:
                self._waiting_for[dependency_id].discard(task_id)

            self._logger.debug(
                "Dependency removed", task_id=task_id, removed_dependency=dependency_id
            )

    async def get_unmet_dependencies(self, task_id: str) -> list[str]:
        """Get list of dependency IDs that are not yet completed."""
        async with self._lock:
            return await self._get_unmet_dependencies_locked(task_id)

    async def _get_unmet_dependencies_locked(self, task_id: str) -> list[str]:
        """Internal: Get unmet dependencies (must hold lock)."""
        if task_id not in self._task_dependencies:
            return []

        unmet = []
        for dep_id in self._task_dependencies[task_id]:
            status = await self._get_task_status(dep_id)
            if status != TaskStatus.COMPLETED:
                unmet.append(dep_id)

        return unmet

    async def _get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get task status from cache or external lookup."""
        if task_id in self._task_status_cache:
            return self._task_status_cache[task_id]

        if self._status_lookup:
            status = self._status_lookup(task_id)
            if status:
                self._task_status_cache[task_id] = status
            return status

        return None

    async def are_all_dependencies_met(self, task_id: str) -> bool:
        """Check if all dependencies for a task are completed."""
        async with self._lock:
            unmet = await self._get_unmet_dependencies_locked(task_id)
            return len(unmet) == 0

    async def inject_tasks_as_dependencies(
        self, new_tasks: list[Task], target_task_ids: list[str]
    ) -> None:
        """
        Inject new tasks as dependencies of existing tasks.
        The new tasks become prerequisites for the target tasks.

        Example: Architect creates new design tasks, which become dependencies
        for pending code_writer tasks.
        """
        async with self._lock:
            new_task_ids = [t.id for t in new_tasks]

            for target_id in target_task_ids:
                if target_id not in self._task_dependencies:
                    self._task_dependencies[target_id] = set()

                for new_task_id in new_task_ids:
                    self._task_dependencies[target_id].add(new_task_id)
                    self._waiting_for[new_task_id].add(target_id)

            self._logger.info(
                "New tasks injected as dependencies",
                new_task_count=len(new_tasks),
                target_task_count=len(target_task_ids),
                new_task_ids=new_task_ids,
            )

    async def on_task_completed(self, task_id: str) -> list[str]:
        """
        Called when a task completes. Returns list of task IDs
        that now have all dependencies met and can be started.
        """
        async with self._lock:
            self._task_status_cache[task_id] = TaskStatus.COMPLETED

            # Find tasks waiting for this one
            waiting_tasks = list(self._waiting_for.get(task_id, []))
            ready_tasks = []

            for waiting_id in waiting_tasks:
                unmet = await self._get_unmet_dependencies_locked(waiting_id)
                if len(unmet) == 0:
                    ready_tasks.append(waiting_id)
                    self._logger.info(
                        "Task dependencies met, ready to run",
                        task_id=waiting_id,
                        completed_dependency=task_id,
                    )

            # Clean up reverse index
            if task_id in self._waiting_for:
                del self._waiting_for[task_id]

            return ready_tasks

    async def on_task_failed(self, task_id: str) -> list[str]:
        """
        Called when a task fails. Returns list of task IDs
        that should also be marked as failed (dependency failure).
        """
        async with self._lock:
            self._task_status_cache[task_id] = TaskStatus.FAILED

            # Find tasks waiting for this one - they will also fail
            affected_tasks = list(self._waiting_for.get(task_id, []))

            self._logger.info(
                "Task failed, cascading to dependents",
                failed_task_id=task_id,
                affected_count=len(affected_tasks),
            )

            return affected_tasks

    async def get_dependency_graph(self) -> dict[str, list[str]]:
        """Get the full dependency graph for debugging/visualization."""
        async with self._lock:
            return {
                task_id: list(deps) for task_id, deps in self._task_dependencies.items()
            }

    async def get_tasks_waiting_for(self, task_id: str) -> list[str]:
        """Get list of tasks waiting for a specific task to complete."""
        async with self._lock:
            return list(self._waiting_for.get(task_id, []))

    async def clear_task(self, task_id: str) -> None:
        """Remove a task from the dependency manager."""
        async with self._lock:
            # Remove from dependencies
            if task_id in self._task_dependencies:
                del self._task_dependencies[task_id]

            # Remove from waiting_for reverse index
            for dep_id in list(self._waiting_for.keys()):
                self._waiting_for[dep_id].discard(task_id)

            # Remove from status cache
            if task_id in self._task_status_cache:
                del self._task_status_cache[task_id]


class DependencyManagerFactory:
    """Factory for creating DependencyManager instances."""

    _instance: Optional[DependencyManager] = None

    @classmethod
    def get_instance(cls) -> DependencyManager:
        """Get or create the singleton DependencyManager instance."""
        if cls._instance is None:
            cls._instance = DependencyManager()
        return cls._instance

    @classmethod
    def create(cls) -> DependencyManager:
        """Create a new DependencyManager instance (for testing)."""
        return DependencyManager()
