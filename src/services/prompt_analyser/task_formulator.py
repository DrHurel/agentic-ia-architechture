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
                    "payload": {"type": "object"},
                    "file_path": {"type": "string"}  # Output file path for code_writer tasks
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
6. IMPORTANT: For code_writer tasks, always specify the exact output file_path (e.g., /workspace/main.py)
7. IMPORTANT: For code_quality tasks, always specify the file_path of the file to analyze (must be a file created by a previous code_writer task)
8. Only create files that are actual deliverables - no intermediate or debug files
9. Use meaningful, descriptive file names that reflect the module purpose
10. code_quality tasks MUST depend on the code_writer task that creates the file they analyze
"""
    
    def __init__(self, llm_client: ILLMClient):
        self._llm_client = llm_client
        self._logger = logger.bind(component="TaskFormulator")
    
    async def formulate(self, natural_language_input: str) -> list[Task]:
        """Transform natural language input to structured tasks."""
        self._logger.info("Formulating tasks", input_length=len(natural_language_input))
        
        prompt = f"""Create tasks for this request: {natural_language_input}

Available agents: architect, code_writer, code_quality, tester_whitebox, tester_blackbox, cicd

IMPORTANT RULES:
- For code_writer tasks, you MUST include "file_path" with the exact output path (e.g., "/workspace/calculator.py")
- Only create files that are actual project deliverables
- Use descriptive file names that match the feature (not the task name)
- One task should produce ONE file, not multiple

Return JSON with tasks array. Example:
{{"tasks": [{{"title": "Create main entry point", "description": "Create the main.py with program entry", "agent_type": "code_writer", "priority": "medium", "file_path": "/workspace/main.py"}}]}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, TASK_SCHEMA)
            self._logger.debug("LLM result", result=result)
            tasks = self._parse_tasks(result, natural_language_input)
            self._logger.info("Tasks formulated", count=len(tasks))
            return tasks
        except Exception as e:
            self._logger.error("Failed to formulate tasks, using fallback", error=str(e))
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
            
            # Build payload - include file_path if specified
            payload = raw_task.get("payload", {})
            if raw_task.get("file_path"):
                payload["file_path"] = raw_task["file_path"]
            
            # For code_writer tasks, ensure file_path exists
            if agent_type == AgentType.CODE_WRITER and not payload.get("file_path"):
                # Generate a file path from the task title
                generated_path = self._generate_file_path(raw_task.get("title", ""), raw_task.get("description", ""))
                if generated_path:
                    payload["file_path"] = generated_path
                    self._logger.info("Generated file_path for task", title=raw_task.get("title"), path=generated_path)
            
            # Validate: code_quality tasks require file_path
            if agent_type == AgentType.CODE_QUALITY and not payload.get("file_path"):
                self._logger.warning(
                    "code_quality task missing file_path, skipping",
                    task_title=raw_task.get("title")
                )
                continue  # Skip invalid code_quality tasks
            
            task = Task(
                id=task_id,
                title=raw_task.get("title", f"Task {idx + 1}"),
                description=raw_task.get("description") or raw_task.get("title", original_input),
                agent_type=agent_type,
                priority=priority,
                payload=payload,
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
    
    def _generate_file_path(self, title: str, description: str) -> str:
        """Generate a sensible file path from task title and description."""
        import re
        
        text = f"{title} {description}".lower()
        
        # Check for explicit file paths first
        path_match = re.search(r'/workspace/[\w/.-]+\.\w+', text)
        if path_match:
            return path_match.group(0)
        
        # Map common patterns to standard filenames
        filename_patterns = {
            ('main', 'entry'): 'main.py',
            ('scraper',): 'scraper.py',
            ('parser',): 'parser.py',
            ('utils', 'utility', 'helper'): 'utils.py',
            ('config', 'settings'): 'config.py',
            ('test', 'scraper'): 'test_scraper.py',
            ('test', 'parser'): 'test_parser.py',
            ('test', 'utils'): 'test_utils.py',
            ('unit test', 'scraper'): 'test_scraper.py',
            ('unit test', 'parser'): 'test_parser.py',
            ('integration test',): 'test_integration.py',
        }
        
        for keywords, filename in filename_patterns.items():
            if all(kw in text for kw in keywords):
                return f"/workspace/{filename}"
        
        # Extract meaningful words for filename
        # Remove common action/filler words
        clean = text
        for word in ['create', 'write', 'implement', 'build', 'add', 'make', 
                     'the', 'a', 'an', 'for', 'with', 'logic', 'code', 'file']:
            clean = re.sub(rf'\b{word}\b', '', clean)
        
        # Extract remaining meaningful words
        words = re.findall(r'\b[a-z]{3,}\b', clean)
        
        if words:
            # Take up to 3 most meaningful words
            meaningful = [w for w in words if w not in ['task', 'new', 'based']][:3]
            if meaningful:
                filename = '_'.join(meaningful)
                return f"/workspace/{filename}.py"
        
        # Last resort
        return "/workspace/output.py"
    
    def _create_fallback_task(self, input_text: str) -> Task:
        """Create a fallback task when parsing fails."""
        # Detect likely agent type from keywords
        text_lower = input_text.lower()
        if any(kw in text_lower for kw in ['write', 'create', 'code', 'script', 'function', 'class', '.py']):
            agent_type = AgentType.CODE_WRITER
        elif any(kw in text_lower for kw in ['test', 'unittest', 'pytest']):
            agent_type = AgentType.TESTER_WHITEBOX
        elif any(kw in text_lower for kw in ['design', 'architect', 'structure']):
            agent_type = AgentType.ARCHITECT
        else:
            agent_type = AgentType.CODE_WRITER  # Default to code writer
            
        return Task(
            id=str(uuid.uuid4()),
            title="Process Request",
            description=input_text,
            agent_type=agent_type,
            priority=TaskPriority.MEDIUM,
            metadata={"fallback": True}
        )


class TaskFormulatorFactory:
    """Factory for creating TaskFormulator instances."""
    
    @staticmethod
    def create(llm_client: ILLMClient) -> TaskFormulator:
        """Create a TaskFormulator instance."""
        return TaskFormulator(llm_client)
