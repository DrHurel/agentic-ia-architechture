"""
Tests for the rule enforcer.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.interfaces import Task, AgentType, TaskPriority
from src.services.prompt_analyser.rule_enforcer import RuleEnforcer


class TestRuleEnforcer:
    """Tests for RuleEnforcer."""
    
    @pytest.fixture
    def mock_llm_client(self):
        client = MagicMock()
        client.generate_structured = AsyncMock(return_value={
            "title": "Amended Task",
            "description": "Amended description",
            "payload": {}
        })
        return client
    
    @pytest.fixture
    def rule_enforcer(self, mock_llm_client):
        return RuleEnforcer(mock_llm_client)
    
    @pytest.mark.asyncio
    async def test_valid_task_passes(self, rule_enforcer):
        task = Task(
            id="test-1",
            title="Valid Task",
            description="A valid task description",
            agent_type=AgentType.CODE_WRITER
        )
        
        is_valid, reason = await rule_enforcer.validate(task)
        
        assert is_valid is True
        assert reason is None
    
    @pytest.mark.asyncio
    async def test_empty_title_fails(self, rule_enforcer):
        task = Task(
            id="test-1",
            title="",
            description="Description",
            agent_type=AgentType.CODE_WRITER
        )
        
        is_valid, reason = await rule_enforcer.validate(task)
        
        assert is_valid is False
        assert "title" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_empty_description_fails(self, rule_enforcer):
        task = Task(
            id="test-1",
            title="Valid Title",
            description="",
            agent_type=AgentType.CODE_WRITER
        )
        
        is_valid, reason = await rule_enforcer.validate(task)
        
        assert is_valid is False
        assert "description" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_dangerous_payload_fails(self, rule_enforcer):
        task = Task(
            id="test-1",
            title="Dangerous Task",
            description="Task with dangerous payload",
            agent_type=AgentType.CICD,
            payload={"command": "rm -rf /"}
        )
        
        is_valid, reason = await rule_enforcer.validate(task)
        
        assert is_valid is False
        assert "dangerous" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_path_traversal_fails(self, rule_enforcer):
        task = Task(
            id="test-1",
            title="Path Traversal Task",
            description="Task with path traversal",
            agent_type=AgentType.CODE_WRITER,
            payload={"file_path": "../../../etc/passwd"}
        )
        
        is_valid, reason = await rule_enforcer.validate(task)
        
        assert is_valid is False
        assert "traversal" in reason.lower()
