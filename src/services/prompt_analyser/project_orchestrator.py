"""
Project Orchestrator - Drives autonomous work until project completion.
Follows Open/Closed Principle - extensible completion criteria.
"""

import asyncio
from typing import Optional, Set, Dict, List, Any
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
import structlog

from src.core.interfaces import (
    ILLMClient, ITaskFormulator, IFileReader,
    Task, TaskResult, TaskStatus, TaskPriority, AgentType
)
from src.core.plan_notifier import get_plan_notifier
from src.core.agent_selector import get_agent_selector


logger = structlog.get_logger()


class ProjectStatus(str, Enum):
    """Status of the autonomous project."""
    INITIALIZING = "initializing"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    TESTING = "testing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass
class ProjectContext:
    """Context for an autonomous project."""
    id: str
    original_request: str
    status: ProjectStatus = ProjectStatus.INITIALIZING
    created_at: datetime = field(default_factory=datetime.now)
    
    # Task tracking
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    pending_task_ids: Set[str] = field(default_factory=set)  # Track which tasks belong to this project
    
    # Retry tracking: fix_task_id -> original_task_info
    pending_retries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Task registry: task_id -> task info (for retry purposes)
    task_registry: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # File tracking
    created_files: Set[str] = field(default_factory=set)
    modified_files: Set[str] = field(default_factory=set)
    
    # Validation
    tests_passed: bool = False
    code_quality_checked: bool = False
    
    # Interrupt flag
    interrupted: bool = False
    
    # Architecture plan (shared with code_writer tasks)
    architect_plan: Dict[str, Any] = field(default_factory=dict)
    
    # History
    task_history: List[Dict[str, Any]] = field(default_factory=list)
    iteration_count: int = 0
    max_iterations: int = 50  # Safety limit


