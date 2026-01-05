"""
Tester WhiteBox Agent - Creates and runs unit/integration tests.
Has Read and Command access.
"""

import structlog

from src.core.base import BaseAgent
from src.core.interfaces import (
    AgentType, Task, TaskResult, TaskStatus,
    IMessagePublisher, ILLMClient, IFileReader, ICommandExecutor
)


logger = structlog.get_logger()


class TesterWhiteBoxAgent(BaseAgent):
    """
    Agent responsible for white-box testing (unit tests, integration tests).
    Capabilities: Create tests, analyze code paths, verify internal behavior.
    """
    
    SYSTEM_PROMPT = """You are an expert white-box testing agent.
Your job is to create comprehensive tests that verify internal code behavior.

When creating tests:
1. Aim for high code coverage
2. Test edge cases and boundary conditions
3. Test error handling paths
4. Use appropriate mocking/stubbing
5. Follow testing best practices (AAA pattern)
6. Write clear, descriptive test names

Test types:
- Unit tests: Test individual functions/methods in isolation
- Integration tests: Test component interactions
"""
    
    def __init__(
        self,
        message_publisher: IMessagePublisher,
        llm_client: ILLMClient,
        file_reader: IFileReader,
        command_executor: ICommandExecutor
    ):
        super().__init__(message_publisher, llm_client, "TesterWhiteBoxAgent")
        self._file_reader = file_reader
        self._command_executor = command_executor
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.TESTER_WHITEBOX
    
    async def _do_execute(self, task: Task) -> TaskResult:
        """Execute a white-box testing task."""
        payload = task.payload
        action = payload.get("action", "create_tests")
        
        if action == "create_tests":
            return await self._create_tests(task)
        elif action == "run_tests":
            return await self._run_tests(task)
        elif action == "coverage":
            return await self._analyze_coverage(task)
        else:
            return await self._create_tests(task)
    
    async def _create_tests(self, task: Task) -> TaskResult:
        """Create unit/integration tests for code."""
        payload = task.payload
        file_path = payload.get("file_path", "")
        test_framework = payload.get("framework", "pytest")
        
        if not file_path:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error="file_path is required"
            )
        
        try:
            code = await self._file_reader.read_file(file_path)
        except FileNotFoundError:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=f"File not found: {file_path}"
            )
        
        prompt = f"""Create comprehensive {test_framework} tests for this code:

File: {file_path}
```
{code}
```

Requirements:
1. Test all public functions/methods
2. Include edge cases and error conditions
3. Use mocking where appropriate
4. Follow {test_framework} conventions
5. Aim for high coverage

Respond with JSON:
{{
    "test_code": "complete test file code",
    "test_file_path": "suggested test file path",
    "test_cases": [
        {{
            "name": "test function name",
            "description": "what it tests",
            "type": "unit/integration"
        }}
    ],
    "mocking_needed": ["list of things to mock"],
    "coverage_estimate": "estimated coverage percentage"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "test_code": {"type": "string"},
                    "test_file_path": {"type": "string"},
                    "test_cases": {"type": "array"},
                    "mocking_needed": {"type": "array"},
                    "coverage_estimate": {"type": "string"}
                },
                "required": ["test_code", "test_file_path", "test_cases"]
            })
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "source_file": file_path,
                    **result,
                    "suggested_followup": f"Run tests for {file_path}"
                }
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _run_tests(self, task: Task) -> TaskResult:
        """Run existing tests."""
        payload = task.payload
        test_path = payload.get("test_path", "tests/")
        test_command = payload.get("command", f"pytest {test_path} -v")
        
        try:
            stdout, stderr, return_code = await self._command_executor.execute_command(test_command)
            
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
    
    async def _analyze_coverage(self, task: Task) -> TaskResult:
        """Analyze test coverage."""
        payload = task.payload
        test_path = payload.get("test_path", "tests/")
        source_path = payload.get("source_path", "src/")
        
        coverage_command = f"pytest {test_path} --cov={source_path} --cov-report=json"
        
        try:
            stdout, stderr, return_code = await self._command_executor.execute_command(coverage_command)
            
            # Parse coverage results
            # Note: In real implementation, would parse coverage.json
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "command": coverage_command,
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr
                }
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
