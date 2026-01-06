"""
FastAPI Application - VSCode Interface API
Provides the REST API for PO/PM input and user feedback.
Supports both single tasks and autonomous project execution.
"""

import asyncio
import uuid
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import structlog

from src.core.config import get_settings, AppSettings
from src.core.interfaces import Task, TaskStatus
from src.core.command_approval import get_approval_manager, CommandApprovalRequest
from src.core.client_executor import get_client_executor
from src.core.plan_notifier import get_plan_notifier
from src.core.agent_selector import get_agent_selector
from src.services.prompt_analyser.main import PromptAnalyserService
from src.services.prompt_analyser.project_orchestrator import (
    ProjectOrchestrator,
    ProjectOrchestratorFactory,
    ProjectStatus,
)
from src.infrastructure.message_bus import MessageBus, Topics


logger = structlog.get_logger()

# Global state
settings: AppSettings = None
prompt_analyser: PromptAnalyserService = None
project_orchestrator: ProjectOrchestrator = None
feedback_queue: asyncio.Queue = None
client_connections: list = []  # WebSocket connections from clients


# Request/Response models
class TaskRequest(BaseModel):
    """Request model for submitting a task."""

    input: str
    priority: Optional[str] = "medium"
    metadata: Optional[dict] = {}


class ProjectRequest(BaseModel):
    """Request model for starting an autonomous project."""

    input: str
    metadata: Optional[dict] = {}


class TaskResponse(BaseModel):
    """Response model for task submission."""

    success: bool
    message: str
    tasks: list[dict] = []


class ProjectResponse(BaseModel):
    """Response model for project submission."""

    success: bool
    project_id: str
    message: str
    status: str


class FeedbackResponse(BaseModel):
    """Response model for feedback polling."""

    feedback: list[dict]


