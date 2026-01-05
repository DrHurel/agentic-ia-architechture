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
    
    async def _handle_task(self, message: dict) -> None:
        """Handle incoming task message."""
        try:
            task = Task(**message)
            self._logger.info("Received task", task_id=task.id, title=task.title)
            
            # Execute the task
            result = await self._agent.execute(task)
            
            # Publish result
            await self._message_bus.publish_result(result)
            
        except Exception as e:
            self._logger.error("Failed to handle task", error=str(e), message=message)
    
    async def run(self) -> None:
        """Run the agent service."""
        await self.start()
        try:
            await self._message_bus.start_consuming()
        except KeyboardInterrupt:
            self._logger.info("Shutting down agent...")
        finally:
            await self.stop()
