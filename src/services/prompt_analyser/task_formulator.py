"""
Task Formulator - Transforms Natural Language to Program Definition Language (tasks).
Follows Single Responsibility Principle.
"""

import uuid
from typing import Optional
import structlog

from src.core.interfaces import ITaskFormulator, ILLMClient, Task, AgentType, TaskPriority


logger = structlog.get_logger()


# JSON schema for task generation
TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "agent_type": {
                        "type": "string",
                        "enum": ["code_writer", "architect", "code_quality", 
                                "tester_whitebox", "tester_blackbox", "cicd"]
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"]
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "integer"}
                    },
                    "payload": {"type": "object"}
                },
                "required": ["title", "description", "agent_type"]
            }
        }
    },
    "required": ["tasks"]
}


class TaskFormulator(ITaskFormulator):
    """
    Transforms natural language input from PO/PM into structured tasks.
    Uses LLM to understand intent and break down work into agent-executable tasks.
    """
    
    SYSTEM_PROMPT = """You are a task formulator for a software development AI system.
Your job is to analyze user requests and break them down into specific, actionable tasks.

Available agent types and their capabilities:
- architect: Designs system architecture, creates technical specifications, defines project structure
- code_writer: Writes code, implements features, creates files, modifies existing code
- code_quality: Analyzes code quality, runs linters, suggests improvements, checks standards
- tester_whitebox: Creates unit tests, writes integration tests, tests internal code paths
- tester_blackbox: Creates end-to-end tests, tests APIs, validates user flows
- cicd: Sets up CI/CD pipelines, configures deployments, manages build processes

Rules:
1. Break complex requests into smaller, specific tasks
2. Assign each task to the most appropriate agent
3. Set dependencies between tasks when order matters
4. Provide clear descriptions with enough context
5. Include relevant payload data (file paths, specifications, etc.)
"""
    
    def __init__(self, llm_client: ILLMClient):
        self._llm_client = llm_client
        self._logger = logger.bind(component="TaskFormulator")
    
    async def formulate(self, natural_language_input: str) -> list[Task]:
        """Transform natural language input to structured tasks."""
        self._logger.info("Formulating tasks", input_length=len(natural_language_input))
        
        prompt = f"""Analyze this request and create a list of tasks:

Request: {natural_language_input}

Create specific, actionable tasks that can be executed by the agents.
Return a JSON object with a "tasks" array."""
        
        try:
            result = await self._llm_client.generate_structured(prompt, TASK_SCHEMA)
            tasks = self._parse_tasks(result, natural_language_input)
            self._logger.info("Tasks formulated", count=len(tasks))
            return tasks
        except Exception as e:
            self._logger.error("Failed to formulate tasks", error=str(e))
            # Fallback: create a single generic task
            return [self._create_fallback_task(natural_language_input)]
    
    def _parse_tasks(self, result: dict, original_input: str) -> list[Task]:
        """Parse LLM result into Task objects."""
        tasks = []
        task_index_map = {}  # Map index to task_id for dependencies
        
        raw_tasks = result.get("tasks", [])
        
        for idx, raw_task in enumerate(raw_tasks):
            task_id = str(uuid.uuid4())
            task_index_map[idx] = task_id
            
            # Parse agent type
            agent_type_str = raw_task.get("agent_type", "code_writer")
            try:
                agent_type = AgentType(agent_type_str)
            except ValueError:
                agent_type = AgentType.CODE_WRITER
            
            # Parse priority
            priority_str = raw_task.get("priority", "medium")
            try:
                priority = TaskPriority(priority_str)
            except ValueError:
                priority = TaskPriority.MEDIUM
            
            task = Task(
                id=task_id,
                title=raw_task.get("title", f"Task {idx + 1}"),
                description=raw_task.get("description") or raw_task.get("title", original_input),
                agent_type=agent_type,
                priority=priority,
                payload=raw_task.get("payload", {}),
                metadata={
                    "original_input": original_input,
                    "task_index": idx,
                    "dependencies": raw_task.get("dependencies", [])
                }
            )
            tasks.append(task)
        
        # Resolve dependencies (convert indices to task IDs)
        for task in tasks:
            dep_indices = task.metadata.get("dependencies", [])
            if dep_indices and len(dep_indices) > 0:
                # Set parent task (first dependency)
                parent_idx = dep_indices[0]
                if parent_idx in task_index_map:
                    task.parent_task_id = task_index_map[parent_idx]
        
        return tasks
    
    def _create_fallback_task(self, input_text: str) -> Task:
        """Create a fallback task when parsing fails."""
        return Task(
            id=str(uuid.uuid4()),
            title="Process Request",
            description=input_text,
            agent_type=AgentType.ARCHITECT,  # Architect can analyze and delegate
            priority=TaskPriority.MEDIUM,
            metadata={"fallback": True}
        )


class TaskFormulatorFactory:
    """Factory for creating TaskFormulator instances."""
    
    @staticmethod
    def create(llm_client: ILLMClient) -> TaskFormulator:
        """Create a TaskFormulator instance."""
        return TaskFormulator(llm_client)
