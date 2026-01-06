"""
Client-Side Executor - Sends commands to be executed on client outside Docker.
Commands are sent via WebSocket and results are returned asynchronously.
"""

import asyncio
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid
import structlog


logger = structlog.get_logger()


class CommandStatus(str, Enum):
    """Status of a remote command execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


@dataclass
class RemoteCommand:
    """A command to be executed on the client side."""
    id: str
    command: str
    working_dir: str
    agent_type: str
    task_id: str
    project_id: Optional[str]
    reason: str
    requires_approval: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    status: CommandStatus = CommandStatus.PENDING
    stdout: str = ""
    stderr: str = ""
    return_code: Optional[int] = None
    completed_at: Optional[datetime] = None


class ClientExecutorManager:
    """
    Manages remote command execution on the client.
    Commands are sent via WebSocket and executed outside Docker.
    
    Flow:
    1. Agent requests command execution
    2. Command is sent to client via WebSocket
    3. If approval required, client prompts user
    4. Client executes command and sends result back
    5. Result is returned to the agent
    """
    
    def __init__(self, timeout: float = 300.0):
        self._pending_commands: dict[str, RemoteCommand] = {}
        self._result_events: dict[str, asyncio.Event] = {}
        self._send_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._session_allow_all: bool = False
        self._approved_patterns: set[str] = set()
        self._timeout = timeout
        self._logger = logger.bind(component="ClientExecutor")
    
    def set_send_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set callback for sending commands to client."""
        self._send_callback = callback
    
    @property
    def session_allow_all(self) -> bool:
        return self._session_allow_all
    
    def set_session_allow_all(self, allow: bool) -> None:
        """Enable/disable blanket approval for session."""
        self._session_allow_all = allow
        self._logger.info("Session allow-all changed", allow_all=allow)
    
    def add_approved_pattern(self, pattern: str) -> None:
        """Add auto-approved command pattern."""
        self._approved_patterns.add(pattern)
    
    def remove_approved_pattern(self, pattern: str) -> None:
        """Remove auto-approved pattern."""
        self._approved_patterns.discard(pattern)
    
    def _is_auto_approved(self, command: str) -> bool:
        """Check if command is auto-approved."""
        if self._session_allow_all:
            return True
        cmd_parts = command.split()
        if not cmd_parts:
            return False
        return cmd_parts[0] in self._approved_patterns
    
    async def execute_remote(
        self,
        command: str,
        working_dir: str,
        agent_type: str,
        task_id: str,
        project_id: Optional[str] = None,
        reason: str = "Agent requested command execution"
    ) -> tuple[str, str, int]:
        """
        Execute a command on the client.
        
        Returns:
            (stdout, stderr, return_code)
        """
        command_id = str(uuid.uuid4())
        requires_approval = not self._is_auto_approved(command)
        
        remote_cmd = RemoteCommand(
            id=command_id,
            command=command,
            working_dir=working_dir,
            agent_type=agent_type,
            task_id=task_id,
            project_id=project_id,
            reason=reason,
            requires_approval=requires_approval
        )
        
        self._pending_commands[command_id] = remote_cmd
        self._result_events[command_id] = asyncio.Event()
        
        # Send to client via WebSocket
        if self._send_callback:
            await self._send_callback({
                "type": "command_request",
                "id": command_id,
                "command": command,
                "working_dir": working_dir,
                "agent_type": agent_type,
                "task_id": task_id,
                "project_id": project_id,
                "reason": reason,
                "requires_approval": requires_approval
            })
            self._logger.info(
                "Command sent to client",
                command_id=command_id,
                command=command[:50],
                requires_approval=requires_approval
            )
        else:
            self._logger.error("No WebSocket callback set, cannot execute remote command")
            self._cleanup(command_id)
            raise RuntimeError("No client connected to execute commands")
        
        # Wait for result
        try:
            await asyncio.wait_for(
                self._result_events[command_id].wait(),
                timeout=self._timeout
            )
        except asyncio.TimeoutError:
            remote_cmd.status = CommandStatus.TIMEOUT
            self._cleanup(command_id)
            raise TimeoutError(f"Command timed out after {self._timeout}s")
        
        # Get result
        result_cmd = self._pending_commands.get(command_id, remote_cmd)
        self._cleanup(command_id)
        
        if result_cmd.status == CommandStatus.REJECTED:
            raise PermissionError("Command rejected by user")
        
        return result_cmd.stdout, result_cmd.stderr, result_cmd.return_code or 0
    
    def handle_result(self, command_id: str, result: dict) -> bool:
        """Handle command result from client."""
        if command_id not in self._pending_commands:
            self._logger.warning("Unknown command result", command_id=command_id)
            return False
        
        remote_cmd = self._pending_commands[command_id]
        
        if result.get("rejected"):
            remote_cmd.status = CommandStatus.REJECTED
        elif result.get("error"):
            remote_cmd.status = CommandStatus.FAILED
            remote_cmd.stderr = result.get("error", "")
            remote_cmd.return_code = 1
        else:
            remote_cmd.status = CommandStatus.COMPLETED
            remote_cmd.stdout = result.get("stdout", "")
            remote_cmd.stderr = result.get("stderr", "")
            remote_cmd.return_code = result.get("return_code", 0)
        
        remote_cmd.completed_at = datetime.now()
        
        # Signal completion
        event = self._result_events.get(command_id)
        if event:
            event.set()
        
        self._logger.info(
            "Command result received",
            command_id=command_id,
            status=remote_cmd.status.value
        )
        return True
    
    def _cleanup(self, command_id: str) -> None:
        """Clean up command tracking."""
        self._pending_commands.pop(command_id, None)
        self._result_events.pop(command_id, None)
    
    def get_pending_commands(self) -> list[RemoteCommand]:
        """Get all pending commands."""
        return [
            cmd for cmd in self._pending_commands.values()
            if cmd.status == CommandStatus.PENDING
        ]
    
    def get_settings(self) -> dict:
        """Get current settings."""
        return {
            "session_allow_all": self._session_allow_all,
            "approved_patterns": list(self._approved_patterns),
            "timeout": self._timeout,
            "pending_count": len([c for c in self._pending_commands.values() 
                                  if c.status == CommandStatus.PENDING])
        }


# Global singleton
_client_executor: Optional[ClientExecutorManager] = None


def get_client_executor() -> ClientExecutorManager:
    """Get the global ClientExecutorManager instance."""
    global _client_executor
    if _client_executor is None:
        _client_executor = ClientExecutorManager()
    return _client_executor
