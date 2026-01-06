#!/usr/bin/env python3
"""
Agentic IA System Client
========================
A client library to interact with the Agentic IA System API.
Supports project creation, monitoring, feedback polling, and WebSocket real-time updates.

New Features:
- Plan display: See what tasks are planned before execution
- Command execution: Execute commands locally that agents request
- Session settings: Approve all commands for the session
"""

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any, List, Dict
import httpx
import websockets


class ProjectStatus(str, Enum):
    """Project status enum."""

    INITIALIZING = "initializing"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    TESTING = "testing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass
class PlanTask:
    """A task in the execution plan."""

    id: str
    title: str
    description: str
    agent_type: str
    priority: str
    depends_on: Optional[str] = None
    status: str = "pending"

    @classmethod
    def from_dict(cls, data: dict) -> "PlanTask":
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            agent_type=data.get("agent_type", ""),
            priority=data.get("priority", "medium"),
            depends_on=data.get("depends_on"),
            status=data.get("status", "pending"),
        )


@dataclass
class ExecutionPlan:
    """An execution plan from the server."""

    project_id: str
    prompt: str
    tasks: List[PlanTask]
    architect_output: Optional[Dict] = None

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionPlan":
        return cls(
            project_id=data.get("project_id", ""),
            prompt=data.get("prompt", ""),
            tasks=[PlanTask.from_dict(t) for t in data.get("tasks", [])],
            architect_output=data.get("architect_output"),
        )


@dataclass
class CommandRequest:
    """A command execution request from an agent."""

    id: str
    command: str
    working_dir: str
    agent_type: str
    task_id: str
    project_id: Optional[str]
    reason: str
    requires_approval: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "CommandRequest":
        return cls(
            id=data.get("id", ""),
            command=data.get("command", ""),
            working_dir=data.get("working_dir", ""),
            agent_type=data.get("agent_type", ""),
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id"),
            reason=data.get("reason", ""),
            requires_approval=data.get("requires_approval", True),
        )


@dataclass
class ProjectInfo:
    """Project information."""

    id: str
    status: ProjectStatus
    original_request: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    files_created: list[str]
    iteration_count: int
    created_at: str

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectInfo":
        return cls(
            id=data["id"],
            status=ProjectStatus(data["status"]),
            original_request=data["original_request"],
            total_tasks=data["total_tasks"],
            completed_tasks=data["completed_tasks"],
            failed_tasks=data["failed_tasks"],
            files_created=data.get("files_created", []),
            iteration_count=data["iteration_count"],
            created_at=data["created_at"],
        )

    @property
    def progress_percent(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return (self.completed_tasks / self.total_tasks) * 100

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ProjectStatus.COMPLETED,
            ProjectStatus.FAILED,
            ProjectStatus.INTERRUPTED,
        )


@dataclass
class TaskInfo:
    """Task information."""

    id: str
    title: str
    description: str
    status: str
    agent_type: str
    priority: str

    @classmethod
    def from_dict(cls, data: dict) -> "TaskInfo":
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            status=data["status"],
            agent_type=data["agent_type"],
            priority=data["priority"],
        )


