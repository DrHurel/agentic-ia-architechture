"""
Command Approval System - Requires user approval for terminal commands.
Supports session-level blanket approval.
"""

import asyncio
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import structlog


logger = structlog.get_logger()


class ApprovalStatus(str, Enum):
    """Status of a command approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class CommandApprovalRequest:
    """A request for command approval."""
    id: str
    command: str
    agent_type: str
    task_id: str
    project_id: Optional[str]
    working_dir: str
    reason: str  # Why the agent wants to run this command
    created_at: datetime = field(default_factory=datetime.now)
    status: ApprovalStatus = ApprovalStatus.PENDING
    response_at: Optional[datetime] = None


class CommandApprovalManager:
    """
    Manages command approval requests and session settings.
    
    Commands can be:
    1. Individually approved/rejected
    2. Auto-approved for the session (user opts in)
    3. Timed out (rejected automatically)
    """
    
    def __init__(self, approval_timeout: float = 300.0):  # 5 minute default
        self._pending_requests: dict[str, CommandApprovalRequest] = {}
        self._approval_events: dict[str, asyncio.Event] = {}
        self._session_allow_all: bool = False
        self._approved_patterns: set[str] = set()  # Patterns that are auto-approved
        self._approval_timeout = approval_timeout
        self._notification_callback: Optional[Callable[[CommandApprovalRequest], Awaitable[None]]] = None
        self._logger = logger.bind(component="CommandApprovalManager")
    
    def on_approval_request(self, callback: Callable[[CommandApprovalRequest], Awaitable[None]]) -> None:
        """Register callback for when approval is needed."""
        self._notification_callback = callback
    
    @property
    def session_allow_all(self) -> bool:
        """Check if session has blanket approval enabled."""
        return self._session_allow_all
    
    def set_session_allow_all(self, allow: bool) -> None:
        """Enable or disable blanket approval for the session."""
        self._session_allow_all = allow
        self._logger.info("Session allow-all changed", allow_all=allow)
    
    def add_approved_pattern(self, pattern: str) -> None:
        """Add a command pattern that's auto-approved (e.g., 'python', 'pytest')."""
        self._approved_patterns.add(pattern)
        self._logger.info("Added approved pattern", pattern=pattern)
    
    def remove_approved_pattern(self, pattern: str) -> None:
        """Remove an auto-approved pattern."""
        self._approved_patterns.discard(pattern)
    
    def _is_auto_approved(self, command: str) -> bool:
        """Check if command matches any auto-approved pattern."""
        if self._session_allow_all:
            return True
        
        cmd_parts = command.split()
        if not cmd_parts:
            return False
        
        base_cmd = cmd_parts[0]
        return base_cmd in self._approved_patterns
    
    async def request_approval(
        self,
        request_id: str,
        command: str,
        agent_type: str,
        task_id: str,
        project_id: Optional[str],
        working_dir: str,
        reason: str
    ) -> tuple[bool, str]:
        """
        Request approval for a command.
        
        Returns:
            (approved: bool, message: str)
        """
        # Check auto-approval
        if self._is_auto_approved(command):
            self._logger.info("Command auto-approved", command=command)
            return True, "Auto-approved (session setting)"
        
        # Create approval request
        request = CommandApprovalRequest(
            id=request_id,
            command=command,
            agent_type=agent_type,
            task_id=task_id,
            project_id=project_id,
            working_dir=working_dir,
            reason=reason
        )
        
        self._pending_requests[request_id] = request
        self._approval_events[request_id] = asyncio.Event()
        
        self._logger.info(
            "Command approval requested",
            request_id=request_id,
            command=command,
            agent=agent_type
        )
        
        # Notify via callback
        if self._notification_callback:
            try:
                await self._notification_callback(request)
            except Exception as e:
                self._logger.error("Notification callback failed", error=str(e))
        
        # Wait for approval with timeout
        try:
            await asyncio.wait_for(
                self._approval_events[request_id].wait(),
                timeout=self._approval_timeout
            )
        except asyncio.TimeoutError:
            request.status = ApprovalStatus.TIMEOUT
            self._cleanup_request(request_id)
            return False, f"Approval timed out after {self._approval_timeout}s"
        
        # Get result
        updated_request = self._pending_requests.get(request_id, request)
        self._cleanup_request(request_id)
        
        if updated_request.status == ApprovalStatus.APPROVED:
            return True, "Approved by user"
        else:
            return False, "Rejected by user"
    
    def approve(self, request_id: str) -> bool:
        """Approve a pending command request."""
        request = self._pending_requests.get(request_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return False
        
        request.status = ApprovalStatus.APPROVED
        request.response_at = datetime.now()
        
        event = self._approval_events.get(request_id)
        if event:
            event.set()
        
        self._logger.info("Command approved", request_id=request_id)
        return True
    
    def reject(self, request_id: str) -> bool:
        """Reject a pending command request."""
        request = self._pending_requests.get(request_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return False
        
        request.status = ApprovalStatus.REJECTED
        request.response_at = datetime.now()
        
        event = self._approval_events.get(request_id)
        if event:
            event.set()
        
        self._logger.info("Command rejected", request_id=request_id)
        return True
    
    def get_pending_requests(self) -> list[CommandApprovalRequest]:
        """Get all pending approval requests."""
        return [r for r in self._pending_requests.values() 
                if r.status == ApprovalStatus.PENDING]
    
    def get_request(self, request_id: str) -> Optional[CommandApprovalRequest]:
        """Get a specific request by ID."""
        return self._pending_requests.get(request_id)
    
    def _cleanup_request(self, request_id: str) -> None:
        """Clean up a processed request."""
        self._approval_events.pop(request_id, None)
        # Keep in pending_requests for history (could add TTL cleanup)
    
    def get_settings(self) -> dict:
        """Get current approval settings."""
        return {
            "session_allow_all": self._session_allow_all,
            "approved_patterns": list(self._approved_patterns),
            "approval_timeout": self._approval_timeout,
            "pending_count": len(self.get_pending_requests())
        }


# Global singleton
_approval_manager: Optional[CommandApprovalManager] = None


def get_approval_manager() -> CommandApprovalManager:
    """Get the global approval manager instance."""
    global _approval_manager
    if _approval_manager is None:
        _approval_manager = CommandApprovalManager()
    return _approval_manager


def reset_approval_manager() -> None:
    """Reset the approval manager (for testing)."""
    global _approval_manager
    _approval_manager = None
