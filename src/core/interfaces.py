"""
Core interfaces following SOLID principles.
- Interface Segregation Principle: Small, focused interfaces
- Dependency Inversion Principle: Depend on abstractions, not concretions
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
from enum import Enum
from pydantic import BaseModel


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class TaskPriority(str, Enum):
    """Task priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentType(str, Enum):
    """Types of agents in the workforce."""

    CODE_WRITER = "code_writer"
    ARCHITECT = "architect"
    CODE_QUALITY = "code_quality"
    TESTER_WHITEBOX = "tester_whitebox"
    TESTER_BLACKBOX = "tester_blackbox"
    CICD = "cicd"


class Task(BaseModel):
    """Domain model for a task in the system."""

    id: str
    title: str
    description: str
    agent_type: AgentType
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    payload: dict = {}
    parent_task_id: Optional[str] = (
        None  # Legacy: single parent (deprecated, use dependency_ids)
    )
    dependency_ids: list[str] = []  # Task IDs this task depends on (all must complete)
    generated_by_task_id: Optional[str] = (
        None  # Task that triggered generation of this task
    )
    metadata: dict = {}


class TaskResult(BaseModel):
    """Domain model for task execution result."""

    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[str] = None
    metadata: dict = {}


class IMessagePublisher(ABC):
    """Interface for publishing messages (ISP)."""

    @abstractmethod
    async def publish(self, topic: str, message: dict) -> None:
        """Publish a message to a topic."""
        pass


class IMessageSubscriber(ABC):
    """Interface for subscribing to messages (ISP)."""

    @abstractmethod
    async def subscribe(self, topic: str, callback) -> None:
        """Subscribe to a topic with a callback handler."""
        pass

    @abstractmethod
    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic."""
        pass


class IMessageBus(IMessagePublisher, IMessageSubscriber):
    """Combined interface for full message bus functionality."""

    pass


class IAgent(ABC):
    """Interface for all agents in the workforce (ISP)."""

    @property
    @abstractmethod
    def agent_type(self) -> AgentType:
        """Return the agent type."""
        pass

    @abstractmethod
    async def execute(self, task: Task) -> TaskResult:
        """Execute a task and return the result."""
        pass

    @abstractmethod
    async def validate_task(self, task: Task) -> bool:
        """Validate if the agent can handle the task."""
        pass


class ITaskFormulator(ABC):
    """Interface for formulating tasks from natural language."""

    @abstractmethod
    async def formulate(self, natural_language_input: str) -> list[Task]:
        """Transform natural language to structured tasks."""
        pass


class IRuleEnforcer(ABC):
    """Interface for validating and enforcing task rules."""

    @abstractmethod
    async def validate(self, task: Task) -> tuple[bool, Optional[str]]:
        """Validate a task against rules. Returns (is_valid, rejection_reason)."""
        pass

    @abstractmethod
    async def request_amendment(self, task: Task, reason: str) -> Task:
        """Request task amendment with the given reason."""
        pass


class ITaskScheduler(ABC):
    """Interface for scheduling tasks."""

    @abstractmethod
    async def schedule(self, task: Task) -> None:
        """Schedule a task for execution."""
        pass

    @abstractmethod
    async def get_pending_tasks(self) -> list[Task]:
        """Get all pending tasks."""
        pass

    @abstractmethod
    async def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update task status."""
        pass


class IResultInterpreter(ABC):
    """Interface for interpreting task results."""

    @abstractmethod
    async def interpret(self, result: TaskResult) -> dict:
        """Interpret a task result and determine next actions."""
        pass

    @abstractmethod
    async def complete_task(self, result: TaskResult) -> None:
        """Mark a task as complete based on result."""
        pass


class IFileReader(ABC):
    """Interface for read access (ISP)."""

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Read file content."""
        pass

    @abstractmethod
    async def list_directory(self, path: str) -> list[str]:
        """List directory contents."""
        pass

    @abstractmethod
    async def file_exists(self, path: str) -> bool:
        """Check if file exists."""
        pass


class IFileWriter(ABC):
    """Interface for write access (ISP)."""

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """Write content to file."""
        pass

    @abstractmethod
    async def delete_file(self, path: str) -> None:
        """Delete a file."""
        pass

    @abstractmethod
    async def create_directory(self, path: str) -> None:
        """Create a directory."""
        pass


class ICommandExecutor(ABC):
    """Interface for command execution (ISP)."""

    @abstractmethod
    def set_context(
        self, agent_type: str, task_id: str, project_id: Optional[str] = None
    ) -> None:
        """Set execution context for approval requests."""
        pass

    @abstractmethod
    async def execute_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        reason: str = "Agent requested command execution",
    ) -> tuple[str, str, int]:
        """Execute a shell command. Returns (stdout, stderr, return_code)."""
        pass


class ILLMClient(ABC):
    """Interface for LLM interactions."""

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate text from a prompt."""
        pass

    @abstractmethod
    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        """Generate structured output matching the schema."""
        pass


class IDependencyManager(ABC):
    """Interface for managing task dynamic dependencies."""

    @abstractmethod
    async def add_dependency(self, task_id: str, dependency_id: str) -> None:
        """Add a dependency to an existing task. The task will wait for this dependency."""
        pass

    @abstractmethod
    async def remove_dependency(self, task_id: str, dependency_id: str) -> None:
        """Remove a dependency from a task."""
        pass

    @abstractmethod
    async def get_unmet_dependencies(self, task_id: str) -> list[str]:
        """Get list of dependency IDs that are not yet completed."""
        pass

    @abstractmethod
    async def are_all_dependencies_met(self, task_id: str) -> bool:
        """Check if all dependencies for a task are completed."""
        pass

    @abstractmethod
    async def inject_tasks_as_dependencies(
        self, new_tasks: list["Task"], target_task_ids: list[str]
    ) -> None:
        """
        Inject new tasks as dependencies of existing tasks.
        The new tasks become prerequisites for the target tasks.
        """
        pass


class IProjectAdvancementContext(ABC):
    """
    Interface for tracking project advancement based on expert outputs.
    Enables progressive task generation where expert outputs inform subsequent tasks.
    """

    @abstractmethod
    async def store_expert_output(
        self, project_id: str, agent_type: AgentType, task_id: str, output: Any
    ) -> None:
        """Store output from an expert agent for future task generation."""
        pass

    @abstractmethod
    async def get_expert_outputs(
        self, project_id: str, agent_types: Optional[list[AgentType]] = None
    ) -> dict[AgentType, list[dict]]:
        """Get stored expert outputs, optionally filtered by agent types."""
        pass

    @abstractmethod
    async def get_context_for_task_generation(
        self, project_id: str, target_agent_type: AgentType
    ) -> dict:
        """
        Get relevant context for generating tasks for a specific agent type.
        Returns outputs from prerequisite experts (e.g., architect for code_writer).
        """
        pass

    @abstractmethod
    async def should_generate_follow_up_tasks(
        self, project_id: str, completed_agent_type: AgentType
    ) -> list[AgentType]:
        """
        Determine which agent types should have tasks generated after an expert completes.
        Example: After architect completes, code_writer tasks should be generated.
        """
        pass