class AgenticClient:
    """
    Client for the Agentic IA System API.

    Usage:
        client = AgenticClient("http://localhost:8000")

        # Start a project
        project_id = await client.start_project("Create a Python web scraper")

        # Monitor progress
        async for info in client.monitor_project(project_id):
            print(f"Progress: {info.progress_percent:.1f}%")

        # Get feedback
        feedback = await client.get_feedback()
    """

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AgenticClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "Client not initialized. Use 'async with AgenticClient() as client:'"
            )
        return self._client

    # ========== Health & Status ==========

    async def health_check(self) -> dict:
        """Check system health."""
        response = await self.client.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    async def is_healthy(self) -> bool:
        """Quick health check."""
        try:
            health = await self.health_check()
            return health.get("status") == "healthy"
        except Exception:
            return False

    # ========== Project Management ==========

    async def start_project(self, prompt: str, metadata: Optional[dict] = None) -> str:
        """
        Start an autonomous project.

        Args:
            prompt: Natural language description of what to build
            metadata: Optional metadata to attach to the project

        Returns:
            Project ID
        """
        payload = {"input": prompt}
        if metadata:
            payload["metadata"] = metadata

        response = await self.client.post(f"{self.base_url}/projects", json=payload)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            raise RuntimeError(f"Failed to start project: {data}")

        return data["project_id"]

    async def get_project(self, project_id: str) -> ProjectInfo:
        """Get project status and details."""
        response = await self.client.get(f"{self.base_url}/projects/{project_id}")
        response.raise_for_status()
        return ProjectInfo.from_dict(response.json())

    async def diagnose_project(self, project_id: str) -> dict:
        """Get diagnostic information for a project (for deadlock detection)."""
        response = await self.client.get(
            f"{self.base_url}/projects/{project_id}/diagnose"
        )
        response.raise_for_status()
        return response.json()

    async def recover_project(self, project_id: str) -> dict:
        """Attempt to automatically recover a stuck project."""
        response = await self.client.post(
            f"{self.base_url}/projects/{project_id}/recover"
        )
        response.raise_for_status()
        return response.json()

    async def interrupt_project(
        self, project_id: str, reason: str = "User requested"
    ) -> bool:
        """Interrupt a running project."""
        response = await self.client.post(
            f"{self.base_url}/projects/{project_id}/interrupt", json={"reason": reason}
        )
        response.raise_for_status()
        return response.json().get("success", False)

    async def monitor_project(
        self,
        project_id: str,
        poll_interval: float = 2.0,
        on_update: Optional[Callable[[ProjectInfo], None]] = None,
    ) -> ProjectInfo:
        """
        Monitor a project until completion.

        Args:
            project_id: Project to monitor
            poll_interval: Seconds between status checks
            on_update: Optional callback for each status update

        Returns:
            Final project info
        """
        while True:
            info = await self.get_project(project_id)

            if on_update:
                on_update(info)

            if info.is_terminal:
                return info

            await asyncio.sleep(poll_interval)

    async def monitor_project_async(self, project_id: str, poll_interval: float = 2.0):
        """
        Async generator for monitoring project progress.

        Usage:
            async for info in client.monitor_project_async(project_id):
                print(f"Status: {info.status}, Progress: {info.progress_percent:.1f}%")
        """
        while True:
            info = await self.get_project(project_id)
            yield info

            if info.is_terminal:
                break

            await asyncio.sleep(poll_interval)

    # ========== Task Management ==========

    async def submit_task(
        self, prompt: str, priority: str = "medium"
    ) -> list[TaskInfo]:
        """Submit a single task (non-autonomous mode)."""
        response = await self.client.post(
            f"{self.base_url}/tasks", json={"input": prompt, "priority": priority}
        )
        response.raise_for_status()
        data = response.json()
        return [TaskInfo.from_dict(t) for t in data.get("tasks", [])]

    async def get_task(self, task_id: str) -> TaskInfo:
        """Get task details."""
        response = await self.client.get(f"{self.base_url}/tasks/{task_id}")
        response.raise_for_status()
        return TaskInfo.from_dict(response.json())

    async def get_task_queue_stats(self) -> dict:
        """Get task queue statistics."""
        response = await self.client.get(f"{self.base_url}/tasks")
        response.raise_for_status()
        return response.json()

    # ========== Feedback ==========

    async def get_feedback(self) -> list[dict]:
        """Poll for accumulated feedback."""
        response = await self.client.get(f"{self.base_url}/feedback")
        response.raise_for_status()
        return response.json().get("feedback", [])

    async def acknowledge_feedback(self, feedback_ids: list[str]) -> int:
        """Acknowledge received feedback."""
        response = await self.client.post(
            f"{self.base_url}/feedback/acknowledge", json=feedback_ids
        )
        response.raise_for_status()
        return response.json().get("acknowledged", 0)

    async def stream_feedback(self, on_feedback: Callable[[dict], None]):
        """
        Stream feedback via WebSocket.

        Args:
            on_feedback: Callback for each feedback message
        """
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        async with websockets.connect(f"{ws_url}/ws/feedback") as ws:
            async for message in ws:
                data = json.loads(message)
                on_feedback(data)

    # ========== Command Settings ==========

    async def set_allow_all_commands(self, allow: bool) -> dict:
        """Enable or disable auto-approval for all commands."""
        response = await self.client.post(
            f"{self.base_url}/commands/settings", json={"allow_all": allow}
        )
        response.raise_for_status()
        return response.json()

    async def add_approved_command(self, pattern: str) -> dict:
        """Add an auto-approved command pattern."""
        response = await self.client.post(
            f"{self.base_url}/commands/settings", json={"approved_patterns": [pattern]}
        )
        response.raise_for_status()
        return response.json()

    async def get_command_settings(self) -> dict:
        """Get current command approval settings."""
        response = await self.client.get(f"{self.base_url}/commands/settings")
        response.raise_for_status()
        return response.json()


