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
    ProjectOrchestrator, ProjectOrchestratorFactory, ProjectStatus
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
        prompt_analyser._file_reader
    )
    
    # Wire up orchestrator callbacks
    async def dispatch_tasks(tasks):
        """Dispatch tasks from orchestrator."""
        for task in tasks:
            await prompt_analyser._task_scheduler.schedule(task)
    
    async def on_project_complete(project, summary):
        """Handle project completion."""
        await feedback_queue.put({
            "type": "project_complete",
            "project_id": project.id,
            "summary": summary
        })
    
    async def on_status_change(project, old_status, new_status):
        """Handle project status change."""
        await feedback_queue.put({
            "type": "project_status",
            "project_id": project.id,
            "old_status": old_status.value,
            "new_status": new_status.value
        })
    
    project_orchestrator.on_task_dispatch(dispatch_tasks)
    project_orchestrator.on_completion(on_project_complete)
    project_orchestrator.on_status_change(on_status_change)
    
    # Subscribe to result topic for orchestrator
    async def handle_result(message: dict):
        """Route results to orchestrator if part of a project."""
        from src.core.interfaces import TaskResult, TaskStatus as TS
        
        task_id = message.get("task_id")
        status = TS(message.get("status", "completed"))
        
        # Check if this task belongs to any active project
        task_belongs_to_project = False
        for project_id in list(project_orchestrator._projects.keys()):
            project = project_orchestrator._projects[project_id]
            if task_id in project.pending_task_ids:
                task_belongs_to_project = True
                break
        
        # Only update scheduler for tasks we dispatched from this service
        if task_belongs_to_project:
            await prompt_analyser._task_scheduler.update_task_status(task_id, status)
        
        # Route to project orchestrator - it will filter by task ownership
        for project_id in list(project_orchestrator._projects.keys()):
            project = project_orchestrator._projects[project_id]
            if project.status in (ProjectStatus.IN_PROGRESS, ProjectStatus.TESTING, ProjectStatus.VALIDATING):
                result = TaskResult(
                    task_id=task_id,
                    status=status,
                    output=message.get("output"),
                    error=message.get("error")
                )
                new_tasks = await project_orchestrator.handle_task_result(project_id, result)
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
        await feedback_queue.put({
            "type": "command_approval",
            "request_id": request.id,
            "command": request.command,
            "agent_type": request.agent_type,
            "task_id": request.task_id,
            "project_id": request.project_id,
            "working_dir": request.working_dir,
            "reason": request.reason
        })
    
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
    lifespan=lifespan
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
            "api": True
        }
    )


@app.post("/tasks", response_model=TaskResponse)
async def submit_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """
    Submit a natural language task for processing.
    This is the main endpoint for PO/PM input.
    Creates individual tasks (for simple requests).
    """
    if not prompt_analyser or not prompt_analyser.is_running:
        raise HTTPException(status_code=503, detail="Prompt analyser service not available")
    
    try:
        logger.info("Received task request", input_length=len(request.input))
        
        # Process the input through the prompt analyser
        tasks = await prompt_analyser.process_input(request.input)
        
        return TaskResponse(
            success=True,
            message=f"Created {len(tasks)} tasks",
            tasks=[t.model_dump() for t in tasks]
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
        raise HTTPException(status_code=503, detail="Project orchestrator not available")
    
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
            status="initializing"
        )
        
    except Exception as e:
        logger.error("Failed to start project", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/projects/{project_id}")
async def get_project_status(project_id: str):
    """Get status of an autonomous project."""
    if not project_orchestrator:
        raise HTTPException(status_code=503, detail="Project orchestrator not available")
    
    status = project_orchestrator.get_project_status(project_id)
    if not status:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return status


@app.get("/projects")
async def list_projects():
    """List all projects and their statuses."""
    if not project_orchestrator:
        raise HTTPException(status_code=503, detail="Project orchestrator not available")
    
    return {"projects": project_orchestrator.list_projects()}


@app.post("/projects/{project_id}/interrupt")
async def interrupt_project(project_id: str):
    """
    Interrupt a running autonomous project.
    The project will stop after completing the current task.
    """
    if not project_orchestrator:
        raise HTTPException(status_code=503, detail="Project orchestrator not available")
    
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
                    client_executor.handle_result(command_id, {
                        "stdout": data.get("stdout", ""),
                        "stderr": data.get("stderr", ""),
                        "return_code": data.get("return_code", 0)
                    })
                    logger.info("Command result received from client", command_id=command_id)
                
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
                "created_at": r.created_at.isoformat()
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
    raise HTTPException(status_code=404, detail="Request not found or already processed")


@app.post("/commands/{request_id}/reject")
async def reject_command(request_id: str):
    """Reject a pending command request."""
    manager = get_approval_manager()
    success = manager.reject(request_id)
    if success:
        return {"success": True, "message": "Command rejected"}
    raise HTTPException(status_code=404, detail="Request not found or already processed")


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
                "status": t.status
            }
            for t in plan.tasks
        ],
        "architect_output": plan.architect_output,
        "created_at": plan.created_at.isoformat()
    }


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
            files.append({
                "path": relpath,
                "size": stat.st_size,
                "modified": stat.st_mtime
            })
    
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
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
