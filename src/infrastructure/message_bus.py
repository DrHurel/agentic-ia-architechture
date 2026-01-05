"""
Message Bus implementation - routes messages between components.
Follows Single Responsibility Principle and Dependency Inversion Principle.
"""

import asyncio
from typing import Callable, Optional
import structlog

from src.core.interfaces import IMessageBus, Task, TaskResult, AgentType
from src.core.base import BaseService
from .kafka import KafkaProducer, KafkaConsumer, KafkaAdmin
from src.core.config import KafkaSettings


logger = structlog.get_logger()


# Topic definitions
class Topics:
    """Kafka topic constants."""
    # Task topics
    TASK_NEW = "task.new"
    TASK_STATUS = "task.status"
    TASK_RESULT = "task.result"
    
    # Agent-specific topics
    AGENT_CODE_WRITER = "agent.code_writer"
    AGENT_ARCHITECT = "agent.architect"
    AGENT_CODE_QUALITY = "agent.code_quality"
    AGENT_TESTER_WHITEBOX = "agent.tester_whitebox"
    AGENT_TESTER_BLACKBOX = "agent.tester_blackbox"
    AGENT_CICD = "agent.cicd"
    
    # Feedback topics
    USER_FEEDBACK = "user.feedback"
    
    @classmethod
    def get_agent_topic(cls, agent_type: AgentType) -> str:
        """Get the topic for a specific agent type."""
        mapping = {
            AgentType.CODE_WRITER: cls.AGENT_CODE_WRITER,
            AgentType.ARCHITECT: cls.AGENT_ARCHITECT,
            AgentType.CODE_QUALITY: cls.AGENT_CODE_QUALITY,
            AgentType.TESTER_WHITEBOX: cls.AGENT_TESTER_WHITEBOX,
            AgentType.TESTER_BLACKBOX: cls.AGENT_TESTER_BLACKBOX,
            AgentType.CICD: cls.AGENT_CICD,
        }
        return mapping.get(agent_type, cls.TASK_NEW)
    
    @classmethod
    def all_topics(cls) -> list[str]:
        """Get all topic names."""
        return [
            cls.TASK_NEW, cls.TASK_STATUS, cls.TASK_RESULT,
            cls.AGENT_CODE_WRITER, cls.AGENT_ARCHITECT,
            cls.AGENT_CODE_QUALITY, cls.AGENT_TESTER_WHITEBOX,
            cls.AGENT_TESTER_BLACKBOX, cls.AGENT_CICD,
            cls.USER_FEEDBACK
        ]


class MessageBus(IMessageBus, BaseService):
    """
    Central message bus for routing messages between system components.
    Acts as a facade over Kafka for simplified messaging.
    """
    
    def __init__(self, settings: KafkaSettings, group_id: Optional[str] = None):
        super().__init__("MessageBus")
        self._settings = settings
        self._producer = KafkaProducer(settings)
        self._consumer = KafkaConsumer(settings, group_id)
        self._admin = KafkaAdmin(settings)
        self._result_callbacks: dict[str, Callable] = {}
    
    async def _on_start(self) -> None:
        """Initialize Kafka connections and create topics."""
        self._admin.connect()
        self._admin.create_topics(Topics.all_topics())
        self._producer.connect()
        self._consumer.connect()
    
    async def _on_stop(self) -> None:
        """Clean up Kafka connections."""
        self._consumer.disconnect()
        self._producer.disconnect()
    
    async def publish(self, topic: str, message: dict) -> None:
        """Publish a message to a topic."""
        await self._producer.publish(topic, message)
    
    async def subscribe(self, topic: str, callback: Callable) -> None:
        """Subscribe to a topic with a callback."""
        await self._consumer.subscribe(topic, callback)
    
    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic."""
        await self._consumer.unsubscribe(topic)
    
    async def publish_task(self, task: Task) -> None:
        """Publish a task to the appropriate agent topic."""
        topic = Topics.get_agent_topic(task.agent_type)
        await self.publish(topic, task.model_dump())
        self._logger.info("Task published", task_id=task.id, topic=topic)
    
    async def publish_result(self, result: TaskResult) -> None:
        """Publish a task result."""
        await self.publish(Topics.TASK_RESULT, result.model_dump())
        self._logger.info("Result published", task_id=result.task_id)
    
    async def publish_feedback(self, feedback: dict) -> None:
        """Publish user feedback."""
        await self.publish(Topics.USER_FEEDBACK, feedback)
    
    async def start_consuming(self) -> None:
        """Start the message consumption loop."""
        await self._consumer.start_consuming()
    
    def stop_consuming(self) -> None:
        """Stop message consumption."""
        self._consumer.stop_consuming()


class MessageBusFactory:
    """Factory for creating message bus instances."""
    
    @staticmethod
    def create(settings: KafkaSettings, group_id: Optional[str] = None) -> MessageBus:
        """Create a new MessageBus instance."""
        return MessageBus(settings, group_id)
    
    @staticmethod
    def create_for_agent(settings: KafkaSettings, agent_type: AgentType) -> MessageBus:
        """Create a MessageBus configured for a specific agent."""
        group_id = f"agent-{agent_type.value}"
        return MessageBus(settings, group_id)