class HealthResponse(BaseModel):
    """Response model for health check."""

    status: str
    services: dict


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global settings, prompt_analyser, project_orchestrator, feedback_queue

    logger.info("Starting VSCode API...")

    # Initialize settings
    settings = get_settings()

    # Initialize feedback queue
    feedback_queue = asyncio.Queue()

    # Initialize prompt analyser
    prompt_analyser = PromptAnalyserService(settings)
    await prompt_analyser.start()

    # Create a SEPARATE message bus for API with unique consumer group
    # This prevents message splitting between API and prompt-analyser
    api_message_bus = MessageBus(settings.kafka, group_id="vscode-api-consumer")
    await api_message_bus.start()

    # Initialize project orchestrator
    project_orchestrator = ProjectOrchestratorFactory.create(
        prompt_analyser._llm_client,
        prompt_analyser._task_formulator,
        prompt_analyser._file_reader,
    )

    # Wire up orchestrator callbacks
    async def dispatch_tasks(tasks):
        """Dispatch tasks from orchestrator."""
        # Group tasks by project_id and mark as running
        tasks_by_project = {}
        for task in tasks:
            project_id = task.payload.get("project_id")
            if project_id:
                if project_id not in tasks_by_project:
                    tasks_by_project[project_id] = []
                tasks_by_project[project_id].append(task.id)

        # Mark tasks as running before dispatching
        for project_id, task_ids in tasks_by_project.items():
            project_orchestrator.mark_tasks_as_running(project_id, task_ids)

        # Now dispatch to scheduler
        for task in tasks:
            await prompt_analyser._task_scheduler.schedule(task)

    async def on_project_complete(project, summary):
        """Handle project completion."""
        await feedback_queue.put(
            {"type": "project_complete", "project_id": project.id, "summary": summary}
        )

    async def on_status_change(project, old_status, new_status):
        """Handle project status change."""
        await feedback_queue.put(
            {
                "type": "project_status",
                "project_id": project.id,
                "old_status": old_status.value,
                "new_status": new_status.value,
            }
        )

    project_orchestrator.on_task_dispatch(dispatch_tasks)
    project_orchestrator.on_completion(on_project_complete)
    project_orchestrator.on_status_change(on_status_change)

    # Subscribe to result topic for orchestrator
    async def handle_result(message: dict):
        """Route results to orchestrator if part of a project."""
        from src.core.interfaces import TaskResult, TaskStatus as TS

        task_id = message.get("task_id")
        status = TS(message.get("status", "completed"))

        # Check if this task belongs to any active project (check both pending AND running)
        task_belongs_to_project = False
        for project_id in list(project_orchestrator._projects.keys()):
            project = project_orchestrator._projects[project_id]
            if (
                task_id in project.pending_task_ids
                or task_id in project.running_task_ids
            ):
                task_belongs_to_project = True
                break

        # Only update scheduler for tasks we dispatched from this service
        if task_belongs_to_project:
            await prompt_analyser._task_scheduler.update_task_status(task_id, status)

        # Route to project orchestrator - it will filter by task ownership
        for project_id in list(project_orchestrator._projects.keys()):
            project = project_orchestrator._projects[project_id]
            if project.status in (
                ProjectStatus.IN_PROGRESS,
                ProjectStatus.TESTING,
                ProjectStatus.VALIDATING,
            ):
                result = TaskResult(
                    task_id=task_id,
                    status=status,
                    output=message.get("output"),
                    error=message.get("error"),
                )
                new_tasks = await project_orchestrator.handle_task_result(
                    project_id, result
                )
                if new_tasks:
                    await dispatch_tasks(new_tasks)

    await api_message_bus.subscribe(Topics.TASK_RESULT, handle_result)

    # Subscribe to feedback topic
    async def handle_feedback(message: dict):
        await feedback_queue.put(message)

    await api_message_bus.subscribe(Topics.USER_FEEDBACK, handle_feedback)

    # Wire up command approval notifications
    async def on_command_approval_request(request: CommandApprovalRequest):
        """Push command approval requests to the feedback queue."""
        await feedback_queue.put(
            {
                "type": "command_approval",
                "request_id": request.id,
                "command": request.command,
                "agent_type": request.agent_type,
                "task_id": request.task_id,
                "project_id": request.project_id,
                "working_dir": request.working_dir,
                "reason": request.reason,
            }
        )

    approval_manager = get_approval_manager()
    approval_manager.on_approval_request(on_command_approval_request)

    # Wire up plan notifier and client executor
    plan_notifier = get_plan_notifier()
    client_executor = get_client_executor()

    async def broadcast_to_clients(message: dict):
        """Broadcast message to all connected clients."""
        # Send only via manager to avoid duplicate messages
        await manager.broadcast(message)

    plan_notifier.set_broadcast_callback(broadcast_to_clients)
    client_executor.set_send_callback(broadcast_to_clients)

    # Start consuming in background
    asyncio.create_task(api_message_bus.start_consuming())

    logger.info("VSCode API started")

    yield

    # Shutdown
    logger.info("Shutting down VSCode API...")
    await prompt_analyser.stop()


# Create FastAPI app
app = FastAPI(
    title="Agentic IA System API",
    description="API for the Agentic IA Architecture - VSCode Interface",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        services={
            "prompt_analyser": prompt_analyser.is_running if prompt_analyser else False,
            "api": True,
        },
    )


