"""
Tester BlackBox Agent - Creates and runs end-to-end/API tests.
Has Command access for running tests.
"""

import structlog

from src.core.base import BaseAgent
from src.core.interfaces import (
    AgentType, Task, TaskResult, TaskStatus,
    IMessagePublisher, ILLMClient, ICommandExecutor
)


logger = structlog.get_logger()


class TesterBlackBoxAgent(BaseAgent):
    """
    Agent responsible for black-box testing (E2E, API tests).
    Capabilities: Create E2E tests, test APIs, validate user flows.
    """
    
    SYSTEM_PROMPT = """You are an expert black-box testing agent.
Your job is to create tests that verify system behavior from an external perspective.

When creating tests:
1. Focus on user scenarios and business requirements
2. Test complete user flows end-to-end
3. Validate API contracts
4. Test with realistic data
5. Include both positive and negative test cases

Test types:
- End-to-end tests: Test complete user journeys
- API tests: Test HTTP endpoints and responses
- Smoke tests: Quick sanity checks
- Regression tests: Verify existing functionality
"""
    
    def __init__(
        self,
        message_publisher: IMessagePublisher,
        llm_client: ILLMClient,
        command_executor: ICommandExecutor
    ):
        super().__init__(message_publisher, llm_client, "TesterBlackBoxAgent")
        self._command_executor = command_executor
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.TESTER_BLACKBOX
    
    async def _do_execute(self, task: Task) -> TaskResult:
        """Execute a black-box testing task."""
        # Set context for command approval
        self._command_executor.set_context(
            agent_type=str(self.agent_type),
            task_id=task.id,
            project_id=task.payload.get("project_id")
        )
        
        payload = task.payload
        action = payload.get("action", "create_tests")
        
        if action == "create_tests":
            return await self._create_e2e_tests(task)
        elif action == "create_api_tests":
            return await self._create_api_tests(task)
        elif action == "run_tests":
            return await self._run_tests(task)
        else:
            return await self._create_e2e_tests(task)
    
    async def _create_e2e_tests(self, task: Task) -> TaskResult:
        """Create end-to-end tests."""
        payload = task.payload
        scenario = payload.get("scenario", task.description)
        framework = payload.get("framework", "pytest")
        
        prompt = f"""Create end-to-end tests for this scenario:

Scenario: {scenario}
Framework: {framework}

Requirements:
1. Test the complete user flow
2. Include setup and teardown
3. Use realistic test data
4. Add assertions for expected outcomes
5. Handle timeouts and async operations

Respond with JSON:
{{
    "test_code": "complete test code",
    "test_file_path": "suggested file path",
    "test_scenarios": [
        {{
            "name": "scenario name",
            "steps": ["list of steps"],
            "expected_outcome": "what should happen"
        }}
    ],
    "setup_requirements": ["any setup needed"],
    "environment_variables": ["env vars needed"]
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "test_code": {"type": "string"},
                    "test_file_path": {"type": "string"},
                    "test_scenarios": {"type": "array"},
                    "setup_requirements": {"type": "array"},
                    "environment_variables": {"type": "array"}
                },
                "required": ["test_code", "test_scenarios"]
            })
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output=result
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _create_api_tests(self, task: Task) -> TaskResult:
        """Create API tests."""
        payload = task.payload
        api_spec = payload.get("api_spec", {})
        base_url = payload.get("base_url", "http://localhost:8000")
        
        prompt = f"""Create API tests for:

API Specification: {api_spec}
Base URL: {base_url}
Description: {task.description}

Requirements:
1. Test all specified endpoints
2. Verify response status codes
3. Validate response schemas
4. Test error cases
5. Include authentication if needed

Respond with JSON:
{{
    "test_code": "complete test code",
    "test_file_path": "suggested file path",
    "endpoints_tested": [
        {{
            "method": "GET/POST/etc",
            "path": "/api/path",
            "test_cases": ["list of test cases"]
        }}
    ],
    "authentication": "type of auth needed or null"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "test_code": {"type": "string"},
                    "test_file_path": {"type": "string"},
                    "endpoints_tested": {"type": "array"},
                    "authentication": {"type": ["string", "null"]}
                },
                "required": ["test_code", "endpoints_tested"]
            })
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output=result
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _run_tests(self, task: Task) -> TaskResult:
        """Run black-box tests."""
        payload = task.payload
        test_command = payload.get("command", "pytest tests/e2e/ -v")
        
        try:
            stdout, stderr, return_code = await self._command_executor.execute_command(
                test_command,
                reason=f"Running black-box tests: {task.title}"
            )
            
            success = return_code == 0
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
                output={
                    "command": test_command,
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "success": success
                },
                error=None if success else f"Tests failed with return code {return_code}"
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
