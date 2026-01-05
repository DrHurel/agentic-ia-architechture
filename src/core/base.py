"""
Base classes providing common functionality.
Follows Open/Closed Principle - open for extension, closed for modification.
"""

import asyncio
import structlog
from abc import ABC
from typing import Optional

from .interfaces import (
    IAgent, AgentType, Task, TaskResult, TaskStatus,
    IMessagePublisher, ILLMClient
)


logger = structlog.get_logger()


class BaseAgent(IAgent, ABC):
    """
    Base class for all agents providing common functionality.
    Follows Template Method pattern for task execution.
    """
    
    def __init__(
        self,
        message_publisher: IMessagePublisher,
        llm_client: ILLMClient,
        agent_name: Optional[str] = None
    ):
        self._message_publisher = message_publisher
        self._llm_client = llm_client
        self._name = agent_name or self.__class__.__name__
        self._logger = logger.bind(agent=self._name)
    
    @property
    def name(self) -> str:
        return self._name
    
    async def execute(self, task: Task) -> TaskResult:
        """
        Template method for task execution.
        Subclasses should override _do_execute for specific logic.
        """
        self._logger.info("Starting task execution", task_id=task.id, task_title=task.title)
        
        try:
            # Validate task
            if not await self.validate_task(task):
                return TaskResult(
                    task_id=task.id,
                    status=TaskStatus.REJECTED,
                    error=f"Task validation failed for agent {self._name}"
                )
            
            # Pre-execution hook
            await self._pre_execute(task)
            
            # Execute the task
            result = await self._do_execute(task)
            
            # Post-execution hook
            await self._post_execute(task, result)
            
            self._logger.info("Task completed", task_id=task.id, status=result.status)
            return result
            
        except Exception as e:
            self._logger.error("Task execution failed", task_id=task.id, error=str(e))
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def validate_task(self, task: Task) -> bool:
        """Validate that this agent can handle the task."""
        return task.agent_type == self.agent_type
    
    async def _pre_execute(self, task: Task) -> None:
        """Hook called before task execution. Override for custom behavior."""
        await self._publish_status(task.id, TaskStatus.IN_PROGRESS)
    
    async def _post_execute(self, task: Task, result: TaskResult) -> None:
        """Hook called after task execution. Override for custom behavior."""
        await self._publish_result(result)
    
    async def _do_execute(self, task: Task) -> TaskResult:
        """
        Core execution logic. Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement _do_execute")
    
    async def _publish_status(self, task_id: str, status: TaskStatus) -> None:
        """Publish task status update."""
        await self._message_publisher.publish(
            "task.status",
            {"task_id": task_id, "status": status.value, "agent": self._name}
        )
    
    async def _publish_result(self, result: TaskResult) -> None:
        """Publish task result to message bus."""
        await self._message_publisher.publish(
            "task.result",
            result.model_dump()
        )
    
    async def _ask_llm(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Helper method to interact with LLM."""
        return await self._llm_client.generate(prompt, system_prompt)


class BaseService(ABC):
    """Base class for services with common lifecycle management."""
    
    def __init__(self, name: str):
        self._name = name
        self._logger = logger.bind(service=name)
        self._running = False
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    async def start(self) -> None:
        """Start the service."""
        self._logger.info("Starting service")
        await self._on_start()
        self._running = True
        self._logger.info("Service started")
    
    async def stop(self) -> None:
        """Stop the service."""
        self._logger.info("Stopping service")
        self._running = False
        await self._on_stop()
        self._logger.info("Service stopped")
    
    async def _on_start(self) -> None:
        """Hook called on service start. Override for custom behavior."""
        pass
    
    async def _on_stop(self) -> None:
        """Hook called on service stop. Override for custom behavior."""
        pass
