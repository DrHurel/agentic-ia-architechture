"""
FastAPI Application - VSCode Interface API
Provides the REST API for PO/PM input and user feedback.
"""

import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import structlog

from src.core.config import get_settings, AppSettings
from src.core.interfaces import Task, TaskStatus
from src.services.prompt_analyser.main import PromptAnalyserService
from src.infrastructure.message_bus import MessageBus, Topics


logger = structlog.get_logger()

# Global state
settings: AppSettings = None
prompt_analyser: PromptAnalyserService = None
feedback_queue: asyncio.Queue = None


# Request/Response models
class TaskRequest(BaseModel):
    """Request model for submitting a task."""
    input: str
    priority: Optional[str] = "medium"
    metadata: Optional[dict] = {}


class TaskResponse(BaseModel):
    """Response model for task submission."""
    success: bool
    message: str
    tasks: list[dict] = []


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
    global settings, prompt_analyser, feedback_queue
    
    logger.info("Starting VSCode API...")
    
    # Initialize settings
    settings = get_settings()
    
    # Initialize feedback queue
    feedback_queue = asyncio.Queue()
    
    # Initialize prompt analyser
    prompt_analyser = PromptAnalyserService(settings)
    await prompt_analyser.start()
    
    # Subscribe to feedback topic
    async def handle_feedback(message: dict):
        await feedback_queue.put(message)
    
    await prompt_analyser._message_bus.subscribe(Topics.USER_FEEDBACK, handle_feedback)
    
    # Start consuming in background
    asyncio.create_task(prompt_analyser._message_bus.start_consuming())
    
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
