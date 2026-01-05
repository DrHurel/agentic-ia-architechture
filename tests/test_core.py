"""
Tests for core interfaces and base classes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.interfaces import (
    Task, TaskResult, TaskStatus, TaskPriority, AgentType
)


class TestTask:
    """Tests for Task model."""
    
    def test_task_creation(self):
        task = Task(
            id="test-1",
            title="Test Task",
            description="A test task",
            agent_type=AgentType.CODE_WRITER
        )
        
        assert task.id == "test-1"
        assert task.title == "Test Task"
        assert task.agent_type == AgentType.CODE_WRITER
        assert task.status == TaskStatus.PENDING
        assert task.priority == TaskPriority.MEDIUM
    
    def test_task_with_priority(self):
        task = Task(
            id="test-2",
            title="Critical Task",
            description="A critical task",
            agent_type=AgentType.CICD,
            priority=TaskPriority.CRITICAL
        )
        
        assert task.priority == TaskPriority.CRITICAL
    
    def test_task_with_payload(self):
        task = Task(
            id="test-3",
            title="Task with Payload",
            description="Has extra data",
            agent_type=AgentType.ARCHITECT,
            payload={"file_path": "test.py"}
        )
        
        assert task.payload["file_path"] == "test.py"


class TestTaskResult:
    """Tests for TaskResult model."""
    
    def test_successful_result(self):
        result = TaskResult(
            task_id="test-1",
            status=TaskStatus.COMPLETED,
            output={"message": "Success"}
        )
        
        assert result.status == TaskStatus.COMPLETED
        assert result.error is None
    
    def test_failed_result(self):
        result = TaskResult(
            task_id="test-2",
            status=TaskStatus.FAILED,
            error="Something went wrong"
        )
        
        assert result.status == TaskStatus.FAILED
        assert result.error == "Something went wrong"


class TestAgentType:
    """Tests for AgentType enum."""
    
    def test_all_agent_types_exist(self):
        expected_types = [
            "code_writer", "architect", "code_quality",
            "tester_whitebox", "tester_blackbox", "cicd"
        ]
        
        for agent_type in expected_types:
            assert AgentType(agent_type)
