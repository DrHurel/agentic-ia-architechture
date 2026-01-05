"""
Task Scheduler - Manages task queue and scheduling.
Follows Single Responsibility Principle.
"""

import asyncio
from datetime import datetime
from typing import Optional
from collections import defaultdict
import structlog

from src.core.interfaces import ITaskScheduler, IMessagePublisher, Task, TaskStatus, TaskPriority
from src.infrastructure.message_bus import Topics


logger = structlog.get_logger()


class TaskScheduler(ITaskScheduler):
    """
    Schedules tasks for execution by agents.
    Handles priorities, dependencies, and task queuing.
    """
    
    def __init__(self, message_publisher: IMessagePublisher):
        self._message_publisher = message_publisher
        self._logger = logger.bind(component="TaskScheduler")
        
        # In-memory task storage (in production, use a database)
        self._tasks: dict[str, Task] = {}
        self._pending_queue: list[str] = []  # Task IDs
        self._waiting_for_dependency: dict[str, list[str]] = defaultdict(list)  # parent_id -> [child_ids]
        self._lock = asyncio.Lock()
    
    async def schedule(self, task: Task) -> None:
        """
        Schedule a task for execution.
        Tasks with dependencies wait until parent completes.
        """
        async with self._lock:
            self._tasks[task.id] = task
            
            # Check if task has dependencies
            if task.parent_task_id:
                parent_task = self._tasks.get(task.parent_task_id)
                if parent_task and parent_task.status != TaskStatus.COMPLETED:
                    # Wait for parent to complete
                    self._waiting_for_dependency[task.parent_task_id].append(task.id)
                    self._logger.info(
                        "Task waiting for dependency",
                        task_id=task.id,
                        parent_id=task.parent_task_id
                    )
                    return
            
            # Add to pending queue based on priority
            await self._add_to_queue(task)
    
    async def _add_to_queue(self, task: Task) -> None:
        """Add task to the pending queue with priority ordering."""
        priority_order = {
            TaskPriority.CRITICAL: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 3
        }
        
        # Insert at appropriate position based on priority
        task_priority = priority_order.get(task.priority, 2)
        insert_index = len(self._pending_queue)
        
        for i, tid in enumerate(self._pending_queue):
            existing_task = self._tasks.get(tid)
            if existing_task:
                existing_priority = priority_order.get(existing_task.priority, 2)
                if task_priority < existing_priority:
                    insert_index = i
                    break
        
        self._pending_queue.insert(insert_index, task.id)
        self._logger.info(
            "Task added to queue",
            task_id=task.id,
            priority=task.priority.value,
            queue_position=insert_index
        )
        
        # Dispatch the task
        await self._dispatch_task(task)
    
    async def _dispatch_task(self, task: Task) -> None:
        """Dispatch task to the appropriate agent via message bus."""
        topic = Topics.get_agent_topic(task.agent_type)
        await self._message_publisher.publish(topic, task.model_dump())
        self._logger.info("Task dispatched", task_id=task.id, topic=topic)
    
    async def get_pending_tasks(self) -> list[Task]:
        """Get all pending tasks in priority order."""
        async with self._lock:
            return [
                self._tasks[tid]
                for tid in self._pending_queue
                if tid in self._tasks
            ]
    
    async def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update task status and handle dependent tasks."""
        async with self._lock:
            if task_id not in self._tasks:
                self._logger.warning("Task not found for status update", task_id=task_id)
                return
            
            task = self._tasks[task_id]
            task.status = status
            
            self._logger.info("Task status updated", task_id=task_id, status=status.value)
            
            # Remove from pending queue if completed or failed
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.REJECTED):
                if task_id in self._pending_queue:
                    self._pending_queue.remove(task_id)
                
                # Handle dependent tasks
                if status == TaskStatus.COMPLETED:
                    await self._release_dependent_tasks(task_id)
    
    async def _release_dependent_tasks(self, completed_task_id: str) -> None:
        """Release tasks that were waiting for the completed task."""
        dependent_ids = self._waiting_for_dependency.pop(completed_task_id, [])
        
        for dep_id in dependent_ids:
            dep_task = self._tasks.get(dep_id)
            if dep_task:
                self._logger.info(
                    "Releasing dependent task",
                    task_id=dep_id,
                    completed_parent=completed_task_id
                )
                await self._add_to_queue(dep_task)
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    async def get_queue_stats(self) -> dict:
        """Get statistics about the task queue."""
        async with self._lock:
            stats = {
                "total_tasks": len(self._tasks),
                "pending": len(self._pending_queue),
                "waiting_for_dependency": sum(len(v) for v in self._waiting_for_dependency.values()),
                "by_status": defaultdict(int),
                "by_priority": defaultdict(int),
                "by_agent": defaultdict(int)
            }
            
            for task in self._tasks.values():
                stats["by_status"][task.status.value] += 1
                stats["by_priority"][task.priority.value] += 1
                stats["by_agent"][task.agent_type.value] += 1
            
            return dict(stats)


class TaskSchedulerFactory:
    """Factory for creating TaskScheduler instances."""
    
    @staticmethod
    def create(message_publisher: IMessagePublisher) -> TaskScheduler:
        """Create a TaskScheduler instance."""
        return TaskScheduler(message_publisher)
