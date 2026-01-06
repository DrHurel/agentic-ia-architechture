"""
Task Scheduler - Manages task queue and scheduling.
Follows Single Responsibility Principle.
Supports dynamic multi-dependency task management.
"""

import asyncio
from datetime import datetime
from typing import Optional
from collections import defaultdict
import structlog

from src.core.interfaces import (
    ITaskScheduler,
    IMessagePublisher,
    Task,
    TaskStatus,
    TaskPriority,
)
from src.infrastructure.message_bus import Topics
from src.services.prompt_analyser.dependency_manager import (
    DependencyManager,
    DependencyManagerFactory,
)


logger = structlog.get_logger()


class TaskScheduler(ITaskScheduler):
    """
    Schedules tasks for execution by agents.
    Handles priorities, multi-dependencies, and task queuing.

    Supports:
    - Multiple dependencies per task (all must complete before task starts)
    - Dynamic dependency injection during runtime
    - Backward compatibility with legacy parent_task_id
    """

    def __init__(
        self,
        message_publisher: IMessagePublisher,
        dependency_manager: Optional[DependencyManager] = None,
    ):
        self._message_publisher = message_publisher
        self._logger = logger.bind(component="TaskScheduler")

        # In-memory task storage (in production, use a database)
        self._tasks: dict[str, Task] = {}
        self._pending_queue: list[str] = []  # Task IDs
        self._waiting_for_dependency: dict[str, set[str]] = defaultdict(
            set
        )  # dependency_id -> {task_ids waiting}
        self._lock = asyncio.Lock()

        # Dependency manager for advanced dependency tracking
        self._dependency_manager = (
            dependency_manager or DependencyManagerFactory.get_instance()
        )
        self._dependency_manager.set_status_lookup(self._get_task_status_sync)

    def _get_task_status_sync(self, task_id: str) -> Optional[TaskStatus]:
        """Synchronous task status lookup for dependency manager."""
        task = self._tasks.get(task_id)
        return task.status if task else None

    async def schedule(self, task: Task) -> None:
        """
        Schedule a task for execution.
        Tasks with dependencies wait until ALL dependencies complete.
        """
        should_dispatch = False
        async with self._lock:
            self._tasks[task.id] = task

            # Register task with dependency manager
            await self._dependency_manager.register_task(task)

            # Get all dependencies (both legacy parent_task_id and new dependency_ids)
            all_dependencies = set(task.dependency_ids)
            if task.parent_task_id and task.parent_task_id != task.id:
                all_dependencies.add(task.parent_task_id)

            # Check for circular self-dependency
            all_dependencies.discard(task.id)

            if all_dependencies:
                # Check which dependencies are not yet completed
                unmet_deps = await self._get_unmet_dependencies_locked(
                    task.id, all_dependencies
                )

                if unmet_deps:
                    # Task must wait for unmet dependencies
                    for dep_id in unmet_deps:
                        self._waiting_for_dependency[dep_id].add(task.id)

                    self._logger.info(
                        "Task waiting for dependencies",
                        task_id=task.id,
                        task_title=task.title,
                        unmet_dependency_count=len(unmet_deps),
                        unmet_dependencies=list(unmet_deps)[:5],  # Limit log size
                    )
                    return

            # All dependencies met (or no dependencies) - add to queue
            self._add_to_queue_locked(task)
            should_dispatch = True

        # Dispatch OUTSIDE the lock to prevent deadlock on slow I/O
        if should_dispatch:
            await self._dispatch_task(task)

    async def _get_unmet_dependencies_locked(
        self, task_id: str, dependency_ids: set[str]
    ) -> set[str]:
        """Check which dependencies are not yet completed. Must hold lock."""
        unmet = set()
        for dep_id in dependency_ids:
            dep_task = self._tasks.get(dep_id)
            if dep_task is None or dep_task.status != TaskStatus.COMPLETED:
                unmet.add(dep_id)
        return unmet

        # Dispatch OUTSIDE the lock to prevent deadlock on slow I/O
        if should_dispatch:
            await self._dispatch_task(task)

    def _add_to_queue_locked(self, task: Task) -> None:
        """Add task to the pending queue with priority ordering. Must be called with lock held."""
        priority_order = {
            TaskPriority.CRITICAL: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 3,
        }

        # Fix tasks get inserted at the very front (before other CRITICAL tasks)
        is_fix_task = task.metadata.get("is_fix_task", False)

        if is_fix_task:
            # Insert at position 0 - fix tasks always run next
            self._pending_queue.insert(0, task.id)
            self._logger.info(
                "Fix task added to front of queue",
                task_id=task.id,
                priority=task.priority.value,
                queue_position=0,
            )
            return

        # Regular priority-based insertion
        task_priority = priority_order.get(task.priority, 2)
        insert_index = len(self._pending_queue)

        for i, tid in enumerate(self._pending_queue):
            existing_task = self._tasks.get(tid)
            if existing_task:
                # Don't insert before fix tasks
                if existing_task.metadata.get("is_fix_task", False):
                    continue
                existing_priority = priority_order.get(existing_task.priority, 2)
                if task_priority < existing_priority:
                    insert_index = i
                    break

        self._pending_queue.insert(insert_index, task.id)
        self._logger.info(
            "Task added to queue",
            task_id=task.id,
            priority=task.priority.value,
            queue_position=insert_index,
        )

    async def _dispatch_task(self, task: Task) -> None:
        """Dispatch task to the appropriate agent via message bus."""
        topic = Topics.get_agent_topic(task.agent_type)
        await self._message_publisher.publish(topic, task.model_dump())
        self._logger.info("Task dispatched", task_id=task.id, topic=topic)

    async def get_pending_tasks(self) -> list[Task]:
        """Get all pending tasks in priority order."""
        async with self._lock:
            return [
                self._tasks[tid] for tid in self._pending_queue if tid in self._tasks
            ]

    async def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update task status and handle dependent tasks."""
        tasks_to_dispatch = []

        async with self._lock:
            if task_id not in self._tasks:
                self._logger.warning(
                    "Task not found for status update", task_id=task_id
                )
                return

            task = self._tasks[task_id]
            task.status = status

            self._logger.info(
                "Task status updated", task_id=task_id, status=status.value
            )

            # Remove from pending queue if completed or failed
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.REJECTED):
                if task_id in self._pending_queue:
                    self._pending_queue.remove(task_id)

                # Handle dependent tasks
                if status == TaskStatus.COMPLETED:
                    tasks_to_dispatch = await self._release_dependent_tasks_locked(
                        task_id
                    )
                elif status == TaskStatus.FAILED:
                    # Cascade failure to dependent tasks to prevent deadlock
                    await self._fail_dependent_tasks_locked(task_id)

        # Dispatch released tasks OUTSIDE the lock to prevent deadlock on slow I/O
        for dep_task in tasks_to_dispatch:
            await self._dispatch_task(dep_task)

        # Notify dependency manager of status change
        if status == TaskStatus.COMPLETED:
            await self._dependency_manager.on_task_completed(task_id)
        elif status == TaskStatus.FAILED:
            await self._dependency_manager.on_task_failed(task_id)

    async def _fail_dependent_tasks_locked(self, failed_task_id: str) -> None:
        """Mark dependent tasks as failed when parent fails. Must be called with lock held."""
        dependent_ids = set(self._waiting_for_dependency.pop(failed_task_id, set()))

        for dep_id in dependent_ids:
            dep_task = self._tasks.get(dep_id)
            if dep_task:
                self._logger.info(
                    "Failing dependent task due to parent failure",
                    task_id=dep_id,
                    failed_parent=failed_task_id,
                )
                dep_task.status = TaskStatus.FAILED
                # Recursively fail any grandchildren
                await self._fail_dependent_tasks_locked(dep_id)

    async def _release_dependent_tasks_locked(
        self, completed_task_id: str
    ) -> list[Task]:
        """
        Release tasks that were waiting for the completed task.
        Only releases tasks when ALL their dependencies are met.
        Must be called with lock held.
        Returns list of tasks to dispatch (dispatch should happen outside lock).
        """
        dependent_ids = set(self._waiting_for_dependency.pop(completed_task_id, set()))
        tasks_to_dispatch = []

        for dep_id in dependent_ids:
            dep_task = self._tasks.get(dep_id)
            if dep_task:
                # Check if ALL dependencies are now met
                all_deps = set(dep_task.dependency_ids)
                if dep_task.parent_task_id:
                    all_deps.add(dep_task.parent_task_id)
                all_deps.discard(dep_task.id)  # Remove self-reference

                unmet = await self._get_unmet_dependencies_locked(dep_id, all_deps)

                if len(unmet) == 0:
                    # All dependencies met - can dispatch
                    self._logger.info(
                        "All dependencies met, releasing task",
                        task_id=dep_id,
                        completed_dependency=completed_task_id,
                    )
                    self._add_to_queue_locked(dep_task)
                    tasks_to_dispatch.append(dep_task)
                else:
                    self._logger.debug(
                        "Task still has unmet dependencies",
                        task_id=dep_id,
                        completed_dependency=completed_task_id,
                        remaining_dependencies=len(unmet),
                    )

        return tasks_to_dispatch

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    async def inject_dependency(self, task_id: str, dependency_id: str) -> bool:
        """
        Add a new dependency to an existing task.
        Returns True if successful, False if task not found or already started.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                self._logger.warning(
                    "Cannot inject dependency: task not found",
                    task_id=task_id,
                    dependency_id=dependency_id,
                )
                return False

            # Don't inject into already running/completed tasks
            if task.status in (
                TaskStatus.IN_PROGRESS,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            ):
                self._logger.warning(
                    "Cannot inject dependency: task already started or completed",
                    task_id=task_id,
                    task_status=task.status.value,
                )
                return False

            # Add to task's dependency list
            if dependency_id not in task.dependency_ids:
                task.dependency_ids.append(dependency_id)

            # Update waiting_for_dependency tracking
            self._waiting_for_dependency[dependency_id].add(task_id)

            # Remove from pending queue if currently queued
            if task_id in self._pending_queue:
                self._pending_queue.remove(task_id)
                self._logger.info(
                    "Task moved back to waiting (new dependency injected)",
                    task_id=task_id,
                    new_dependency=dependency_id,
                )

            await self._dependency_manager.add_dependency(task_id, dependency_id)

            self._logger.info(
                "Dependency injected into task",
                task_id=task_id,
                dependency_id=dependency_id,
                total_dependencies=len(task.dependency_ids),
            )
            return True

    async def inject_tasks_as_dependencies(
        self, new_tasks: list[Task], target_task_ids: list[str]
    ) -> None:
        """
        Schedule new tasks and inject them as dependencies of target tasks.
        Target tasks will wait until new tasks complete.

        Use case: Architect generates additional design tasks that code_writer
        tasks must wait for.
        """
        # Schedule the new tasks first
        for task in new_tasks:
            await self.schedule(task)

        # Inject as dependencies into target tasks
        async with self._lock:
            for target_id in target_task_ids:
                for new_task in new_tasks:
                    target_task = self._tasks.get(target_id)
                    if target_task and target_task.status == TaskStatus.PENDING:
                        if new_task.id not in target_task.dependency_ids:
                            target_task.dependency_ids.append(new_task.id)
                        self._waiting_for_dependency[new_task.id].add(target_id)

                        # Remove from queue if currently pending
                        if target_id in self._pending_queue:
                            self._pending_queue.remove(target_id)

        await self._dependency_manager.inject_tasks_as_dependencies(
            new_tasks, target_task_ids
        )

        self._logger.info(
            "Tasks injected as dependencies",
            new_task_count=len(new_tasks),
            target_task_count=len(target_task_ids),
        )

    async def get_tasks_by_agent_type(self, agent_type) -> list[Task]:
        """Get all tasks for a specific agent type."""
        async with self._lock:
            return [
                task for task in self._tasks.values() if task.agent_type == agent_type
            ]

    async def get_pending_tasks_by_agent_type(self, agent_type) -> list[Task]:
        """Get pending tasks for a specific agent type."""
        async with self._lock:
            return [
                task
                for task in self._tasks.values()
                if task.agent_type == agent_type and task.status == TaskStatus.PENDING
            ]

    async def get_queue_stats(self) -> dict:
        """Get statistics about the task queue."""
        async with self._lock:
            stats = {
                "total_tasks": len(self._tasks),
                "pending": len(self._pending_queue),
                "waiting_for_dependency": sum(
                    len(v) for v in self._waiting_for_dependency.values()
                ),
                "by_status": defaultdict(int),
                "by_priority": defaultdict(int),
                "by_agent": defaultdict(int),
            }

            for task in self._tasks.values():
                stats["by_status"][task.status.value] += 1
                stats["by_priority"][task.priority.value] += 1
                stats["by_agent"][task.agent_type.value] += 1

            return dict(stats)

    async def recover_stuck_tasks(self) -> dict:
        """
        Attempt to recover from stuck state by re-dispatching pending tasks.

        This handles cases where:
        1. Tasks are in pending queue but never dispatched
        2. Tasks are waiting for dependencies that already completed
        3. Tasks are in unknown/stale state

        Returns recovery report.
        """
        recovery_report = {
            "recovered_tasks": [],
            "failed_recoveries": [],
            "orphaned_dependencies_resolved": [],
            "stale_tasks_reset": [],
        }

        tasks_to_dispatch = []

        async with self._lock:
            # 1. Check for tasks waiting on already-completed dependencies
            orphaned_deps = []
            for parent_id, waiting_ids in list(self._waiting_for_dependency.items()):
                parent_task = self._tasks.get(parent_id)
                if parent_task and parent_task.status == TaskStatus.COMPLETED:
                    # Parent already completed, release waiting tasks
                    orphaned_deps.append((parent_id, waiting_ids))
                elif not parent_task:
                    # Parent doesn't exist, release waiting tasks
                    orphaned_deps.append((parent_id, waiting_ids))

            for parent_id, waiting_ids in orphaned_deps:
                self._waiting_for_dependency.pop(parent_id, None)
                for wait_id in waiting_ids:
                    wait_task = self._tasks.get(wait_id)
                    if wait_task and wait_task.status not in (
                        TaskStatus.COMPLETED,
                        TaskStatus.FAILED,
                    ):
                        self._add_to_queue_locked(wait_task)
                        tasks_to_dispatch.append(wait_task)
                        recovery_report["orphaned_dependencies_resolved"].append(
                            {
                                "task_id": wait_id,
                                "title": wait_task.title,
                                "was_waiting_for": parent_id[:8] + "...",
                            }
                        )

            # 2. Re-dispatch pending tasks that may have been lost
            for task_id in list(self._pending_queue):
                task = self._tasks.get(task_id)
                if task and task.status not in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.REJECTED,
                ):
                    # Check if task should be re-dispatched
                    if task.status == TaskStatus.PENDING:
                        tasks_to_dispatch.append(task)
                        recovery_report["recovered_tasks"].append(
                            {
                                "task_id": task_id,
                                "title": task.title,
                                "agent_type": task.agent_type.value,
                            }
                        )

            # 3. Find stale tasks (not pending, not completed, not in queue)
            for task_id, task in self._tasks.items():
                if (
                    task_id not in self._pending_queue
                    and task.status == TaskStatus.IN_PROGRESS
                ):
                    # Task was in progress but never completed - reset and re-dispatch
                    task.status = TaskStatus.PENDING
                    self._add_to_queue_locked(task)
                    tasks_to_dispatch.append(task)
                    recovery_report["stale_tasks_reset"].append(
                        {
                            "task_id": task_id,
                            "title": task.title,
                            "agent_type": task.agent_type.value,
                        }
                    )

        # Dispatch recovered tasks OUTSIDE the lock
        for task in tasks_to_dispatch:
            try:
                await self._dispatch_task(task)
                self._logger.info("Recovered and dispatched task", task_id=task.id)
            except Exception as e:
                self._logger.error(
                    "Failed to dispatch recovered task", task_id=task.id, error=str(e)
                )
                recovery_report["failed_recoveries"].append(
                    {"task_id": task.id, "title": task.title, "error": str(e)}
                )

        self._logger.info(
            "Task recovery completed",
            recovered=len(recovery_report["recovered_tasks"]),
            orphans_resolved=len(recovery_report["orphaned_dependencies_resolved"]),
            stale_reset=len(recovery_report["stale_tasks_reset"]),
            failed=len(recovery_report["failed_recoveries"]),
        )

        return recovery_report


class TaskSchedulerFactory:
    """Factory for creating TaskScheduler instances."""

    @staticmethod
    def create(
        message_publisher: IMessagePublisher,
        dependency_manager: Optional[DependencyManager] = None,
    ) -> TaskScheduler:
        """Create a TaskScheduler instance."""
        return TaskScheduler(message_publisher, dependency_manager)