class ProjectOrchestrator:
    """
    Orchestrates autonomous project execution.
    
    Workflow:
    1. Receive high-level project request
    2. Plan architecture and break into tasks
    3. Execute tasks iteratively
    4. Validate results (tests, quality)
    5. Loop until project is complete or interrupted
    """
    
    def __init__(
        self,
        llm_client: ILLMClient,
        task_formulator: ITaskFormulator,
        file_reader: IFileReader
    ):
        self._llm_client = llm_client
        self._task_formulator = task_formulator
        self._file_reader = file_reader
        self._logger = logger.bind(component="ProjectOrchestrator")
        
        # Active projects
        self._projects: Dict[str, ProjectContext] = {}
        
        # Callbacks
        self._task_dispatch_callback = None
        self._completion_callback = None
        self._status_callback = None
    
    def on_task_dispatch(self, callback) -> None:
        """Register callback for when tasks need to be dispatched."""
        self._task_dispatch_callback = callback
    
    def on_completion(self, callback) -> None:
        """Register callback for project completion."""
        self._completion_callback = callback
    
    def on_status_change(self, callback) -> None:
        """Register callback for status updates."""
        self._status_callback = callback
    
    async def start_project(self, project_id: str, request: str) -> ProjectContext:
        """
        Start a new autonomous project.
        
        Args:
            project_id: Unique project identifier
            request: High-level project request from user
            
        Returns:
            ProjectContext for tracking
        """
        self._logger.info("Starting autonomous project", project_id=project_id, request=request[:100])
        
        # Create project context
        project = ProjectContext(
            id=project_id,
            original_request=request
        )
        self._projects[project_id] = project
        
        # Phase 1: Planning
        await self._update_status(project, ProjectStatus.PLANNING)
        
        # Generate architecture plan
        architecture = await self._plan_architecture(request)
        project.architect_plan = architecture  # Store for code_writer to follow
        
        # Generate initial tasks
        initial_tasks = await self._generate_initial_tasks(request, architecture)
        
        # Optimize agent selection
        agent_selector = get_agent_selector()
        initial_tasks = agent_selector.assign_agents_with_optimization(initial_tasks, architecture)
        
        # Pass architect plan to code_writer tasks
        for task in initial_tasks:
            if task.agent_type == AgentType.CODE_WRITER:
                task.payload["architect_plan"] = architecture
            task.payload["project_id"] = project_id
        
        project.total_tasks = len(initial_tasks)
        
        # Track task IDs and info for this project
        for task in initial_tasks:
            project.pending_task_ids.add(task.id)
            # Store task info for potential retry
            self._store_task_info(project, task)
        
        # Notify clients about the plan
        plan_notifier = get_plan_notifier()
        await plan_notifier.notify_plan_created(
            project_id=project_id,
            prompt=request,
            tasks=initial_tasks,
            architect_output=architecture
        )
        
        # Phase 2: Execution
        await self._update_status(project, ProjectStatus.IN_PROGRESS)
        
        # Dispatch initial tasks
        if self._task_dispatch_callback and initial_tasks:
            await self._task_dispatch_callback(initial_tasks)
        
        return project
    
    async def _plan_architecture(self, request: str) -> Dict[str, Any]:
        """Plan the project architecture."""
        prompt = f"""You are a software architect. Analyze this project request and create an architecture plan.

PROJECT REQUEST:
{request}

Create a detailed plan including:
1. Required files and their purposes
2. Main components/modules
3. Dependencies between components
4. Testing strategy

Respond with JSON:
{{
    "project_name": "name",
    "description": "brief description",
    "files": [
        {{"path": "/workspace/file.py", "purpose": "description", "priority": 1}}
    ],
    "components": ["list of main components"],
    "dependencies": {{"component": ["depends_on"]}},
    "test_files": ["/workspace/test_file.py"],
    "entry_point": "/workspace/main.py"
}}"""
        
        try:
            architecture = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "files": {"type": "array"},
                    "components": {"type": "array"},
                    "entry_point": {"type": "string"}
                }
            })
            self._logger.info("Architecture planned", 
                            files=len(architecture.get("files", [])),
                            components=architecture.get("components", []))
            return architecture
        except Exception as e:
            self._logger.error("Architecture planning failed", error=str(e))
            return {"files": [], "components": [], "entry_point": "/workspace/main.py"}
    
    async def _generate_initial_tasks(self, request: str, architecture: Dict[str, Any]) -> List[Task]:
        """Generate the initial set of tasks based on architecture."""
        files = architecture.get("files", [])
        entry_point = architecture.get("entry_point", "/workspace/main.py")
        
        # Build a detailed prompt for task generation
        file_list = "\n".join([f"- {f.get('path', 'unknown')}: {f.get('purpose', '')}" 
                               for f in files]) if files else "No files planned yet"
        
        prompt = f"""Create implementation tasks for this project:

PROJECT REQUEST: {request}

PLANNED FILES:
{file_list}

ENTRY POINT: {entry_point}

Generate tasks to implement this project. Each file needs a task.
After implementation, add testing and validation tasks.

IMPORTANT: Generate tasks in the correct order (dependencies first).
Each task should be specific and actionable."""
        
        tasks = await self._task_formulator.formulate(prompt)
        return tasks
    
    async def handle_task_result(self, project_id: str, result: TaskResult) -> Optional[List[Task]]:
        """
        Handle a task result and determine next actions.
        
        Returns new tasks to dispatch, if any.
        """
        project = self._projects.get(project_id)
        if not project:
            self._logger.warning("Project not found for result", project_id=project_id)
            return None
        
        # Only process results for tasks that belong to this project
        if result.task_id not in project.pending_task_ids:
            return None  # Task doesn't belong to this project
        
        # Remove from pending and add to history
        project.pending_task_ids.discard(result.task_id)
        
        # Check for interrupt
        if project.interrupted:
            await self._update_status(project, ProjectStatus.INTERRUPTED)
            return None
        
        # Update counters
        project.iteration_count += 1
        
        # Get plan notifier for updates
        plan_notifier = get_plan_notifier()
        
        # Track task in history
        project.task_history.append({
            "task_id": result.task_id,
            "status": result.status.value,
            "timestamp": datetime.now().isoformat()
        })
        
        if result.status == TaskStatus.COMPLETED:
            project.completed_tasks += 1
            
            # Notify about completion
            await plan_notifier.notify_task_completed(
                project_id=project_id,
                task_id=result.task_id,
                output=result.output
            )
            
            # Track created files (only actual files, not directories)
            output = result.output or {}
            if isinstance(output, dict):
                file_path = output.get("file_path")
                if file_path:
                    # Only track actual files (not directories ending with /)
                    if not file_path.endswith("/"):
                        project.created_files.add(file_path)
                    else:
                        self._logger.debug("Skipping directory path", path=file_path)
            
            # Check if this was a fix task - if so, generate retry of original task
            if result.task_id in project.pending_retries:
                retry_info = project.pending_retries.pop(result.task_id)
                retry_tasks = await self._generate_retry_task(project, retry_info, result.output)
                if retry_tasks and self._task_dispatch_callback:
                    for task in retry_tasks:
                        project.pending_task_ids.add(task.id)
                    await self._task_dispatch_callback(retry_tasks)
                    
        else:
            project.failed_tasks += 1
            
            # Notify about failure
            await plan_notifier.notify_task_failed(
                project_id=project_id,
                task_id=result.task_id,
                error=result.error or "Unknown error"
            )
        
        self._logger.info(
            "Task result processed",
            project_id=project_id,
            completed=project.completed_tasks,
            total=project.total_tasks,
            iteration=project.iteration_count
        )
        
        # Check safety limit
        if project.iteration_count >= project.max_iterations:
            self._logger.warning("Max iterations reached", project_id=project_id)
            await self._complete_project(project, success=False, reason="Max iterations reached")
            return None
        
        # Determine next steps
        return await self._determine_next_steps(project, result)
    
    async def _determine_next_steps(self, project: ProjectContext, result: TaskResult) -> Optional[List[Task]]:
        """Determine what tasks to create next based on project state."""
        
        # Too many failures - fail the project and stop processing
        if project.failed_tasks > 5:
            self._logger.warning("Too many failures, failing project", project_id=project.id)
            # Clear pending tasks as we're giving up
            project.pending_task_ids.clear()
            await self._complete_project(project, success=False, reason="Too many task failures")
            return None
        
        # Calculate total processed tasks (both completed and failed)
        total_processed = project.completed_tasks + project.failed_tasks
        
        # If all pending tasks are done, check completion
        if len(project.pending_task_ids) == 0:
            # All tasks processed - determine final state
            if total_processed >= project.total_tasks:
                # For simple projects (few tasks), skip testing phase and complete
                is_simple_project = project.total_tasks <= 5 or len(project.created_files) <= 2
                
                # Check if we should move to testing or complete
                if not project.tests_passed and not is_simple_project:
                    # Try testing phase if we have created files (but only once)
                    if project.created_files and project.status != ProjectStatus.TESTING:
                        await self._update_status(project, ProjectStatus.TESTING)
                        return await self._generate_test_tasks(project)
                
                # Project is complete - success if most tasks succeeded
                success = project.failed_tasks <= 1 or project.completed_tasks >= project.total_tasks * 0.8
                if success:
                    await self._complete_project(project, success=True)
                else:
                    await self._complete_project(project, success=False, reason=f"Too many failures: {project.failed_tasks}/{project.total_tasks}")
                return None
        
        # If we're already in testing phase and tasks complete, mark tests passed and complete
        if project.status == ProjectStatus.TESTING and len(project.pending_task_ids) == 0:
            project.tests_passed = True
            success = project.failed_tasks <= 2 or project.completed_tasks >= project.total_tasks * 0.7
            await self._complete_project(project, success=success)
            return None
        
        # Check if project is complete
        if await self._check_completion(project):
            await self._complete_project(project, success=True)
            return None
        
        # Generate follow-up tasks if needed
        if result.status == TaskStatus.FAILED:
            return await self._handle_failure(project, result)
        
        return None
    
    async def _generate_test_tasks(self, project: ProjectContext) -> List[Task]:
        """Generate tasks to test the created code."""
        files_list = ", ".join(project.created_files)
        
        prompt = f"""The following files have been created for the project:
{files_list}

Original request: {project.original_request}

Generate tasks to:
1. Run any existing tests
2. Create tests if none exist
3. Verify the code runs correctly

Focus on the entry point and main functionality."""
        
        tasks = await self._task_formulator.formulate(prompt)
        project.total_tasks += len(tasks)
        # Track new task IDs and store info
        for task in tasks:
            project.pending_task_ids.add(task.id)
            self._store_task_info(project, task)
        return tasks
    
    async def _generate_validation_tasks(self, project: ProjectContext) -> List[Task]:
        """Generate tasks to validate code quality."""
        files_list = ", ".join(project.created_files)
        
        prompt = f"""Validate code quality for these files:
{files_list}

Generate tasks to:
1. Check code quality and style
2. Verify the implementation meets the original request
3. Suggest any final improvements

Original request: {project.original_request}"""
        
        tasks = await self._task_formulator.formulate(prompt)
        project.total_tasks += len(tasks)
        # Track new task IDs and store info
        for task in tasks:
            project.pending_task_ids.add(task.id)
            self._store_task_info(project, task)
        return tasks
    
    async def _handle_failure(self, project: ProjectContext, result: TaskResult) -> Optional[List[Task]]:
        """
        Analyze a failed task and generate fix tasks.
        After fix task completes, the original task will be retried with fix context.
        
        Failure categories:
        1. FILE_NOT_FOUND - Dependency issue, need to create file first
        2. MISSING_PARAM - Task was malformed, regenerate with correct params
        3. CODE_ERROR - Syntax/logic error in generated code, need to fix
        4. TIMEOUT - Task took too long, try simpler approach
        5. UNKNOWN - Generic retry with more context
        """
        error = result.error or "Unknown error"
        task_output = result.output or {}
        
        # Don't retry too many times
        if project.failed_tasks > 5:
            self._logger.warning("Too many failures, stopping retry attempts", project_id=project.id)
            return None
        
        # Get original task info from history
        original_task_info = self._get_task_from_history(project, result.task_id)
        
        # Analyze the failure
        failure_analysis = await self._analyze_failure(error, task_output, project)
        
        self._logger.info(
            "Failure analyzed",
            project_id=project.id,
            task_id=result.task_id,
            category=failure_analysis.get("category"),
            fix_strategy=failure_analysis.get("fix_strategy")
        )
        
        # Generate fix tasks based on analysis
        fix_prompt = self._build_fix_prompt(failure_analysis, project)
        
        tasks = await self._task_formulator.formulate(fix_prompt)
        
        if tasks:
            project.total_tasks += len(tasks)
            # Use the first fix task as the primary one to link retry
            primary_fix_task = tasks[0]
            
            # Track new task IDs and store info
            for task in tasks:
                project.pending_task_ids.add(task.id)
                # Mark as a fix task with CRITICAL priority for immediate execution
                task.metadata["is_fix_task"] = True
                task.metadata["original_error"] = error[:200]
                task.priority = TaskPriority.CRITICAL  # Fix tasks execute first
                # Store task info for potential retry
                self._store_task_info(project, task)
            
            # Store retry info: when fix task completes, regenerate original task
            if original_task_info:
                project.pending_retries[primary_fix_task.id] = {
                    "original_title": original_task_info.get("title", "Failed task"),
                    "original_description": original_task_info.get("description", ""),
                    "original_agent_type": original_task_info.get("agent_type", "code_writer"),
                    "original_payload": original_task_info.get("payload", {}),
                    "error": error,
                    "fix_strategy": failure_analysis.get("fix_strategy", ""),
                    "category": failure_analysis.get("category", "UNKNOWN"),
                    "retry_count": original_task_info.get("retry_count", 0) + 1
                }
                self._logger.info(
                    "Retry scheduled after fix",
                    fix_task_id=primary_fix_task.id,
                    original_title=original_task_info.get("title")
                )
            
            # Notify about fix tasks
            plan_notifier = get_plan_notifier()
            await plan_notifier.notify_new_tasks(
                project_id=project.id,
                tasks=tasks,
                reason=f"Fix for: {failure_analysis.get('category', 'error')}"
            )
        
        return tasks
    
    def _store_task_info(self, project: ProjectContext, task: Task) -> None:
        """Store task info in the registry for potential retry."""
        project.task_registry[task.id] = {
            "title": task.title,
            "description": task.description,
            "agent_type": task.agent_type.value,
            "priority": task.priority.value,
            "payload": task.payload.copy() if task.payload else {},
            "parent_task_id": task.parent_task_id,
            "retry_count": task.metadata.get("retry_count", 0)
        }
    
    def _get_task_from_history(self, project: ProjectContext, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task info from project registry."""
        return project.task_registry.get(task_id)
    
    async def _generate_retry_task(
        self, 
        project: ProjectContext, 
        retry_info: Dict[str, Any],
        fix_output: Any
    ) -> Optional[List[Task]]:
        """
        Generate a retry task after fix task completes.
        The retry task is aware of the fix that was applied.
        """
        # Don't retry more than 2 times
        if retry_info.get("retry_count", 0) >= 2:
            self._logger.warning(
                "Max retries reached for task",
                original_title=retry_info.get("original_title")
            )
            return None
        
        original_title = retry_info.get("original_title", "Retry task")
        original_description = retry_info.get("original_description", "")
        fix_strategy = retry_info.get("fix_strategy", "")
        error = retry_info.get("error", "")
        category = retry_info.get("category", "UNKNOWN")
        
        # Build context about what was fixed
        fix_context = ""
        if isinstance(fix_output, dict):
            if fix_output.get("file_path"):
                fix_context = f"A fix was applied: created/modified {fix_output.get('file_path')}"
            elif fix_output.get("summary"):
                fix_context = f"A fix was applied: {fix_output.get('summary')}"
        
        # Generate retry prompt with fix context
        retry_prompt = f"""Retry a previously failed task. The fix has been applied.

ORIGINAL TASK: {original_title}
ORIGINAL DESCRIPTION: {original_description}

PREVIOUS ERROR: {error[:200]}
ERROR CATEGORY: {category}
FIX APPLIED: {fix_strategy}
{fix_context}

Files now available: {', '.join(project.created_files) or 'None'}

Generate a task to complete the original goal, taking into account:
1. The fix that was just applied
2. Any new files that were created
3. The original error (avoid repeating it)

Make sure to include proper file_path for any code_writer tasks."""
        
        tasks = await self._task_formulator.formulate(retry_prompt)
        
        if tasks:
            project.total_tasks += len(tasks)
            for task in tasks:
                project.pending_task_ids.add(task.id)
                # Mark as retry task with high priority
                task.metadata["is_retry_task"] = True
                task.metadata["retry_count"] = retry_info.get("retry_count", 0) + 1
                task.metadata["original_error"] = error[:100]
                task.priority = TaskPriority.HIGH  # High priority but not as urgent as fix
                # Store for potential future retry
                self._store_task_info(project, task)
            
            # Notify about retry tasks
            plan_notifier = get_plan_notifier()
            await plan_notifier.notify_new_tasks(
                project_id=project.id,
                tasks=tasks,
                reason=f"Retry after fix: {original_title}"
            )
            
            self._logger.info(
                "Retry task generated",
                original_title=original_title,
                retry_count=retry_info.get("retry_count", 0) + 1
            )
        
        return tasks
    
    async def _analyze_failure(self, error: str, output: dict, project: ProjectContext) -> Dict[str, Any]:
        """Analyze a failure and categorize it for smart recovery."""
        error_lower = error.lower()
        
        # Quick pattern matching for common failures
        if "file not found" in error_lower or "no such file" in error_lower:
            # Extract the missing file path
            import re
            file_match = re.search(r'["\']?(/\S+\.\w+)["\']?', error)
            missing_file = file_match.group(1) if file_match else None
            
            return {
                "category": "FILE_NOT_FOUND",
                "missing_file": missing_file,
                "fix_strategy": "Create the missing file before retrying the original task",
                "priority": "high"
            }
        
        if "file_path is required" in error_lower or "required" in error_lower and "missing" in error_lower:
            return {
                "category": "MISSING_PARAM",
                "fix_strategy": "Regenerate the task with proper parameters",
                "priority": "medium"
            }
        
        if any(kw in error_lower for kw in ["syntax error", "indentation", "invalid syntax", "unexpected token"]):
            return {
                "category": "CODE_ERROR",
                "fix_strategy": "Fix the syntax error in the generated code",
                "priority": "high"
            }
        
        if any(kw in error_lower for kw in ["timeout", "timed out", "took too long"]):
            return {
                "category": "TIMEOUT",
                "fix_strategy": "Try a simpler approach or break into smaller tasks",
                "priority": "medium"
            }
        
        if any(kw in error_lower for kw in ["import error", "module not found", "no module named"]):
            return {
                "category": "IMPORT_ERROR",
                "fix_strategy": "Create missing module or fix import statement",
                "priority": "high"
            }
        
        # Use LLM for complex error analysis
        return await self._llm_analyze_failure(error, output, project)
    
    async def _llm_analyze_failure(self, error: str, output: dict, project: ProjectContext) -> Dict[str, Any]:
        """Use LLM to analyze complex failures."""
        prompt = f"""Analyze this task failure and suggest a fix strategy.

ERROR: {error[:500]}

OUTPUT: {str(output)[:500]}

PROJECT CONTEXT:
- Original request: {project.original_request}
- Files created so far: {', '.join(project.created_files) or 'None'}
- Completed tasks: {project.completed_tasks}
- Failed tasks: {project.failed_tasks}

Categorize this error and provide a fix strategy.

Respond with JSON:
{{
    "category": "one of: CODE_ERROR, LOGIC_ERROR, DEPENDENCY_ERROR, CONFIG_ERROR, UNKNOWN",
    "root_cause": "brief explanation of what went wrong",
    "fix_strategy": "specific steps to fix this issue",
    "priority": "high/medium/low",
    "suggested_agent": "which agent should handle the fix: code_writer/architect/tester_whitebox"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "root_cause": {"type": "string"},
                    "fix_strategy": {"type": "string"},
                    "priority": {"type": "string"},
                    "suggested_agent": {"type": "string"}
                },
                "required": ["category", "fix_strategy"]
            })
            return result
        except Exception as e:
            self._logger.error("LLM failure analysis failed", error=str(e))
            return {
                "category": "UNKNOWN",
                "fix_strategy": "Retry with alternative approach",
                "priority": "medium"
            }
    
    def _build_fix_prompt(self, analysis: Dict[str, Any], project: ProjectContext) -> str:
        """Build a prompt for generating fix tasks based on failure analysis."""
        category = analysis.get("category", "UNKNOWN")
        fix_strategy = analysis.get("fix_strategy", "Fix the issue")
        
        base_context = f"""Original project request: {project.original_request}
Files created so far: {', '.join(project.created_files) or 'None'}
"""
        
        if category == "FILE_NOT_FOUND":
            missing_file = analysis.get("missing_file", "the missing file")
            return f"""{base_context}

A task failed because a file was not found: {missing_file}

Create a task to:
1. Create the missing file with appropriate content for this project
2. Ensure the file follows the project's patterns and requirements

The file should be created at: {missing_file}"""
        
        elif category == "MISSING_PARAM":
            return f"""{base_context}

A task failed due to missing parameters. {fix_strategy}

Create a properly structured task that includes all required parameters.
For code_writer tasks, always include file_path.
For code_quality tasks, include file_path of an existing file to analyze."""
        
        elif category == "CODE_ERROR":
            return f"""{base_context}

A code error occurred: {analysis.get('root_cause', 'Syntax or logic error')}

Create a task to:
1. Review and fix the problematic code
2. Ensure proper syntax and formatting
3. Test the fix works correctly"""
        
        elif category == "IMPORT_ERROR":
            return f"""{base_context}

An import error occurred. Create a task to:
1. Create the missing module if needed
2. Or fix the import statement to use correct module path
3. Ensure all dependencies are properly structured"""
        
        else:
            return f"""{base_context}

A task failed with this issue: {fix_strategy}

Create a recovery task that:
1. Addresses the root cause
2. Uses an alternative approach if the original failed
3. Ensures the project can continue towards completion"""
    
    async def _check_completion(self, project: ProjectContext) -> bool:
        """Check if the project is complete."""
        # Must have completed at least some tasks
        if project.completed_tasks == 0:
            return False
        
        # Must have created some files
        if not project.created_files:
            return False
        
        # Use LLM to verify completion
        files_list = "\n".join(project.created_files)
        
        prompt = f"""Evaluate if this project is complete:

ORIGINAL REQUEST: {project.original_request}

FILES CREATED:
{files_list}

STATS:
- Tasks completed: {project.completed_tasks}
- Tasks failed: {project.failed_tasks}
- Tests passed: {project.tests_passed}

Is this project complete and functional? Consider:
1. Are all required files created?
2. Does it fulfill the original request?
3. Is there a working entry point?

Respond with JSON:
{{"complete": true/false, "reason": "explanation", "missing": ["list of missing items if any"]}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "complete": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "missing": {"type": "array"}
                }
            })
            
            is_complete = result.get("complete", False)
            self._logger.info("Completion check", 
                            complete=is_complete, 
                            reason=result.get("reason", ""))
            
            return is_complete
            
        except Exception as e:
            self._logger.error("Completion check failed", error=str(e))
            # Fall back to basic check
            return project.completed_tasks >= project.total_tasks
    
    async def _complete_project(self, project: ProjectContext, success: bool, reason: str = "") -> None:
        """Mark project as complete."""
        status = ProjectStatus.COMPLETED if success else ProjectStatus.FAILED
        await self._update_status(project, status)
        
        summary = {
            "project_id": project.id,
            "success": success,
            "reason": reason,
            "files_created": list(project.created_files),
            "tasks_completed": project.completed_tasks,
            "tasks_failed": project.failed_tasks,
            "iterations": project.iteration_count
        }
        
        self._logger.info("Project completed", **summary)
        
        if self._completion_callback:
            await self._completion_callback(project, summary)
    
    async def _update_status(self, project: ProjectContext, status: ProjectStatus) -> None:
        """Update project status and notify."""
        old_status = project.status
        project.status = status
        
        self._logger.info("Project status changed", 
                         project_id=project.id,
                         old_status=old_status.value,
                         new_status=status.value)
        
        if self._status_callback:
            await self._status_callback(project, old_status, status)
    
    def interrupt_project(self, project_id: str) -> bool:
        """Interrupt a running project."""
        project = self._projects.get(project_id)
        if project and project.status == ProjectStatus.IN_PROGRESS:
            project.interrupted = True
            self._logger.info("Project interrupted", project_id=project_id)
            return True
        return False
    
    def get_project_status(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a project."""
        project = self._projects.get(project_id)
        if not project:
            return None
        
        return {
            "id": project.id,
            "status": project.status.value,
            "original_request": project.original_request,
            "total_tasks": project.total_tasks,
            "completed_tasks": project.completed_tasks,
            "failed_tasks": project.failed_tasks,
            "pending_tasks": len(project.pending_task_ids),
            "files_created": list(project.created_files),
            "iteration_count": project.iteration_count,
            "created_at": project.created_at.isoformat()
        }
    
    def list_projects(self) -> List[Dict[str, Any]]:
        """List all projects and their statuses."""
        return [self.get_project_status(pid) for pid in self._projects.keys()]


class ProjectOrchestratorFactory:
    """Factory for creating ProjectOrchestrator instances."""
    
    @staticmethod
    def create(
        llm_client: ILLMClient,
        task_formulator: ITaskFormulator,
        file_reader: IFileReader
    ) -> ProjectOrchestrator:
        return ProjectOrchestrator(llm_client, task_formulator, file_reader)