@app.post("/tasks", response_model=TaskResponse)
async def submit_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """
    Submit a natural language task for processing.
    This is the main endpoint for PO/PM input.
    Creates individual tasks (for simple requests).
    """
    if not prompt_analyser or not prompt_analyser.is_running:
        raise HTTPException(
            status_code=503, detail="Prompt analyser service not available"
        )

    try:
        logger.info("Received task request", input_length=len(request.input))

        # Process the input through the prompt analyser
        tasks = await prompt_analyser.process_input(request.input)

        return TaskResponse(
            success=True,
            message=f"Created {len(tasks)} tasks",
            tasks=[t.model_dump() for t in tasks],
        )

    except Exception as e:
        logger.error("Failed to process task", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects", response_model=ProjectResponse)
async def start_project(request: ProjectRequest, background_tasks: BackgroundTasks):
    """
    Start an autonomous project.

    The system will work autonomously until:
    1. A working product is produced
    2. An interrupt request is received
    3. Maximum iterations are reached

    This is the recommended endpoint for complex requests.
    """
    if not project_orchestrator:
        raise HTTPException(
            status_code=503, detail="Project orchestrator not available"
        )

    try:
        project_id = str(uuid.uuid4())
        logger.info("Starting autonomous project", project_id=project_id)

        # Start the project in background
        async def run_project():
            try:
                await project_orchestrator.start_project(project_id, request.input)
            except Exception as e:
                logger.error("Project failed", project_id=project_id, error=str(e))

        background_tasks.add_task(run_project)

        return ProjectResponse(
            success=True,
            project_id=project_id,
            message="Autonomous project started. System will work until completion or interruption.",
            status="initializing",
        )

    except Exception as e:
        logger.error("Failed to start project", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/projects/{project_id}")
async def get_project_status(project_id: str):
    """Get status of an autonomous project."""
    if not project_orchestrator:
        raise HTTPException(
            status_code=503, detail="Project orchestrator not available"
        )

    status = project_orchestrator.get_project_status(project_id)
    if not status:
        raise HTTPException(status_code=404, detail="Project not found")

    return status


@app.get("/projects")
async def list_projects():
    """List all projects and their statuses."""
    if not project_orchestrator:
        raise HTTPException(
            status_code=503, detail="Project orchestrator not available"
        )

    return {"projects": project_orchestrator.list_projects()}


@app.post("/projects/{project_id}/interrupt")
async def interrupt_project(project_id: str):
    """
    Interrupt a running autonomous project.
    The project will stop after completing the current task.
    """
    if not project_orchestrator:
        raise HTTPException(
            status_code=503, detail="Project orchestrator not available"
        )

    success = project_orchestrator.interrupt_project(project_id)

    if success:
        return {"success": True, "message": "Project interrupted"}
    else:
        raise HTTPException(status_code=400, detail="Project not found or not running")


@app.get("/tasks", response_model=dict)
async def get_tasks():
    """Get current task queue status."""
    if not prompt_analyser:
        raise HTTPException(status_code=503, detail="Service not available")

    stats = await prompt_analyser.get_queue_stats()
    return stats


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get details of a specific task."""
    if not prompt_analyser:
        raise HTTPException(status_code=503, detail="Service not available")

    task = await prompt_analyser._task_scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return task.model_dump()


@app.get("/feedback", response_model=FeedbackResponse)
async def get_feedback():
    """
    Poll for user feedback.
    Returns accumulated feedback since last poll.
    """
    feedback_list = []

    # Drain the feedback queue (non-blocking)
    while not feedback_queue.empty():
        try:
            feedback = feedback_queue.get_nowait()
            feedback_list.append(feedback)
        except asyncio.QueueEmpty:
            break

    return FeedbackResponse(feedback=feedback_list)


@app.post("/feedback/acknowledge")
async def acknowledge_feedback(feedback_ids: list[str]):
    """Acknowledge received feedback."""
    # In a production system, this would update feedback status
    return {"acknowledged": len(feedback_ids)}


# WebSocket endpoint for real-time feedback (optional enhancement)
from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@app.websocket("/ws/feedback")
async def websocket_feedback(websocket: WebSocket):
    """WebSocket endpoint for real-time feedback."""
    await manager.connect(websocket)
    try:
        while True:
            # Wait for feedback
            if not feedback_queue.empty():
                feedback = await feedback_queue.get()
                await websocket.send_json(feedback)
            else:
                await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.websocket("/ws/client")
async def websocket_client(websocket: WebSocket):
    """
    Bidirectional WebSocket for client communication.

    Server -> Client messages:
    - command_request: Request to execute a command
    - plan_created: New execution plan
    - task_started/completed/failed: Task status updates

    Client -> Server messages:
    - command_result: Result of command execution
    - command_rejected: User rejected a command
    """
    await manager.connect(websocket)
    client_executor = get_client_executor()

    logger.info("Client connected via WebSocket")

    try:
        while True:
            # Handle incoming messages from client
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=0.5)

                msg_type = data.get("type")

                if msg_type == "command_result":
                    # Client executed a command and sent result
                    command_id = data.get("command_id")
                    client_executor.handle_result(
                        command_id,
                        {
                            "stdout": data.get("stdout", ""),
                            "stderr": data.get("stderr", ""),
                            "return_code": data.get("return_code", 0),
                        },
                    )
                    logger.info(
                        "Command result received from client", command_id=command_id
                    )

                elif msg_type == "command_rejected":
                    # User rejected the command
                    command_id = data.get("command_id")
                    client_executor.handle_result(command_id, {"rejected": True})
                    logger.info("Command rejected by client", command_id=command_id)

                elif msg_type == "settings_update":
                    # Client updating settings
                    if data.get("allow_all") is not None:
                        client_executor.set_session_allow_all(data["allow_all"])
                    if data.get("approved_patterns"):
                        for pattern in data["approved_patterns"]:
                            client_executor.add_approved_pattern(pattern)

            except asyncio.TimeoutError:
                pass  # No message, continue

            # Also send any pending feedback
            if not feedback_queue.empty():
                feedback = await feedback_queue.get()
                await websocket.send_json(feedback)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected from WebSocket")


# ============================================================
# Command Approval Endpoints
# ============================================================

from src.core.command_approval import get_approval_manager, CommandApprovalRequest


@app.get("/commands/pending")
async def get_pending_commands():
    """Get all pending command approval requests."""
    manager = get_approval_manager()
    pending = manager.get_pending_requests()
    return {
        "pending": [
            {
                "id": r.id,
                "command": r.command,
                "agent_type": r.agent_type,
                "task_id": r.task_id,
                "project_id": r.project_id,
                "working_dir": r.working_dir,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in pending
        ]
    }


@app.post("/commands/{request_id}/approve")
async def approve_command(request_id: str):
    """Approve a pending command request."""
    manager = get_approval_manager()
    success = manager.approve(request_id)
    if success:
        return {"success": True, "message": "Command approved"}
    raise HTTPException(
        status_code=404, detail="Request not found or already processed"
    )


@app.post("/commands/{request_id}/reject")
async def reject_command(request_id: str):
    """Reject a pending command request."""
    manager = get_approval_manager()
    success = manager.reject(request_id)
    if success:
        return {"success": True, "message": "Command rejected"}
    raise HTTPException(
        status_code=404, detail="Request not found or already processed"
    )


@app.get("/commands/settings")
async def get_command_settings():
    """Get current command approval settings."""
    manager = get_approval_manager()
    return manager.get_settings()


class SessionSettingsRequest(BaseModel):
    """Request model for session settings."""

    allow_all: Optional[bool] = None
    approved_patterns: Optional[list[str]] = None


@app.post("/commands/settings")
async def update_command_settings(request: SessionSettingsRequest):
    """
    Update command approval settings.

    - allow_all: If True, all commands are auto-approved for this session
    - approved_patterns: List of command prefixes to auto-approve (e.g., ['python', 'pytest'])
    """
    manager = get_approval_manager()

    if request.allow_all is not None:
        manager.set_session_allow_all(request.allow_all)

    if request.approved_patterns is not None:
        # Replace patterns
        for pattern in list(manager._approved_patterns):
            manager.remove_approved_pattern(pattern)
        for pattern in request.approved_patterns:
            manager.add_approved_pattern(pattern)

    return manager.get_settings()


# ============================================================
# Plan Endpoints
# ============================================================


@app.get("/projects/{project_id}/plan")
async def get_project_plan(project_id: str):
    """
    Get the execution plan for a project.

    Returns the list of planned tasks and architect output.
    """
    plan_notifier = get_plan_notifier()
    plan = plan_notifier.get_plan(project_id)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found for project")

    return {
        "project_id": plan.project_id,
        "prompt": plan.prompt,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "agent_type": t.agent_type,
                "priority": t.priority,
                "depends_on": t.depends_on,
                "status": t.status,
            }
            for t in plan.tasks
        ],
        "architect_output": plan.architect_output,
        "created_at": plan.created_at.isoformat(),
    }


async def _get_container_logs(container_name: str, tail: int = 50) -> str:
    """Fetch recent logs from a Docker container."""
    import asyncio

    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            "--tail",
            str(tail),
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
        return stdout.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return f"[Timeout fetching logs from {container_name}]"
    except Exception as e:
        return f"[Error fetching logs: {str(e)}]"


async def _get_task_scheduler_state() -> dict:
    """Get internal state of the task scheduler for debugging."""
    if not prompt_analyser or not prompt_analyser._task_scheduler:
        return {"error": "Task scheduler not available"}

    scheduler = prompt_analyser._task_scheduler
    stats = await scheduler.get_queue_stats()

    # Get detailed pending info
    pending_tasks = await scheduler.get_pending_tasks()
    pending_info = [
        {
            "id": t.id[:8] + "...",
            "title": t.title,
            "agent_type": t.agent_type.value,
            "priority": t.priority.value,
            "status": t.status.value,
            "parent_id": t.parent_task_id[:8] + "..." if t.parent_task_id else None,
        }
        for t in pending_tasks[:10]
    ]

    return {
        "stats": stats,
        "pending_tasks_detail": pending_info,
        "waiting_for_dependency_count": stats.get("waiting_for_dependency", 0),
    }


@app.get("/projects/{project_id}/diagnose")
async def diagnose_project(project_id: str, include_logs: bool = True):
    """
    Diagnose a project for potential issues like deadlocks.

    Returns diagnostic information about:
    - Pending tasks and their status
    - Which agents are processing tasks
    - Potential blockers or issues
    - Agent container logs (if include_logs=True)
    - Task scheduler internal state
    """
    import os
    from datetime import datetime

    if not project_orchestrator:
        raise HTTPException(
            status_code=503, detail="Project orchestrator not available"
        )

    project = project_orchestrator._projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    plan_notifier = get_plan_notifier()
    plan = plan_notifier.get_plan(project_id)

    # Gather diagnostic information
    pending_tasks = []
    running_tasks = []
    completed_tasks = []
    failed_tasks = []

    if plan:
        for task in plan.tasks:
            task_info = {
                "id": task.id[:8] + "...",
                "full_id": task.id,
                "title": task.title,
                "description": (
                    task.description[:100] + "..."
                    if len(task.description) > 100
                    else task.description
                ),
                "agent_type": task.agent_type,
                "status": task.status,
                "depends_on": task.depends_on[:8] + "..." if task.depends_on else None,
            }

            if task.status == "pending":
                pending_tasks.append(task_info)
            elif task.status == "running":
                running_tasks.append(task_info)
            elif task.status == "completed":
                completed_tasks.append(task_info)
            elif task.status == "failed":
                failed_tasks.append(task_info)

    # Analyze potential issues
    issues = []
    recommendations = []

    # Check for stuck running tasks
    if running_tasks:
        for task in running_tasks:
            issues.append(
                {
                    "type": "STUCK_TASK",
                    "severity": "high",
                    "message": f"Task '{task['title']}' is still running (agent: {task['agent_type']})",
                }
            )
        recommendations.append(
            "Check agent container logs: docker logs agent-"
            + running_tasks[0]["agent_type"].replace("_", "-")
        )

    # Check for dependency cycles or unresolvable dependencies
    pending_task_ids = {t["id"] for t in pending_tasks}
    for task in pending_tasks:
        if task["depends_on"] and task["depends_on"] in pending_task_ids:
            issues.append(
                {
                    "type": "BLOCKED_DEPENDENCY",
                    "severity": "medium",
                    "message": f"Task '{task['title']}' is waiting for another pending task",
                }
            )

    # Check if agents are responsive (would need health checks)
    agent_issues = set()
    for task in running_tasks:
        agent_issues.add(task["agent_type"])

    if agent_issues:
        recommendations.append(
            f"Potentially unresponsive agents: {', '.join(agent_issues)}"
        )
        recommendations.append("Try restarting affected agent containers")

    # Check for common issues
    if not running_tasks and pending_tasks:
        issues.append(
            {
                "type": "NO_RUNNING_TASKS",
                "severity": "high",
                "message": "No tasks are currently running but there are pending tasks",
            }
        )
        recommendations.append(
            "The task scheduler may be stuck. Try restarting the prompt-analyser container."
        )

    # Check if all tasks done but project not completing
    if (
        not running_tasks
        and not pending_tasks
        and project.status.value not in ("completed", "failed")
    ):
        issues.append(
            {
                "type": "COMPLETION_STUCK",
                "severity": "high",
                "message": "All tasks appear done but project hasn't completed",
            }
        )
        recommendations.append(
            "Check if there are tasks waiting for command approval that timed out"
        )

    # Check project age
    project_age = (datetime.now() - project.created_at).total_seconds()
    if project_age > 300 and project.completed_tasks < project.total_tasks:
        issues.append(
            {
                "type": "LONG_RUNNING",
                "severity": "warning",
                "message": f"Project has been running for {int(project_age)}s with only {project.completed_tasks}/{project.total_tasks} tasks completed",
            }
        )

    # Determine root cause
    root_cause = "Unknown"
    if running_tasks:
        stuck_agents = [t["agent_type"] for t in running_tasks]
        root_cause = f"Agent(s) not responding: {', '.join(set(stuck_agents))}"
    elif not running_tasks and pending_tasks:
        root_cause = "Task scheduler not dispatching tasks"
    elif project.failed_tasks > project.total_tasks * 0.3:
        root_cause = "Too many task failures causing cascading issues"
    elif not running_tasks and not pending_tasks and project.status.value == "testing":
        root_cause = "Testing phase stuck - possibly waiting for command approval or test execution"

    # Get task scheduler internal state
    scheduler_state = await _get_task_scheduler_state()

    # Collect agent logs if requested
    agent_logs = {}
    if include_logs:
        # Always get key service logs
        agent_logs["prompt-analyser"] = await _get_container_logs(
            "prompt-analyser", tail=30
        )
        agent_logs["vscode-api"] = await _get_container_logs("vscode-api", tail=20)

        # Get logs for agents with running tasks
        for agent in agent_issues:
            container_name = f"agent-{agent.replace('_', '-')}"
            agent_logs[container_name] = await _get_container_logs(
                container_name, tail=30
            )

        # If no specific agents, get all agent logs
        if not agent_issues and (running_tasks or pending_tasks or not issues):
            for agent_type in [
                "code-writer",
                "architect",
                "tester-whitebox",
                "cicd",
                "code-quality",
            ]:
                container_name = f"agent-{agent_type}"
                agent_logs[container_name] = await _get_container_logs(
                    container_name, tail=20
                )

    # Analyze logs for common error patterns
    log_insights = []
    for container, logs in agent_logs.items():
        if "error" in logs.lower() or "exception" in logs.lower():
            # Extract error lines
            error_lines = [
                line
                for line in logs.split("\n")
                if "error" in line.lower() or "exception" in line.lower()
            ]
            if error_lines:
                log_insights.append(
                    {
                        "container": container,
                        "type": "error_detected",
                        "sample": error_lines[-1][:200] if error_lines else "",
                    }
                )
        if "timeout" in logs.lower():
            log_insights.append(
                {
                    "container": container,
                    "type": "timeout_detected",
                    "sample": "Timeout mentioned in logs",
                }
            )
        if "approval" in logs.lower() and "timed out" in logs.lower():
            log_insights.append(
                {
                    "container": container,
                    "type": "approval_timeout",
                    "sample": "Command approval timed out",
                }
            )
            root_cause = "Command approval timeout - a command was waiting for approval but timed out"

    return {
        "project_id": project_id,
        "status": project.status.value,
        "summary": {
            "total_tasks": project.total_tasks,
            "completed": project.completed_tasks,
            "failed": project.failed_tasks,
            "pending": len(pending_tasks),
            "running": len(running_tasks),
            "project_age_seconds": int(project_age),
        },
        "running_tasks": running_tasks,
        "pending_tasks": pending_tasks[:10],  # Limit for display
        "failed_tasks": failed_tasks,
        "issues": issues,
        "root_cause": root_cause,
        "recommendations": recommendations,
        "scheduler_state": scheduler_state,
        "log_insights": log_insights,
        "agent_logs": agent_logs if include_logs else {},
        "debug_commands": [
            f"docker logs agent-{agent.replace('_', '-')}" for agent in agent_issues
        ]
        + ["docker logs prompt-analyser", "docker logs vscode-api"],
        "can_auto_recover": not running_tasks
        and pending_tasks,  # Can recover if scheduler is stuck
    }


@app.post("/projects/{project_id}/recover")
async def recover_project(project_id: str):
    """
    Attempt to automatically recover a stuck project.

    Recovery actions:
    1. Re-dispatch pending tasks that may have been lost
    2. Resolve orphaned dependencies (tasks waiting for completed parents)
    3. Reset stale in-progress tasks

    Returns a recovery report with details of what was done.
    """
    from datetime import datetime

    if not project_orchestrator:
        raise HTTPException(
            status_code=503, detail="Project orchestrator not available"
        )

    project = project_orchestrator._projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get the task scheduler
    task_scheduler = prompt_analyser._task_scheduler

    # Perform recovery
    recovery_report = await task_scheduler.recover_stuck_tasks()

    # Also notify the client about the recovery attempt
    plan_notifier = get_plan_notifier()
    await plan_notifier.notify_operation(
        project_id=project_id,
        operation="recovery",
        details=f"Auto-recovery: {len(recovery_report.get('recovered_tasks', []))} tasks re-dispatched",
    )

    total_recovered = (
        len(recovery_report.get("recovered_tasks", []))
        + len(recovery_report.get("orphaned_dependencies_resolved", []))
        + len(recovery_report.get("stale_tasks_reset", []))
    )

    return {
        "success": total_recovered > 0,
        "project_id": project_id,
        "message": (
            f"Recovery attempted: {total_recovered} tasks recovered"
            if total_recovered > 0
            else "No tasks needed recovery"
        ),
        "recovery_report": recovery_report,
    }


@app.get("/system/introspect")
async def introspect_system():
    """
    Get system introspection for debugging.

    Returns information about:
    - Container health status
    - Agent configurations
    - System paths and settings
    - Message bus status
    """
    import os
    import asyncio

    result = {"containers": {}, "services": {}, "configuration": {}, "paths": {}}

    # Check container health
    containers = [
        "prompt-analyser",
        "vscode-api",
        "message-bus",
        "agent-code-writer",
        "agent-architect",
        "agent-tester-whitebox",
        "agent-tester-blackbox",
        "agent-cicd",
        "agent-code-quality",
        "kafka",
        "zookeeper",
        "ollama-server",
    ]

    for container in containers:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}",
                container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=3.0)
            status = stdout.decode().strip()
            result["containers"][container] = {
                "status": status,
                "healthy": status == "running",
            }
        except:
            result["containers"][container] = {"status": "unknown", "healthy": False}

    # Service info
    result["services"]["prompt_analyser"] = {
        "active": prompt_analyser is not None,
        "has_scheduler": (
            prompt_analyser._task_scheduler is not None if prompt_analyser else False
        ),
    }
    result["services"]["project_orchestrator"] = {
        "active": project_orchestrator is not None,
        "projects_count": (
            len(project_orchestrator._projects) if project_orchestrator else 0
        ),
    }

    # Configuration paths
    result["paths"]["workspace"] = settings.service.workspace_path
    result["paths"]["agent_scripts"] = "/app/src/agents"

    # Get configuration
    result["configuration"]["llm_endpoint"] = settings.llama.base_url
    result["configuration"]["kafka_brokers"] = settings.kafka.bootstrap_servers
    result["configuration"]["max_concurrent_tasks"] = getattr(
        settings.service, "max_concurrent_tasks", 3
    )

    # Agent type mappings
    result["agent_types"] = {
        "code_writer": {
            "container": "agent-code-writer",
            "script": "src/agents/code_writer/main.py",
        },
        "architect": {
            "container": "agent-architect",
            "script": "src/agents/architect/main.py",
        },
        "tester_whitebox": {
            "container": "agent-tester-whitebox",
            "script": "src/agents/tester_whitebox/main.py",
        },
        "tester_blackbox": {
            "container": "agent-tester-blackbox",
            "script": "src/agents/tester_blackbox/main.py",
        },
        "cicd": {"container": "agent-cicd", "script": "src/agents/cicd/main.py"},
        "code_quality": {
            "container": "agent-code-quality",
            "script": "src/agents/code_quality/main.py",
        },
    }

    return result


@app.get("/system/agent-code/{agent_type}")
async def get_agent_code(agent_type: str):
    """
    Get the source code for a specific agent.
    Useful for understanding agent behavior during debugging.
    """
    import os

    agent_scripts = {
        "code_writer": "/app/src/agents/code_writer/main.py",
        "architect": "/app/src/agents/architect/main.py",
        "tester_whitebox": "/app/src/agents/tester_whitebox/main.py",
        "tester_blackbox": "/app/src/agents/tester_blackbox/main.py",
        "cicd": "/app/src/agents/cicd/main.py",
        "code_quality": "/app/src/agents/code_quality/main.py",
    }

    if agent_type not in agent_scripts:
        raise HTTPException(status_code=404, detail=f"Unknown agent type: {agent_type}")

    script_path = agent_scripts[agent_type]

    # Also check local path
    local_path = script_path.replace("/app/", "")
    if os.path.exists(local_path):
        script_path = local_path

    try:
        with open(script_path, "r") as f:
            code = f.read()

        return {
            "agent_type": agent_type,
            "script_path": script_path,
            "code": code,
            "lines": len(code.split("\n")),
        }
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Agent script not found at {script_path}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading agent code: {str(e)}"
        )


# ============================================================
# Workspace Management Endpoints
# ============================================================


@app.get("/workspace/files")
async def list_workspace_files():
    """List all files in the workspace."""
    import os

    workspace_path = settings.service.workspace_path

    files = []
    for root, dirs, filenames in os.walk(workspace_path):
        for filename in filenames:
            filepath = os.path.join(root, filename)
            relpath = os.path.relpath(filepath, workspace_path)
            stat = os.stat(filepath)
            files.append(
                {"path": relpath, "size": stat.st_size, "modified": stat.st_mtime}
            )

    return {"files": files, "count": len(files)}


@app.delete("/workspace/files/{filepath:path}")
async def delete_workspace_file(filepath: str):
    """Delete a file from the workspace."""
    import os

    workspace_path = settings.service.workspace_path
    full_path = os.path.join(workspace_path, filepath)

    # Security check
    if not os.path.abspath(full_path).startswith(os.path.abspath(workspace_path)):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found")

    os.remove(full_path)
    return {"success": True, "deleted": filepath}


@app.delete("/workspace/clean")
async def clean_workspace():
    """Delete all files in the workspace."""
    import os
    import shutil

    workspace_path = settings.service.workspace_path

    deleted = []
    for item in os.listdir(workspace_path):
        item_path = os.path.join(workspace_path, item)
        if os.path.isfile(item_path):
            os.remove(item_path)
            deleted.append(item)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)
            deleted.append(item + "/")

    return {"success": True, "deleted": deleted, "count": len(deleted)}


def main():
    """Run the API server."""
    import uvicorn

    uvicorn.run(
        "src.api.main:app", host="0.0.0.0", port=8000, reload=False, log_level="info"
    )


if __name__ == "__main__":
    main()
