"""
Agent Runner - Base class for running agents as services.
"""

import asyncio
import structlog

from src.core.base import BaseService
from src.core.config import AppSettings
from src.core.interfaces import IAgent, Task, AgentType
from src.infrastructure.message_bus import MessageBus, Topics
from src.infrastructure.llm_client import LlamaClient


logger = structlog.get_logger()


class AgentRunner(BaseService):
    """
    Service wrapper for running an agent.
    Handles message subscription and task dispatching.
    """

    def __init__(self, settings: AppSettings, agent: IAgent):
        super().__init__(f"AgentRunner-{agent.name}")
        self._settings = settings
        self._agent = agent
        self._message_bus: MessageBus = None

    async def _on_start(self) -> None:
        """Initialize message bus and subscribe to agent topic."""
        group_id = f"agent-{self._agent.agent_type.value}"
        self._message_bus = MessageBus(self._settings.kafka, group_id)
        await self._message_bus.start()

        # Subscribe to agent-specific topic
        topic = Topics.get_agent_topic(self._agent.agent_type)
        await self._message_bus.subscribe(topic, self._handle_task)

        self._logger.info("Agent runner started", agent=self._agent.name, topic=topic)

    async def _on_stop(self) -> None:
        """Clean up resources."""
        self._message_bus.stop_consuming()
        await self._message_bus.stop()

    async def _notify_operation(
        self, project_id: str, operation: str, details: str
    ) -> None:
        """Send an operation notification to the client."""
        await self._message_bus.publish_feedback(
            {
                "type": "operation",
                "project_id": project_id,
                "operation": operation,
                "details": details,
            }
        )

    async def _notify_agent_processing(
        self, project_id: str, action: str, target: str = None
    ) -> None:
        """Notify that this agent is processing something."""
        await self._message_bus.publish_feedback(
            {
                "type": "agent_processing",
                "project_id": project_id,
                "agent_type": self._agent.agent_type.value,
                "action": action,
                "target": target,
            }
        )

    async def _notify_file_operation(
        self, project_id: str, action: str, file_path: str, size: int = None
    ) -> None:
        """Notify about a file operation."""
        await self._message_bus.publish_feedback(
            {
                "type": "file_operation",
                "project_id": project_id,
                "action": action,
                "file_path": file_path,
                "size": size,
            }
        )

    async def _notify_llm_call(self, project_id: str, purpose: str) -> None:
        """Notify that the agent is calling the LLM."""
        await self._message_bus.publish_feedback(
            {"type": "llm_call", "project_id": project_id, "purpose": purpose}
        )

    async def _handle_task(self, message: dict) -> None:
        """Handle incoming task message."""
        task = None
        try:
            task = Task(**message)
            project_id = task.payload.get("project_id", "")

            self._logger.info("Received task", task_id=task.id, title=task.title)

            # Notify task started
            await self._notify_agent_processing(
                project_id=project_id, action=f"Starting: {task.title}"
            )

            # Execute the task
            result = await self._agent.execute(task)

            # Notify about file creation if applicable
            if result.output and isinstance(result.output, dict):
                file_path = result.output.get("file_path")
                if file_path:
                    await self._notify_file_operation(
                        project_id=project_id,
                        action="create",
                        file_path=file_path,
                        size=result.output.get("code_length"),
                    )

            # Publish result
            await self._message_bus.publish_result(result)

        except Exception as e:
            self._logger.error("Failed to handle task", error=str(e), message=message)

            # Always publish a failure result to prevent tasks from getting stuck
            if task:
                from src.core.interfaces import TaskResult, TaskStatus

                error_result = TaskResult(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    error=f"Agent error: {str(e)}",
                )
                try:
                    await self._message_bus.publish_result(error_result)
                    self._logger.info(
                        "Published failure result for task", task_id=task.id
                    )
                except Exception as pub_err:
                    self._logger.error(
                        "Failed to publish failure result", error=str(pub_err)
                    )

    async def run(self) -> None:
        """Run the agent service."""
        await self.start()
        try:
            await self._message_bus.start_consuming()
        except KeyboardInterrupt:
            self._logger.info("Shutting down agent...")
        finally:
            await self.stop()