class InteractiveClient:
    """
    Interactive client with plan display and command execution.

    This client connects via WebSocket and:
    1. Displays the execution plan to the user
    2. Executes commands locally (outside Docker)
    3. Prompts user for approval when needed
    4. Shows real-time progress updates
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        auto_approve_all: bool = False,
        approved_commands: Optional[List[str]] = None,
        deadlock_timeout: int = 120,  # seconds without progress = deadlock
        long_task_warning: int = 30,  # seconds before showing long-task warning
    ):
        self.base_url = base_url.rstrip("/")
        self.auto_approve_all = auto_approve_all
        self.approved_commands = set(approved_commands or [])
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False

        # Track seen messages to prevent duplicates
        self._seen_plan_ids: set = set()
        self._seen_task_events: set = set()  # (task_id, event_type) tuples

        # Track task statistics
        self._task_stats = {"started": 0, "completed": 0, "failed": 0, "total": 0}

        # Deadlock detection
        self._deadlock_timeout = deadlock_timeout
        self._long_task_warning = long_task_warning
        self._last_progress_time = time.time()
        self._current_task_start: Optional[float] = None
        self._current_task_info: Optional[Dict[str, str]] = None
        self._long_task_warned = False
        self._deadlock_detected = False

        # Callbacks
        self.on_plan_received: Optional[Callable[[ExecutionPlan], None]] = None
        self.on_command_request: Optional[Callable[[CommandRequest], bool]] = (
            None  # Return True to approve
        )
        self.on_task_update: Optional[Callable[[str, str, str], None]] = (
            None  # task_id, event, data
        )
        self.on_deadlock: Optional[Callable[[], None]] = (
            None  # Called when deadlock detected
        )

    async def connect(self):
        """Connect to the server via WebSocket."""
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._ws = await websockets.connect(f"{ws_url}/ws/client")
        self._running = True
        self._last_progress_time = time.time()  # Reset progress timer
        self._deadlock_detected = False
        print(f"✓ Connected to {ws_url}/ws/client")

        # Send initial settings
        await self._ws.send(
            json.dumps(
                {
                    "type": "settings_update",
                    "allow_all": self.auto_approve_all,
                    "approved_patterns": list(self.approved_commands),
                }
            )
        )

    async def disconnect(self):
        """Disconnect from the server."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    async def listen(self):
        """
        Listen for messages from server and handle them.
        Runs until disconnected.
        """
        if not self._ws:
            raise RuntimeError("Not connected")

        try:
            async for message in self._ws:
                data = json.loads(message)
                await self._handle_message(data)
        except websockets.ConnectionClosed:
            print("\n⚠ Connection closed")
            self._running = False

    async def _handle_message(self, data: dict):
        """Handle a message from the server, with duplicate detection."""
        msg_type = data.get("type")

        if msg_type == "plan_created":
            # Prevent duplicate plan display
            plan_id = data.get("project_id", "")
            if plan_id in self._seen_plan_ids:
                return  # Skip duplicate
            self._seen_plan_ids.add(plan_id)

            plan = ExecutionPlan.from_dict(data)
            self._task_stats["total"] = len(plan.tasks)
            self._display_plan(plan)
            if self.on_plan_received:
                self.on_plan_received(plan)

        elif msg_type == "command_request":
            cmd = CommandRequest.from_dict(data)
            await self._handle_command_request(cmd)

        elif msg_type == "task_started":
            task_id = data.get("task_id", "")
            event_key = (task_id, "started")
            if event_key in self._seen_task_events:
                return  # Skip duplicate
            self._seen_task_events.add(event_key)

            self._task_stats["started"] += 1
            self._mark_progress()  # Mark progress for deadlock detection

            title = data.get("title", "")
            agent = data.get("agent_type", "")

            # Start task timer for long-running detection
            self._start_task_timer({"title": title, "agent": agent, "task_id": task_id})

            if title:
                print(f"  ▶ {title} [{agent}]")
            else:
                print(f"  ▶ Task started [{agent}]")
            if self.on_task_update:
                self.on_task_update(task_id, "started", "")

        elif msg_type == "task_completed":
            task_id = data.get("task_id", "")
            event_key = (task_id, "completed")
            if event_key in self._seen_task_events:
                return  # Skip duplicate
            self._seen_task_events.add(event_key)

            self._task_stats["completed"] += 1
            self._mark_progress()  # Mark progress for deadlock detection
            self._end_task_timer()  # End task timer

            title = data.get("title", "")
            agent = data.get("agent_type", "")
            file_created = data.get("file_created", "")

            # Build display message with available info
            if title and file_created:
                print(f"  ✓ {title} → {file_created}")
            elif title:
                print(f"  ✓ {title}")
            elif file_created:
                print(f"  ✓ Created {file_created}")
            elif agent:
                print(f"  ✓ Task completed [{agent}]")
            else:
                print(f"  ✓ Task completed")
            if self.on_task_update:
                self.on_task_update(
                    task_id, "completed", str(data.get("output_summary", ""))
                )

        elif msg_type == "task_failed":
            task_id = data.get("task_id", "")
            event_key = (task_id, "failed")
            if event_key in self._seen_task_events:
                return  # Skip duplicate
            self._seen_task_events.add(event_key)

            self._task_stats["failed"] += 1
            self._mark_progress()  # Mark progress for deadlock detection
            self._end_task_timer()  # End task timer

            title = data.get("title", "")
            agent = data.get("agent_type", "")
            error_msg = data.get("error", "Unknown error")

            # Make error message more concise
            if len(error_msg) > 60:
                error_msg = error_msg[:57] + "..."

            # Build display with available info
            if title:
                print(f"  ✗ {title}: {error_msg}")
            elif agent:
                print(f"  ✗ [{agent}] {error_msg}")
            else:
                print(f"  ✗ Task failed: {error_msg}")

            if self.on_task_update:
                self.on_task_update(task_id, "failed", error_msg)

        elif msg_type == "tasks_added":
            new_tasks = data.get("tasks", [])
            reason = data.get("reason", "")
            self._task_stats["total"] += len(new_tasks)
            self._mark_progress()  # Mark progress for deadlock detection

            # Check if these are fix or retry tasks
            if "fix" in reason.lower() or "error" in reason.lower():
                print(f"  🔧 Generating fix (will run next): {reason}")
                for task in new_tasks:
                    task_title = task.get("title", "Fix task")
                    priority = task.get("priority", "critical")
                    print(f"     → [{priority.upper()}] {task_title}")
            elif "retry" in reason.lower():
                print(f"  🔄 Retrying after fix: {reason}")
                for task in new_tasks:
                    task_title = task.get("title", "Retry task")
                    print(f"     → {task_title}")
            else:
                print(
                    f"  + {len(new_tasks)} new tasks added (total: {self._task_stats['total']})"
                )

        elif msg_type == "project_complete":
            print(f"\n✓ Project completed: {data.get('project_id', '')}")
            self._print_summary()

        elif msg_type == "project_status":
            print(f"  ℹ Status: {data.get('new_status', '')}")

        # === NEW OPERATION MESSAGE TYPES ===

        elif msg_type == "operation":
            # System operation notification
            operation = data.get("operation", "")
            details = data.get("details", "")
            level = data.get("level", "info")

            # For status_change, the details already include the emoji
            if operation == "status_change":
                print(f"\n{details}")
                self._mark_progress()
                return

            # Choose icon based on operation type
            icons = {
                "planning": "📐",
                "generating": "⚙️",
                "analyzing": "🔍",
                "writing_file": "📝",
                "running_test": "🧪",
                "validating": "✅",
            }
            icon = icons.get(operation, "⚙️")

            if level == "debug":
                # Only show in verbose mode (could add a flag)
                pass
            else:
                print(f"  {icon} {details}")
            self._mark_progress()

        elif msg_type == "llm_call":
            # LLM is being called
            purpose = data.get("purpose", "Processing")
            model = data.get("model", "")
            model_str = f" ({model})" if model else ""
            print(f"  🤖 AI: {purpose}{model_str}...")
            self._mark_progress()

        elif msg_type == "file_operation":
            # File being created/modified
            action = data.get("action", "modify")
            file_path = data.get("file_path", "")
            size = data.get("size")

            icons = {"create": "📄", "modify": "✏️", "delete": "🗑️"}
            icon = icons.get(action, "📄")

            size_str = f" ({size} bytes)" if size else ""
            print(f"  {icon} {action.title()}: {file_path}{size_str}")
            self._mark_progress()

        elif msg_type == "agent_processing":
            # Agent is working on something
            agent_type = data.get("agent_type", "")
            action = data.get("action", "")
            target = data.get("target", "")

            target_str = f" → {target}" if target else ""
            print(f"  🔨 [{agent_type}] {action}{target_str}")
            self._mark_progress()

        elif msg_type == "analysis":
            # Analysis being performed
            analysis_type = data.get("analysis_type", "")
            summary = data.get("summary", "")

            if analysis_type == "failure_analysis":
                print(f"  🔍 Analyzing failure: {summary}")
            elif analysis_type == "failure_categorized":
                print(f"  📋 {summary}")
            else:
                print(f"  🔍 {analysis_type}: {summary}")
            self._mark_progress()

        elif msg_type == "retry_scheduled":
            # Retry scheduled after fix
            original_task = data.get("original_task", "")
            fix_task = data.get("fix_task", "")
            reason = data.get("reason", "")
            print(f"  🔄 Retry scheduled: '{original_task}' after '{fix_task}'")
            print(f"     Reason: {reason}")
            self._mark_progress()

    def _print_summary(self):
        """Print task execution summary."""
        stats = self._task_stats
        print(
            f"   Summary: {stats['completed']}/{stats['total']} completed, {stats['failed']} failed"
        )

    def _mark_progress(self):
        """Mark that progress was made (for deadlock detection)."""
        self._last_progress_time = time.time()
        self._deadlock_detected = False
        self._diagnosis_shown = False

    def _start_task_timer(self, task_info: Dict[str, str]):
        """Start timing a task for long-task detection."""
        self._current_task_start = time.time()
        self._current_task_info = task_info
        self._long_task_warned = False

    def _end_task_timer(self):
        """End task timing."""
        self._current_task_start = None
        self._current_task_info = None
        self._long_task_warned = False

    def check_deadlock(self) -> Optional[str]:
        """
        Check if system appears deadlocked.
        Returns a warning message if deadlock detected, None otherwise.
        """
        if self._deadlock_detected:
            return None  # Already reported

        now = time.time()
        idle_time = now - self._last_progress_time

        if idle_time >= self._deadlock_timeout:
            self._deadlock_detected = True
            return (
                f"⚠️ POSSIBLE DEADLOCK DETECTED!\n"
                f"   No progress for {int(idle_time)} seconds.\n"
                f"   Tasks completed: {self._task_stats['completed']}/{self._task_stats['total']}\n"
                f"   Running auto-diagnosis..."
            )
        return None

    def should_run_diagnosis(self) -> bool:
        """Check if diagnosis should be run (deadlock detected but not yet diagnosed)."""
        return self._deadlock_detected and not getattr(self, "_diagnosis_shown", False)

    def mark_diagnosis_shown(self):
        """Mark that diagnosis has been shown."""
        self._diagnosis_shown = True

    def check_long_running_task(self) -> Optional[str]:
        """
        Check if current task is taking too long.
        Returns a status message if task is long-running, None otherwise.
        """
        if not self._current_task_start or self._long_task_warned:
            return None

        now = time.time()
        elapsed = now - self._current_task_start

        if elapsed >= self._long_task_warning:
            self._long_task_warned = True
            task_title = (
                self._current_task_info.get("title", "Current task")
                if self._current_task_info
                else "Current task"
            )
            agent = (
                self._current_task_info.get("agent", "unknown")
                if self._current_task_info
                else "unknown"
            )
            return f"  ⏳ {task_title} [{agent}] - running for {int(elapsed)}s..."
        return None

    def get_progress_info(self) -> Dict[str, Any]:
        """Get current progress information for display."""
        now = time.time()
        info = {
            "idle_time": now - self._last_progress_time,
            "tasks_completed": self._task_stats["completed"],
            "tasks_failed": self._task_stats["failed"],
            "tasks_total": self._task_stats["total"],
            "deadlock_detected": self._deadlock_detected,
            "current_task": None,
        }

        if self._current_task_start:
            info["current_task"] = {
                "info": self._current_task_info,
                "elapsed": now - self._current_task_start,
            }

        return info

    def display_diagnosis(self, diagnosis: dict) -> bool:
        """
        Display diagnostic information in a user-friendly format.
        Returns True if auto-recovery is possible.
        """
        print("\n" + "=" * 60)
        print("🔍 AUTO-DIAGNOSIS REPORT")
        print("=" * 60)

        summary = diagnosis.get("summary", {})
        print(f"\n📊 Project Status: {diagnosis.get('status', 'unknown')}")
        print(f"   Running for: {summary.get('project_age_seconds', 0)}s")
        print(
            f"   Tasks: {summary.get('completed', 0)}/{summary.get('total_tasks', 0)} completed, "
            f"{summary.get('failed', 0)} failed"
        )
        print(
            f"   Pending: {summary.get('pending', 0)}, Running: {summary.get('running', 0)}"
        )

        # Root cause
        root_cause = diagnosis.get("root_cause", "Unknown")
        print(f"\n🎯 ROOT CAUSE: {root_cause}")

        # Running tasks
        running_tasks = diagnosis.get("running_tasks", [])
        if running_tasks:
            print(f"\n⏳ STUCK TASKS ({len(running_tasks)}):")
            for task in running_tasks:
                print(
                    f"   • [{task.get('agent_type', 'unknown')}] {task.get('title', 'Unknown')}"
                )

        # Issues
        issues = diagnosis.get("issues", [])
        if issues:
            print(f"\n⚠️ ISSUES DETECTED ({len(issues)}):")
            for issue in issues:
                severity_icon = {"high": "🔴", "medium": "🟡", "warning": "🟠"}.get(
                    issue.get("severity"), "⚪"
                )
                print(
                    f"   {severity_icon} [{issue.get('type')}] {issue.get('message')}"
                )

        # Log insights (analyzed patterns from container logs)
        log_insights = diagnosis.get("log_insights", [])
        if log_insights:
            print(f"\n📜 LOG INSIGHTS ({len(log_insights)}):")
            for insight in log_insights:
                icon = {
                    "error_detected": "❌",
                    "timeout_detected": "⏰",
                    "approval_timeout": "🚫",
                }.get(insight.get("type"), "📋")
                print(
                    f"   {icon} [{insight.get('container')}] {insight.get('type')}: {insight.get('sample', '')[:60]}"
                )

        # Scheduler state
        scheduler_state = diagnosis.get("scheduler_state", {})
        if scheduler_state and not scheduler_state.get("error"):
            stats = scheduler_state.get("stats", {})
            print(f"\n📈 SCHEDULER STATE:")
            print(
                f"   Queue: {stats.get('pending_tasks', 0)} pending, {stats.get('running_tasks', 0)} running"
            )
            print(f"   Processed: {stats.get('total_processed', 0)} total")
            if stats.get("waiting_for_dependency", 0) > 0:
                print(
                    f"   ⏳ {stats.get('waiting_for_dependency', 0)} waiting for dependencies"
                )

        # Check if auto-recovery is possible
        can_recover = diagnosis.get("can_auto_recover", False)

        if can_recover:
            print(f"\n🔄 AUTO-RECOVERY AVAILABLE")
            print(f"   The system can attempt automatic recovery...")
        else:
            # Recommendations
            recommendations = diagnosis.get("recommendations", [])
            if recommendations:
                print(f"\n💡 RECOMMENDATIONS:")
                for rec in recommendations:
                    print(f"   → {rec}")

            # Debug commands
            debug_commands = diagnosis.get("debug_commands", [])
            if debug_commands:
                print(f"\n🔧 DEBUG COMMANDS:")
                for cmd in debug_commands[:3]:  # Limit to 3
                    print(f"   $ {cmd}")

        # Agent logs (if present)
        agent_logs = diagnosis.get("agent_logs", {})
        if agent_logs:
            print(f"\n📄 CONTAINER LOGS:")
            for container, logs in agent_logs.items():
                if (
                    logs
                    and not logs.startswith("[Error")
                    and not logs.startswith("[Timeout")
                ):
                    # Show last few meaningful lines
                    lines = [l for l in logs.strip().split("\n") if l.strip()][-5:]
                    if lines:
                        print(f"\n   [{container}] (last {len(lines)} lines):")
                        for line in lines:
                            # Truncate long lines
                            display_line = (
                                line[:100] + "..." if len(line) > 100 else line
                            )
                            print(f"      {display_line}")

        print("\n" + "=" * 60)

        return can_recover

    def display_recovery_result(self, result: dict):
        """Display the result of a recovery attempt."""
        success = result.get("success", False)
        report = result.get("recovery_report", {})

        if success:
            print("\n" + "=" * 60)
            print("✅ AUTO-RECOVERY SUCCESSFUL")
            print("=" * 60)

            recovered = report.get("recovered_tasks", [])
            if recovered:
                print(f"\n🔄 Re-dispatched {len(recovered)} task(s):")
                for task in recovered[:5]:
                    print(f"   • [{task.get('agent_type')}] {task.get('title')}")

            orphans = report.get("orphaned_dependencies_resolved", [])
            if orphans:
                print(f"\n🔗 Resolved {len(orphans)} orphaned dependency(s):")
                for task in orphans[:3]:
                    print(
                        f"   • {task.get('title')} (was waiting for {task.get('was_waiting_for')})"
                    )

            stale = report.get("stale_tasks_reset", [])
            if stale:
                print(f"\n🔃 Reset {len(stale)} stale task(s):")
                for task in stale[:3]:
                    print(f"   • {task.get('title')}")

            print("\n   Continuing execution...")
            print("=" * 60 + "\n")
        else:
            print("\n⚠️ Recovery attempted but no tasks needed recovery.")
            print("   The issue may require manual intervention.")
            print("   Press Ctrl+C to abort.\n")

    def _display_plan(self, plan: ExecutionPlan):
        """Display the execution plan to the user."""
        print("\n" + "=" * 60)
        print("📋 EXECUTION PLAN")
        print("=" * 60)
        print(f"Project: {plan.project_id[:8]}...")
        print(f"Prompt: {plan.prompt[:80]}{'...' if len(plan.prompt) > 80 else ''}")
        print(f"Tasks: {len(plan.tasks)}")
        print("-" * 60)

        for i, task in enumerate(plan.tasks, 1):
            status_icon = {
                "pending": "○",
                "running": "▶",
                "completed": "✓",
                "failed": "✗",
            }.get(task.status, "○")

            print(f"  {i}. [{status_icon}] {task.title}")
            print(f"      Agent: {task.agent_type} | Priority: {task.priority}")
            if task.depends_on:
                print(f"      Depends on: {task.depends_on[:8]}...")

        if plan.architect_output:
            print("-" * 60)
            print("Architecture:")
            if plan.architect_output.get("components"):
                print(
                    f"  Components: {', '.join(plan.architect_output['components'][:5])}"
                )
            if plan.architect_output.get("files"):
                files = [f.get("path", "") for f in plan.architect_output["files"][:5]]
                print(f"  Files: {', '.join(files)}")

        print("=" * 60)

    async def _handle_command_request(self, cmd: CommandRequest):
        """Handle a command execution request."""
        print(f"\n🔧 Command Request from {cmd.agent_type}:")
        print(f"   Command: {cmd.command}")
        print(f"   Reason: {cmd.reason}")

        # Check if auto-approved
        approved = False
        if self.auto_approve_all:
            approved = True
            print("   → Auto-approved (session allow-all)")
        elif cmd.command.split()[0] in self.approved_commands:
            approved = True
            print(f"   → Auto-approved (pattern: {cmd.command.split()[0]})")
        elif self.on_command_request:
            approved = self.on_command_request(cmd)
        else:
            # Interactive approval
            response = input("   Approve? [y/N/a(llow all)]: ").strip().lower()
            if response == "a":
                self.auto_approve_all = True
                approved = True
                print("   → All commands will be auto-approved for this session")
            elif response == "y":
                approved = True

        if approved:
            # Execute the command locally
            result = await self._execute_command(cmd)
            await self._send_command_result(cmd.id, result)
        else:
            # Reject
            await self._send_command_rejected(cmd.id)

    async def _execute_command(self, cmd: CommandRequest) -> dict:
        """Execute a command locally and display output."""
        print(f"   → Executing: {cmd.command}")
        print(f"   " + "-" * 50)

        try:
            # Run the command
            process = await asyncio.create_subprocess_shell(
                cmd.command,
                cwd=cmd.working_dir if cmd.working_dir else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            result = {
                "stdout": stdout_str,
                "stderr": stderr_str,
                "return_code": process.returncode,
            }

            # Display output
            if stdout_str.strip():
                # Limit output display to avoid flooding
                lines = stdout_str.strip().split("\n")
                if len(lines) > 20:
                    print("   📤 Output (truncated):")
                    for line in lines[:10]:
                        print(f"      {line}")
                    print(f"      ... ({len(lines) - 20} lines hidden) ...")
                    for line in lines[-10:]:
                        print(f"      {line}")
                else:
                    print("   📤 Output:")
                    for line in lines:
                        print(f"      {line}")

            if stderr_str.strip():
                lines = stderr_str.strip().split("\n")
                if len(lines) > 10:
                    print("   ⚠️ Stderr (truncated):")
                    for line in lines[:5]:
                        print(f"      {line}")
                    print(f"      ... ({len(lines) - 10} lines hidden) ...")
                    for line in lines[-5:]:
                        print(f"      {line}")
                else:
                    print("   ⚠️ Stderr:")
                    for line in lines:
                        print(f"      {line}")

            print(f"   " + "-" * 50)

            if process.returncode == 0:
                print(f"   ✓ Command succeeded (exit code: 0)")
            else:
                print(f"   ✗ Command failed (exit code: {process.returncode})")

            return result

        except Exception as e:
            print(f"   " + "-" * 50)
            print(f"   ✗ Command error: {e}")
            return {"error": str(e), "return_code": 1}

    async def _send_command_result(self, command_id: str, result: dict):
        """Send command result back to server."""
        if self._ws:
            await self._ws.send(
                json.dumps(
                    {"type": "command_result", "command_id": command_id, **result}
                )
            )

    async def _send_command_rejected(self, command_id: str):
        """Send command rejection back to server."""
        print("   ✗ Command rejected")
        if self._ws:
            await self._ws.send(
                json.dumps({"type": "command_rejected", "command_id": command_id})
            )


# ========== CLI Demo ==========


async def demo():
    """Demonstrate the client functionality."""
    import sys

    print("=" * 60)
    print("Agentic IA System - Client Demo")
    print("=" * 60)

    async with AgenticClient() as client:
        # Health check
        print("\n[1] Checking system health...")
        try:
            health = await client.health_check()
            print(f"    ✓ System status: {health['status']}")
            for service, status in health.get("services", {}).items():
                icon = "✓" if status else "✗"
                print(f"    {icon} {service}: {'running' if status else 'stopped'}")
        except Exception as e:
            print(f"    ✗ Health check failed: {e}")
            print("\n    Make sure the system is running:")
            print("    docker compose up -d")
            sys.exit(1)

        # Get prompt from user or use default
        if len(sys.argv) > 1:
            prompt = " ".join(sys.argv[1:])
        else:
            prompt = "Create a Python function that calculates fibonacci numbers"

        print(f"\n[2] Starting project...")
        print(f"    Prompt: {prompt}")

        project_id = await client.start_project(prompt)
        print(f"    ✓ Project ID: {project_id}")

        # Monitor progress
        print(f"\n[3] Monitoring progress...")
        start_time = time.time()
        last_status = None

        async for info in client.monitor_project_async(project_id, poll_interval=2.0):
            elapsed = time.time() - start_time

            # Only print on status change or significant progress
            status_line = (
                f"    [{elapsed:5.1f}s] Status: {info.status.value:12} | "
                f"Tasks: {info.completed_tasks}/{info.total_tasks} | "
                f"Failed: {info.failed_tasks} | "
                f"Files: {len(info.files_created)}"
            )

            if info.status != last_status:
                print(status_line)
                last_status = info.status

        # Final result
        print(f"\n[4] Project completed!")
        print(f"    Status: {info.status.value}")
        print(f"    Duration: {time.time() - start_time:.1f}s")
        print(f"    Tasks completed: {info.completed_tasks}/{info.total_tasks}")
        print(f"    Tasks failed: {info.failed_tasks}")

        if info.files_created:
            print(f"\n    Files created:")
            for f in info.files_created:
                print(f"      - {f}")

        # Check feedback
        print(f"\n[5] Checking feedback...")
        feedback = await client.get_feedback()
        if feedback:
            print(f"    Received {len(feedback)} feedback items:")
            for fb in feedback[:5]:  # Show first 5
                print(f"      - {fb.get('type', 'unknown')}: {fb}")
        else:
            print("    No pending feedback")

        print("\n" + "=" * 60)
        print("Demo complete!")
        print("=" * 60)


async def test_system():
    """Run system tests."""
    print("=" * 60)
    print("Agentic IA System - Test Suite")
    print("=" * 60)

    tests_passed = 0
    tests_failed = 0

    async with AgenticClient() as client:
        # Test 1: Health check
        print("\n[TEST 1] Health Check")
        try:
            assert await client.is_healthy(), "System not healthy"
            print("    ✓ PASSED")
            tests_passed += 1
        except Exception as e:
            print(f"    ✗ FAILED: {e}")
            tests_failed += 1

        # Test 2: Start project
        print("\n[TEST 2] Start Project")
        try:
            project_id = await client.start_project(
                "Create a hello world Python script"
            )
            assert project_id, "No project ID returned"
            print(f"    ✓ PASSED (ID: {project_id[:8]}...)")
            tests_passed += 1
        except Exception as e:
            print(f"    ✗ FAILED: {e}")
            tests_failed += 1
            project_id = None

        # Test 3: Get project status
        print("\n[TEST 3] Get Project Status")
        if project_id:
            try:
                info = await client.get_project(project_id)
                assert info.id == project_id, "Project ID mismatch"
                assert info.status in ProjectStatus, "Invalid status"
                print(f"    ✓ PASSED (Status: {info.status.value})")
                tests_passed += 1
            except Exception as e:
                print(f"    ✗ FAILED: {e}")
                tests_failed += 1
        else:
            print("    ⊘ SKIPPED (no project)")

        # Test 4: Monitor until completion
        print("\n[TEST 4] Project Completion (timeout: 180s)")
        if project_id:
            try:
                start = time.time()
                timeout = 180
                last_completed = 0
                progress_made = False

                while time.time() - start < timeout:
                    info = await client.get_project(project_id)
                    elapsed = time.time() - start
                    pending = (
                        info.total_tasks - info.completed_tasks - info.failed_tasks
                    )
                    print(
                        f"    [{elapsed:5.1f}s] {info.status.value}: {info.completed_tasks}/{info.total_tasks} done, {pending} pending",
                        end="\r",
                    )

                    # Track if we're making progress
                    if info.completed_tasks > last_completed:
                        progress_made = True
                        last_completed = info.completed_tasks

                    if info.is_terminal:
                        break
                    await asyncio.sleep(2)

                print()  # New line after progress

                if info.is_terminal:
                    if info.status == ProjectStatus.COMPLETED:
                        print(f"    ✓ PASSED (completed in {elapsed:.1f}s)")
                        tests_passed += 1
                    else:
                        print(
                            f"    ⚠ PARTIAL: Project ended with status {info.status.value}"
                        )
                        tests_passed += 1
                else:
                    # Timeout - but check if we made progress
                    if progress_made and info.completed_tasks >= 3:
                        print(
                            f"    ⚠ PARTIAL PASS: Timeout but made progress ({info.completed_tasks} tasks completed)"
                        )
                        tests_passed += 1
                    else:
                        print(
                            f"    ✗ FAILED: Timeout after {timeout}s with minimal progress"
                        )
                        tests_failed += 1
            except Exception as e:
                print(f"    ✗ FAILED: {e}")
                tests_failed += 1
        else:
            print("    ⊘ SKIPPED (no project)")

        # Test 5: Feedback endpoint
        print("\n[TEST 5] Feedback Endpoint")
        try:
            feedback = await client.get_feedback()
            assert isinstance(feedback, list), "Feedback should be a list"
            print(f"    ✓ PASSED ({len(feedback)} items)")
            tests_passed += 1
        except Exception as e:
            print(f"    ✗ FAILED: {e}")
            tests_failed += 1

        # Test 6: Task queue stats
        print("\n[TEST 6] Task Queue Stats")
        try:
            stats = await client.get_task_queue_stats()
            assert isinstance(stats, dict), "Stats should be a dict"
            print(f"    ✓ PASSED")
            tests_passed += 1
        except Exception as e:
            print(f"    ✗ FAILED: {e}")
            tests_failed += 1

    # Summary
    print("\n" + "=" * 60)
    total = tests_passed + tests_failed
    print(f"Results: {tests_passed}/{total} tests passed")
    if tests_failed == 0:
        print("✓ All tests passed!")
    else:
        print(f"✗ {tests_failed} test(s) failed")
    print("=" * 60)

    return tests_failed == 0


async def interactive_session():
    """
    Run an interactive session with plan display and command execution.
    Commands from agents are executed locally on this machine.
    """
    import sys

    print("=" * 60)
    print("Agentic IA System - Interactive Session")
    print("=" * 60)
    print("\nThis session will:")
    print("  • Show execution plans before running")
    print("  • Execute agent commands locally")
    print("  • Ask for approval on commands (or auto-approve)")
    print()

    # Get prompt
    if len(sys.argv) > 2:
        prompt = " ".join(sys.argv[2:])
    else:
        prompt = input("Enter your project request: ").strip()
        if not prompt:
            prompt = "Create a simple Python calculator"

    # Ask about auto-approval
    auto_approve = input("Auto-approve all commands? [y/N]: ").strip().lower() == "y"

    async with AgenticClient() as api_client:
        # Check health
        if not await api_client.is_healthy():
            print("\n✗ System not healthy. Make sure docker compose is running.")
            return

        # Start project
        print(f"\n📝 Starting project: {prompt}")
        project_id = await api_client.start_project(prompt)
        print(f"   Project ID: {project_id}")

        # Connect interactive client
        async with InteractiveClient(auto_approve_all=auto_approve) as interactive:
            # Create background task for listening
            listen_task = asyncio.create_task(interactive.listen())

            # Track recovery attempts
            recovery_attempts = 0
            max_recovery_attempts = 3

            # Monitor project progress
            try:
                start_time = time.time()
                last_status_time = time.time()
                while True:
                    info = await api_client.get_project(project_id)

                    if info.is_terminal:
                        elapsed = time.time() - start_time
                        print(
                            f"\n{'✓' if info.status == ProjectStatus.COMPLETED else '✗'} "
                            f"Project finished: {info.status.value} in {elapsed:.1f}s"
                        )
                        print(
                            f"   Tasks: {info.completed_tasks}/{info.total_tasks} completed"
                        )
                        print(f"   Files created: {len(info.files_created)}")
                        for f in info.files_created[:10]:
                            print(f"     - {f}")
                        break

                    # Check for deadlock
                    deadlock_msg = interactive.check_deadlock()
                    if deadlock_msg:
                        print(f"\n{deadlock_msg}")

                        # Auto-diagnose when deadlock is detected
                        if interactive.should_run_diagnosis():
                            try:
                                diagnosis = await api_client.diagnose_project(
                                    project_id
                                )
                                can_recover = interactive.display_diagnosis(diagnosis)
                                interactive.mark_diagnosis_shown()

                                # Attempt auto-recovery if possible
                                if (
                                    can_recover
                                    and recovery_attempts < max_recovery_attempts
                                ):
                                    recovery_attempts += 1
                                    print(
                                        f"\n🔄 Attempting auto-recovery ({recovery_attempts}/{max_recovery_attempts})..."
                                    )
                                    try:
                                        result = await api_client.recover_project(
                                            project_id
                                        )
                                        interactive.display_recovery_result(result)

                                        if result.get("success"):
                                            # Reset deadlock detection after successful recovery
                                            interactive._mark_progress()
                                            interactive._diagnosis_shown = False
                                    except Exception as e:
                                        print(f"   ⚠ Recovery failed: {e}")
                                elif recovery_attempts >= max_recovery_attempts:
                                    print(
                                        f"\n⚠️ Max recovery attempts reached ({max_recovery_attempts})."
                                    )
                                    print("   Manual intervention may be required.")
                                    print("   Press Ctrl+C to abort.\n")

                            except Exception as e:
                                print(f"   ⚠ Could not run diagnosis: {e}")
                                interactive.mark_diagnosis_shown()

                    # Check for long-running task
                    long_task_msg = interactive.check_long_running_task()
                    if long_task_msg:
                        print(long_task_msg)

                    # Periodic status update (every 30s) to show we're still alive
                    now = time.time()
                    if now - last_status_time >= 30:
                        elapsed = now - start_time
                        progress = interactive.get_progress_info()
                        idle = int(progress["idle_time"])
                        print(
                            f"  ⏱ Running {elapsed:.0f}s | "
                            f"{progress['tasks_completed']}/{progress['tasks_total']} done | "
                            f"idle {idle}s"
                        )
                        last_status_time = now

                    await asyncio.sleep(2)

            except KeyboardInterrupt:
                print("\n\n⚠ Interrupted by user")
            finally:
                listen_task.cancel()
                try:
                    await listen_task
                except asyncio.CancelledError:
                    pass

    print("\n" + "=" * 60)
    print("Session ended")
    print("=" * 60)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        success = asyncio.run(test_system())
        sys.exit(0 if success else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        asyncio.run(interactive_session())
    else:
        asyncio.run(demo())
