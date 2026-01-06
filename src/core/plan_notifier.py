"""
Plan Notifier - Broadcasts task plans to connected clients.
Enables clients to see what actions are planned.
"""

import asyncio
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import structlog

from src.core.interfaces import Task, AgentType, TaskPriority


logger = structlog.get_logger()


@dataclass
class TaskPlanItem:
    """A single item in the task plan."""
    id: str
    title: str
    description: str
    agent_type: str
    priority: str
    depends_on: Optional[str] = None
    estimated_time: Optional[str] = None  # e.g., "~30s"
    status: str = "pending"


@dataclass
class ExecutionPlan:
    """A plan for executing a prompt."""
    project_id: str
    prompt: str
    tasks: list[TaskPlanItem]
    created_at: datetime = field(default_factory=datetime.now)
    architect_output: Optional[dict] = None  # Architecture design if any


class PlanNotifier:
    """
    Notifies clients about task execution plans.
    
    When a prompt is analyzed, the plan is broadcast to all connected clients
    so they can see:
    1. What tasks will be executed
    2. Which agent handles each task
    3. Task dependencies and order
    4. Progress updates
    """
    
    def __init__(self):
        self._broadcast_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._current_plans: dict[str, ExecutionPlan] = {}
        self._logger = logger.bind(component="PlanNotifier")
    
    def set_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set callback for broadcasting to clients."""
        self._broadcast_callback = callback
    
    async def notify_plan_created(
        self,
        project_id: str,
        prompt: str,
        tasks: list[Task],
        architect_output: Optional[dict] = None
    ) -> None:
        """Notify clients about a new execution plan."""
        plan_items = []
        for task in tasks:
            plan_items.append(TaskPlanItem(
                id=task.id,
                title=task.title,
                description=task.description,
                agent_type=task.agent_type.value,
                priority=task.priority.value,
                depends_on=task.parent_task_id,
                estimated_time=self._estimate_time(task)
            ))
        
        plan = ExecutionPlan(
            project_id=project_id,
            prompt=prompt,
            tasks=plan_items,
            architect_output=architect_output
        )
        self._current_plans[project_id] = plan
        
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "plan_created",
                "project_id": project_id,
                "prompt": prompt,
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        "agent_type": t.agent_type,
                        "priority": t.priority,
                        "depends_on": t.depends_on,
                        "estimated_time": t.estimated_time,
                        "status": t.status
                    }
                    for t in plan_items
                ],
                "architect_output": architect_output,
                "total_tasks": len(plan_items)
            })
            self._logger.info(
                "Plan created notification sent",
                project_id=project_id,
                task_count=len(plan_items)
            )
    
    async def notify_task_started(self, project_id: str, task_id: str) -> None:
        """Notify clients that a task has started."""
        plan = self._current_plans.get(project_id)
        task_title = ""
        agent_type = ""
        if plan:
            for task in plan.tasks:
                if task.id == task_id:
                    task.status = "running"
                    task_title = task.title
                    agent_type = task.agent_type
                    break
        
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "task_started",
                "project_id": project_id,
                "task_id": task_id,
                "title": task_title,
                "agent_type": agent_type
            })
    
    async def notify_task_completed(
        self, 
        project_id: str, 
        task_id: str,
        output: Optional[dict] = None
    ) -> None:
        """Notify clients that a task completed."""
        plan = self._current_plans.get(project_id)
        task_title = ""
        agent_type = ""
        if plan:
            for task in plan.tasks:
                if task.id == task_id:
                    task.status = "completed"
                    task_title = task.title
                    agent_type = task.agent_type
                    break
        
        # Extract file_path from output if available
        file_created = None
        if output and isinstance(output, dict):
            file_created = output.get("file_path")
        
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "task_completed",
                "project_id": project_id,
                "task_id": task_id,
                "title": task_title,
                "agent_type": agent_type,
                "file_created": file_created,
                "output_summary": self._summarize_output(output) if output else None
            })
    
    async def notify_task_failed(
        self,
        project_id: str,
        task_id: str,
        error: str
    ) -> None:
        """Notify clients that a task failed."""
        plan = self._current_plans.get(project_id)
        task_title = ""
        agent_type = ""
        if plan:
            for task in plan.tasks:
                if task.id == task_id:
                    task.status = "failed"
                    task_title = task.title
                    agent_type = task.agent_type
                    break
        
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "task_failed",
                "project_id": project_id,
                "task_id": task_id,
                "title": task_title,
                "agent_type": agent_type,
                "error": error[:500]  # Limit error length
            })
    
    async def notify_new_tasks(
        self,
        project_id: str,
        tasks: list[Task],
        reason: str = "Follow-up tasks generated"
    ) -> None:
        """Notify clients about new tasks added to the plan."""
        plan = self._current_plans.get(project_id)
        new_items = []
        
        for task in tasks:
            item = TaskPlanItem(
                id=task.id,
                title=task.title,
                description=task.description,
                agent_type=task.agent_type.value,
                priority=task.priority.value,
                depends_on=task.parent_task_id,
                estimated_time=self._estimate_time(task)
            )
            new_items.append(item)
            if plan:
                plan.tasks.append(item)
        
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "tasks_added",
                "project_id": project_id,
                "reason": reason,
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        "agent_type": t.agent_type,
                        "priority": t.priority,
                        "depends_on": t.depends_on,
                        "status": t.status
                    }
                    for t in new_items
                ]
            })
    
    def get_plan(self, project_id: str) -> Optional[ExecutionPlan]:
        """Get the current plan for a project."""
        return self._current_plans.get(project_id)
    
    def _estimate_time(self, task: Task) -> str:
        """Estimate execution time based on task type."""
        estimates = {
            AgentType.ARCHITECT: "~10s",
            AgentType.CODE_WRITER: "~15s",
            AgentType.CODE_QUALITY: "~8s",
            AgentType.TESTER_WHITEBOX: "~12s",
            AgentType.TESTER_BLACKBOX: "~20s",
            AgentType.CICD: "~30s"
        }
        return estimates.get(task.agent_type, "~10s")
    
    def _summarize_output(self, output: dict) -> dict:
        """Create a brief summary of task output."""
        summary = {}
        if isinstance(output, dict):
            for key, value in output.items():
                if isinstance(value, str) and len(value) > 200:
                    summary[key] = value[:200] + "..."
                elif isinstance(value, list):
                    summary[key] = f"[{len(value)} items]"
                elif isinstance(value, dict):
                    summary[key] = "{...}"
                else:
                    summary[key] = value
        return summary
    
    async def notify_operation(
        self,
        project_id: str,
        operation: str,
        details: str,
        level: str = "info"  # info, warning, debug
    ) -> None:
        """
        Notify clients about a system operation.
        
        Operations include:
        - llm_call: Calling LLM for generation
        - planning: Planning architecture or tasks
        - analyzing: Analyzing failure or output
        - generating: Generating fix or retry tasks
        - writing_file: Writing a file
        - running_test: Running tests
        - validating: Validating code quality
        """
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "operation",
                "project_id": project_id,
                "operation": operation,
                "details": details,
                "level": level
            })
    
    async def notify_file_operation(
        self,
        project_id: str,
        action: str,  # create, modify, delete
        file_path: str,
        size: Optional[int] = None
    ) -> None:
        """Notify clients about a file operation."""
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "file_operation",
                "project_id": project_id,
                "action": action,
                "file_path": file_path,
                "size": size
            })
    
    async def notify_llm_call(
        self,
        project_id: str,
        purpose: str,  # e.g., "Planning architecture", "Generating code"
        model: Optional[str] = None
    ) -> None:
        """Notify clients that an LLM call is being made."""
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "llm_call",
                "project_id": project_id,
                "purpose": purpose,
                "model": model
            })
    
    async def notify_agent_processing(
        self,
        project_id: str,
        agent_type: str,
        action: str,  # e.g., "Generating code", "Running tests"
        target: Optional[str] = None  # e.g., file path
    ) -> None:
        """Notify clients that an agent is processing something."""
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "agent_processing",
                "project_id": project_id,
                "agent_type": agent_type,
                "action": action,
                "target": target
            })
    
    async def notify_analysis(
        self,
        project_id: str,
        analysis_type: str,  # e.g., "failure_analysis", "code_review"
        summary: str
    ) -> None:
        """Notify clients about an analysis being performed."""
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "analysis",
                "project_id": project_id,
                "analysis_type": analysis_type,
                "summary": summary
            })
    
    async def notify_retry_scheduled(
        self,
        project_id: str,
        original_task: str,
        fix_task: str,
        reason: str
    ) -> None:
        """Notify clients that a retry has been scheduled after a fix."""
        if self._broadcast_callback:
            await self._broadcast_callback({
                "type": "retry_scheduled",
                "project_id": project_id,
                "original_task": original_task,
                "fix_task": fix_task,
                "reason": reason
            })
    
    def cleanup(self, project_id: str) -> None:
        """Clean up plan data for a completed project."""
        self._current_plans.pop(project_id, None)


# Global singleton
_plan_notifier: Optional[PlanNotifier] = None


def get_plan_notifier() -> PlanNotifier:
    """Get the global PlanNotifier instance."""
    global _plan_notifier
    if _plan_notifier is None:
        _plan_notifier = PlanNotifier()
    return _plan_notifier
