"""
Task Formulator - Transforms Natural Language to Program Definition Language (tasks).
Follows Single Responsibility Principle.
Supports dynamic task generation based on expert outputs.
"""

import uuid
from typing import Optional, Any
import structlog

from src.core.interfaces import (
    ITaskFormulator,
    ILLMClient,
    Task,
    AgentType,
    TaskPriority,
)


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
                        "enum": [
                            "code_writer",
                            "architect",
                            "code_quality",
                            "tester_whitebox",
                            "tester_blackbox",
                            "cicd",
                        ],
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "depends_on": {"type": "integer"},  # Index of parent task (0-based)
                    "dependencies": {"type": "array", "items": {"type": "integer"}},
                    "dependency_task_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },  # Direct task ID dependencies
                    "payload": {"type": "object"},
                    "file_path": {
                        "type": "string"
                    },  # Output file path for code_writer tasks
                },
                "required": ["title", "description", "agent_type"],
            },
        }
    },
    "required": ["tasks"],
}


class TaskFormulator(ITaskFormulator):
    """
    Transforms natural language input from PO/PM into structured tasks.
    Uses LLM to understand intent and break down work into agent-executable tasks.

    Supports:
    - Initial task generation from user request
    - Dynamic task generation based on expert outputs (progressive planning)
    - Task generation with explicit dependency injection
    """

    SYSTEM_PROMPT = """You are a task formulator for a software development AI system.
Your job is to analyze user requests and break them down into specific, actionable tasks.

Available agent types and their capabilities:
- architect: Designs system architecture, creates technical specifications, defines project structure. Does NOT run tests or write code. ALWAYS runs FIRST with CRITICAL priority.
- code_writer: Writes code, implements features, creates files, modifies existing code. Must depend on architect tasks.
- code_quality: Analyzes code quality, runs linters, suggests improvements, checks standards. Requires file_path. Must depend on code_writer tasks.
- tester_whitebox: Creates unit tests, runs existing tests, tests internal code paths. For running tests, include action: "run_tests" and test_path in payload.
- tester_blackbox: Creates end-to-end tests, tests APIs, validates user flows
- cicd: Sets up CI/CD pipelines, configures deployments, manages build processes. Runs after code is written.

CRITICAL ORDERING RULES - Tasks must follow this execution order:
1. ARCHITECT tasks (priority: critical) - Define structure and design FIRST
2. CODE_WRITER tasks (priority: high) - Implement code AFTER architecture is defined, MUST depend on architect task
3. TESTER tasks (priority: medium) - Test AFTER code is written, MUST depend on code_writer tasks
4. CODE_QUALITY tasks (priority: medium) - Review AFTER code is written, MUST depend on code_writer tasks
5. CICD tasks (priority: low) - Setup pipelines AFTER code and tests are ready

Rules:
1. Break complex requests into smaller, specific tasks
2. Assign each task to the most appropriate agent
3. ALWAYS create an architect task first with priority "critical" to define structure
4. Code_writer tasks MUST have depends_on pointing to the architect task index
5. Set dependencies between tasks when order matters using task array index (0-based)
6. Provide clear descriptions with enough context
7. Include relevant payload data (file paths, specifications, etc.)
8. IMPORTANT: For code_writer tasks, always specify the exact output file_path (e.g., /workspace/main.py)
9. IMPORTANT: For code_quality tasks, always specify the file_path of the file to analyze
10. IMPORTANT: For tester_whitebox tasks that RUN tests (not create), include action: "run_tests" and test_path: "/workspace/tests/" in payload
11. Only create files that are actual deliverables - no intermediate or debug files
12. Use meaningful, descriptive file names that reflect the module purpose
"""

    def __init__(self, llm_client: ILLMClient):
        self._llm_client = llm_client
        self._logger = logger.bind(component="TaskFormulator")

    async def formulate(self, natural_language_input: str) -> list[Task]:
        """Transform natural language input to structured tasks."""
        self._logger.info("Formulating tasks", input_length=len(natural_language_input))

        prompt = f"""Create tasks for this request: {natural_language_input}

Available agents: architect, code_writer, code_quality, tester_whitebox, tester_blackbox, cicd

CRITICAL ORDERING - Tasks MUST be in this order:
1. ARCHITECT task FIRST (priority: "critical") - Define project structure and design
2. CODE_WRITER tasks (priority: "high", depends_on: 0) - Implement code, MUST depend on architect task
3. TESTER tasks (priority: "medium", depends_on: code_writer index) - Write tests AFTER code
4. CODE_QUALITY tasks (priority: "medium", depends_on: code_writer index) - Review AFTER code  
5. CICD tasks (priority: "low", depends_on: last code task) - Setup pipelines last

IMPORTANT RULES:
- The FIRST task MUST be an architect task with priority "critical"
- ALL code_writer tasks MUST have "depends_on": 0 (depends on architect task)
- For code_writer tasks, include "file_path" with exact output path
- One task should produce ONE file

Return JSON with tasks array. Example showing proper ordering:
{{"tasks": [
  {{"title": "Design project architecture", "description": "Define the overall structure, components, and file layout", "agent_type": "architect", "priority": "critical"}},
  {{"title": "Create main entry point", "description": "Create the main.py with program entry", "agent_type": "code_writer", "priority": "high", "file_path": "/workspace/main.py", "depends_on": 0}},
  {{"title": "Write unit tests", "description": "Create tests for main module", "agent_type": "code_writer", "priority": "medium", "file_path": "/workspace/tests/test_main.py", "depends_on": 1}}
]}}"""

        try:
            result = await self._llm_client.generate_structured(prompt, TASK_SCHEMA)
            self._logger.debug("LLM result", result=result)
            tasks = self._parse_tasks(result, natural_language_input)
            self._logger.info("Tasks formulated", count=len(tasks))
            return tasks
        except Exception as e:
            self._logger.error(
                "Failed to formulate tasks, using fallback", error=str(e)
            )
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
                generated_path = self._generate_file_path(
                    raw_task.get("title", ""), raw_task.get("description", "")
                )
                if generated_path:
                    payload["file_path"] = generated_path
                    self._logger.info(
                        "Generated file_path for task",
                        title=raw_task.get("title"),
                        path=generated_path,
                    )

            # Validate: code_quality tasks require file_path
            if agent_type == AgentType.CODE_QUALITY and not payload.get("file_path"):
                self._logger.warning(
                    "code_quality task missing file_path, skipping",
                    task_title=raw_task.get("title"),
                )
                continue  # Skip invalid code_quality tasks

            task = Task(
                id=task_id,
                title=raw_task.get("title", f"Task {idx + 1}"),
                description=raw_task.get("description")
                or raw_task.get("title", original_input),
                agent_type=agent_type,
                priority=priority,
                payload=payload,
                metadata={
                    "original_input": original_input,
                    "task_index": idx,
                    # Support both depends_on (single int) and dependencies (array)
                    "dependencies": self._get_dependencies(raw_task),
                },
            )
            tasks.append(task)

        # Enforce proper ordering: architect -> code_writer -> testers/quality -> cicd
        tasks = self._enforce_task_ordering(tasks, task_index_map)

        # Resolve dependencies (convert indices to task IDs)
        for task in tasks:
            dep_indices = task.metadata.get("dependencies", [])
            if dep_indices and len(dep_indices) > 0:
                # Set parent task (first dependency)
                parent_idx = dep_indices[0]
                if parent_idx in task_index_map:
                    task.parent_task_id = task_index_map[parent_idx]

        return tasks

    def _get_dependencies(self, raw_task: dict) -> list:
        """Extract dependencies from raw task, supporting both formats."""
        # Try depends_on first (single int)
        depends_on = raw_task.get("depends_on")
        if depends_on is not None:
            return [depends_on] if isinstance(depends_on, int) else []
        # Fall back to dependencies array
        return raw_task.get("dependencies", [])

    def _enforce_task_ordering(self, tasks: list, task_index_map: dict) -> list:
        """Ensure proper execution order: architect first, then code, then tests."""
        architect_task_id = None
        code_writer_task_ids = []

        # Find architect and code_writer tasks
        for task in tasks:
            if task.agent_type == AgentType.ARCHITECT:
                # Architect tasks should be CRITICAL priority
                task.priority = TaskPriority.CRITICAL
                if not architect_task_id:
                    architect_task_id = task.id
            elif task.agent_type == AgentType.CODE_WRITER:
                # Code writer tasks should be HIGH priority and depend on architect
                if task.priority.value in ("low", "medium"):
                    task.priority = TaskPriority.HIGH
                code_writer_task_ids.append(task.id)
                # Make code_writer depend on first architect task if no dependency set
                if not task.parent_task_id and architect_task_id:
                    task.parent_task_id = architect_task_id
                    self._logger.debug(
                        "Added architect dependency to code_writer task",
                        task_id=task.id,
                    )
            elif task.agent_type in (
                AgentType.TESTER_WHITEBOX,
                AgentType.TESTER_BLACKBOX,
                AgentType.CODE_QUALITY,
            ):
                # Testers and quality tasks should be MEDIUM and depend on code
                if task.priority.value == "low":
                    task.priority = TaskPriority.MEDIUM
            elif task.agent_type == AgentType.CICD:
                # CICD should be LOW priority (runs last)
                task.priority = TaskPriority.LOW

        return tasks

    def _generate_file_path(self, title: str, description: str) -> str:
        """Generate a sensible file path from task title and description."""
        import re

        text = f"{title} {description}".lower()

        # Check for explicit file paths first
        path_match = re.search(r"/workspace/[\w/.-]+\.\w+", text)
        if path_match:
            return path_match.group(0)

        # Map common patterns to standard filenames
        filename_patterns = {
            ("main", "entry"): "main.py",
            ("scraper",): "scraper.py",
            ("parser",): "parser.py",
            ("utils", "utility", "helper"): "utils.py",
            ("config", "settings"): "config.py",
            ("test", "scraper"): "test_scraper.py",
            ("test", "parser"): "test_parser.py",
            ("test", "utils"): "test_utils.py",
            ("unit test", "scraper"): "test_scraper.py",
            ("unit test", "parser"): "test_parser.py",
            ("integration test",): "test_integration.py",
        }

        for keywords, filename in filename_patterns.items():
            if all(kw in text for kw in keywords):
                return f"/workspace/{filename}"

        # Extract meaningful words for filename
        # Remove common action/filler words
        clean = text
        for word in [
            "create",
            "write",
            "implement",
            "build",
            "add",
            "make",
            "the",
            "a",
            "an",
            "for",
            "with",
            "logic",
            "code",
            "file",
        ]:
            clean = re.sub(rf"\b{word}\b", "", clean)

        # Extract remaining meaningful words
        words = re.findall(r"\b[a-z]{3,}\b", clean)

        if words:
            # Take up to 3 most meaningful words
            meaningful = [w for w in words if w not in ["task", "new", "based"]][:3]
            if meaningful:
                filename = "_".join(meaningful)
                return f"/workspace/{filename}.py"

        # Last resort
        return "/workspace/output.py"

    def _create_fallback_task(self, input_text: str) -> Task:
        """Create a fallback task when parsing fails."""
        # Detect likely agent type from keywords
        text_lower = input_text.lower()
        if any(
            kw in text_lower
            for kw in ["write", "create", "code", "script", "function", "class", ".py"]
        ):
            agent_type = AgentType.CODE_WRITER
        elif any(kw in text_lower for kw in ["test", "unittest", "pytest"]):
            agent_type = AgentType.TESTER_WHITEBOX
        elif any(kw in text_lower for kw in ["design", "architect", "structure"]):
            agent_type = AgentType.ARCHITECT
        else:
            agent_type = AgentType.CODE_WRITER  # Default to code writer

        return Task(
            id=str(uuid.uuid4()),
            title="Process Request",
            description=input_text,
            agent_type=agent_type,
            priority=TaskPriority.MEDIUM,
            metadata={"fallback": True},
        )

    async def formulate_from_expert_output(
        self,
        original_request: str,
        expert_output: Any,
        expert_agent_type: AgentType,
        target_agent_type: AgentType,
        dependency_task_ids: Optional[list[str]] = None,
    ) -> list[Task]:
        """
        Generate tasks based on expert output.

        This enables progressive planning where:
        1. Architect produces architecture -> generates code_writer tasks
        2. Code_writer produces code -> generates tester tasks

        Args:
            original_request: The original user request
            expert_output: Output from the expert agent
            expert_agent_type: Type of agent that produced the output
            target_agent_type: Type of agent for new tasks
            dependency_task_ids: Task IDs that new tasks should depend on
        """
        self._logger.info(
            "Formulating tasks from expert output",
            expert_type=expert_agent_type.value,
            target_type=target_agent_type.value,
        )

        prompt = self._build_expert_based_prompt(
            original_request, expert_output, expert_agent_type, target_agent_type
        )

        try:
            result = await self._llm_client.generate_structured(prompt, TASK_SCHEMA)
            tasks = self._parse_tasks_with_dependencies(
                result, original_request, dependency_task_ids or [], target_agent_type
            )

            # Mark tasks as generated by expert
            for task in tasks:
                task.metadata["generated_from_expert"] = expert_agent_type.value

            self._logger.info(
                "Tasks formulated from expert output",
                count=len(tasks),
                expert_type=expert_agent_type.value,
            )
            return tasks
        except Exception as e:
            self._logger.error(
                "Failed to formulate tasks from expert output", error=str(e)
            )
            return []

    def _build_expert_based_prompt(
        self,
        original_request: str,
        expert_output: Any,
        expert_agent_type: AgentType,
        target_agent_type: AgentType,
    ) -> str:
        """Build prompt for task generation based on expert output."""

        if (
            expert_agent_type == AgentType.ARCHITECT
            and target_agent_type == AgentType.CODE_WRITER
        ):
            return self._build_architect_to_code_prompt(original_request, expert_output)
        elif expert_agent_type == AgentType.CODE_WRITER and target_agent_type in (
            AgentType.TESTER_WHITEBOX,
            AgentType.TESTER_BLACKBOX,
        ):
            return self._build_code_to_test_prompt(
                original_request, expert_output, target_agent_type
            )
        elif (
            expert_agent_type == AgentType.CODE_WRITER
            and target_agent_type == AgentType.CODE_QUALITY
        ):
            return self._build_code_to_quality_prompt(original_request, expert_output)
        else:
            return self._build_generic_expert_prompt(
                original_request, expert_output, expert_agent_type, target_agent_type
            )

    def _build_architect_to_code_prompt(
        self, original_request: str, architect_output: Any
    ) -> str:
        """Build prompt for generating code tasks from architect output."""
        files_info = ""
        if isinstance(architect_output, dict):
            files = architect_output.get("files", [])
            files_info = "\n".join(
                [f"- {f.get('path', 'unknown')}: {f.get('purpose', '')}" for f in files]
            )
            components = architect_output.get("components", [])
            entry_point = architect_output.get("entry_point", "/workspace/main.py")
        else:
            files_info = str(architect_output)
            components = []
            entry_point = "/workspace/main.py"

        return f"""Based on the architecture designed by the architect, create implementation tasks.

ORIGINAL REQUEST:
{original_request}

ARCHITECTURE OUTPUT:
Files to create:
{files_info}

Components: {', '.join(components) if components else 'Not specified'}
Entry Point: {entry_point}

Generate code_writer tasks to implement this architecture. Rules:
1. One task per file
2. Each task MUST include file_path
3. All tasks should be for agent_type: code_writer
4. Priority should be HIGH
5. Tasks that depend on shared modules should be ordered correctly

Return JSON with tasks array."""

    def _build_code_to_test_prompt(
        self, original_request: str, code_output: Any, target_type: AgentType
    ) -> str:
        """Build prompt for generating test tasks from code output."""
        files_created = []
        if isinstance(code_output, dict):
            if code_output.get("file_path"):
                files_created.append(code_output["file_path"])

        test_type = (
            "unit" if target_type == AgentType.TESTER_WHITEBOX else "integration/e2e"
        )
        agent_name = target_type.value

        return f"""Based on the code that was written, create testing tasks.

ORIGINAL REQUEST:
{original_request}

CODE FILES CREATED:
{chr(10).join(files_created) if files_created else 'Files created during implementation'}

Generate {test_type} testing tasks. Rules:
1. agent_type should be: {agent_name}
2. For each main code file, create a corresponding test task
3. Include file_path for test files (e.g., /workspace/tests/test_*.py)
4. Priority should be MEDIUM
5. For running tests, include action: "run_tests" in payload

Return JSON with tasks array."""

    def _build_code_to_quality_prompt(
        self, original_request: str, code_output: Any
    ) -> str:
        """Build prompt for generating code quality tasks from code output."""
        files_created = []
        if isinstance(code_output, dict):
            if code_output.get("file_path"):
                files_created.append(code_output["file_path"])

        return f"""Based on the code that was written, create code quality review tasks.

ORIGINAL REQUEST:
{original_request}

CODE FILES TO REVIEW:
{chr(10).join(files_created) if files_created else 'Files created during implementation'}

Generate code_quality tasks. Rules:
1. agent_type should be: code_quality
2. Each task MUST include file_path of file to review
3. Priority should be MEDIUM
4. One task per file to review

Return JSON with tasks array."""

    def _build_generic_expert_prompt(
        self,
        original_request: str,
        expert_output: Any,
        expert_type: AgentType,
        target_type: AgentType,
    ) -> str:
        """Build generic prompt for expert-to-agent task generation."""
        return f"""Based on the output from {expert_type.value}, create tasks for {target_type.value}.

ORIGINAL REQUEST:
{original_request}

EXPERT OUTPUT:
{expert_output}

Generate tasks for agent_type: {target_type.value}
Each task should include appropriate file_path where relevant.

Return JSON with tasks array."""

    def _parse_tasks_with_dependencies(
        self,
        result: dict,
        original_input: str,
        dependency_task_ids: list[str],
        expected_agent_type: AgentType,
    ) -> list[Task]:
        """Parse tasks and assign explicit dependencies."""
        tasks = []
        raw_tasks = result.get("tasks", [])

        for idx, raw_task in enumerate(raw_tasks):
            task_id = str(uuid.uuid4())

            # Parse agent type (override if not matching expected)
            agent_type_str = raw_task.get("agent_type", expected_agent_type.value)
            try:
                agent_type = AgentType(agent_type_str)
            except ValueError:
                agent_type = expected_agent_type

            # Parse priority
            priority_str = raw_task.get("priority", "medium")
            try:
                priority = TaskPriority(priority_str)
            except ValueError:
                priority = TaskPriority.MEDIUM

            # Build payload
            payload = raw_task.get("payload", {})
            if raw_task.get("file_path"):
                payload["file_path"] = raw_task["file_path"]

            # Create task with explicit dependencies
            task = Task(
                id=task_id,
                title=raw_task.get("title", f"Task {idx + 1}"),
                description=raw_task.get("description")
                or raw_task.get("title", original_input),
                agent_type=agent_type,
                priority=priority,
                payload=payload,
                dependency_ids=list(
                    dependency_task_ids
                ),  # All new tasks depend on previous expert
                generated_by_task_id=(
                    dependency_task_ids[0] if dependency_task_ids else None
                ),
                metadata={
                    "original_input": original_input,
                    "task_index": idx,
                },
            )
            tasks.append(task)

        return tasks

    async def formulate_initial_architect_task(self, request: str) -> Task:
        """
        Create only the initial architect task.
        Used for progressive planning where only architect runs first.
        """
        task_id = str(uuid.uuid4())

        return Task(
            id=task_id,
            title="Design project architecture",
            description=f"Design the architecture for: {request}",
            agent_type=AgentType.ARCHITECT,
            priority=TaskPriority.CRITICAL,
            payload={"action": "design", "request": request},
            metadata={"original_request": request, "is_initial_task": True},
        )


class TaskFormulatorFactory:
    """Factory for creating TaskFormulator instances."""

    @staticmethod
    def create(llm_client: ILLMClient) -> TaskFormulator:
        """Create a TaskFormulator instance."""
        return TaskFormulator(llm_client)
