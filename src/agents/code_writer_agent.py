"""
Code Writer Agent - Writes and modifies code.
Has Read and Write access to the filesystem.
"""

import asyncio
from typing import Optional
import structlog

from src.core.base import BaseAgent
from src.core.interfaces import (
    AgentType, Task, TaskResult, TaskStatus,
    IMessagePublisher, ILLMClient, IFileReader, IFileWriter
)


logger = structlog.get_logger()


class CodeWriterAgent(BaseAgent):
    """
    Agent responsible for writing and modifying code.
    Capabilities: Create files, modify files, implement features.
    """
    
    SYSTEM_PROMPT = """You are an expert software developer agent.
Your job is to write clean, efficient, and well-documented code.

When writing code:
1. Follow best practices and design patterns
2. Include appropriate comments and docstrings
3. Handle errors gracefully
4. Write modular, reusable code
5. Follow the project's existing style and conventions

When responding to code tasks, provide:
1. The complete code to be written
2. Explanation of key design decisions
3. Any dependencies or imports needed
4. Suggestions for testing the code
"""
    
    def __init__(
        self,
        message_publisher: IMessagePublisher,
        llm_client: ILLMClient,
        file_reader: IFileReader,
        file_writer: IFileWriter
    ):
        super().__init__(message_publisher, llm_client, "CodeWriterAgent")
        self._file_reader = file_reader
        self._file_writer = file_writer
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.CODE_WRITER
    
    async def _do_execute(self, task: Task) -> TaskResult:
        """Execute a code writing task."""
        payload = task.payload
        action = payload.get("action", "write")
        
        if action == "write":
            return await self._write_code(task)
        elif action == "modify":
            return await self._modify_code(task)
        elif action == "create_file":
            return await self._create_file(task)
        else:
            return await self._generic_code_task(task)
    
    async def _write_code(self, task: Task) -> TaskResult:
        """Write new code based on the task description."""
        payload = task.payload
        file_path = payload.get("file_path", "")
        language = payload.get("language", "python")
        
        # Get context from existing files if specified
        context = ""
        context_files = payload.get("context_files", [])
        for ctx_file in context_files:
            try:
                content = await self._file_reader.read_file(ctx_file)
                context += f"\n--- {ctx_file} ---\n{content}\n"
            except Exception as e:
                self._logger.warning("Could not read context file", file=ctx_file, error=str(e))
        
        prompt = f"""Write code for the following task:

Task: {task.title}
Description: {task.description}
Language: {language}
Target File: {file_path}

{f'Context from existing files:{context}' if context else ''}

Provide the complete code implementation.
Respond with JSON:
{{
    "code": "the complete code",
    "imports": ["list of required imports/dependencies"],
    "explanation": "brief explanation of the implementation"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "imports": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"}
                },
                "required": ["code"]
            })
            
            code = result.get("code", "")
            
            # Write the file if path specified
            if file_path:
                await self._file_writer.write_file(file_path, code)
                self._logger.info("Code written to file", path=file_path)
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "file_path": file_path,
                    "code_length": len(code),
                    "imports": result.get("imports", []),
                    "explanation": result.get("explanation", ""),
                    "suggested_followup": f"Run tests for {file_path}" if file_path else None
                }
            )
            
        except Exception as e:
            self._logger.error("Code writing failed", error=str(e))
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _modify_code(self, task: Task) -> TaskResult:
        """Modify existing code."""
        payload = task.payload
        file_path = payload.get("file_path", "")
        
        if not file_path:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error="file_path is required for modify action"
            )
        
        try:
            existing_code = await self._file_reader.read_file(file_path)
        except FileNotFoundError:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=f"File not found: {file_path}"
            )
        
        prompt = f"""Modify the following code based on the task:

Task: {task.title}
Description: {task.description}

Current code in {file_path}:
```
{existing_code}
```

Provide the modified code.
Respond with JSON:
{{
    "code": "the complete modified code",
    "changes": ["list of changes made"],
    "explanation": "brief explanation of modifications"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "changes": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"}
                },
                "required": ["code"]
            })
            
            modified_code = result.get("code", "")
            await self._file_writer.write_file(file_path, modified_code)
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "file_path": file_path,
                    "changes": result.get("changes", []),
                    "explanation": result.get("explanation", "")
                }
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _create_file(self, task: Task) -> TaskResult:
        """Create a new file with specified content."""
        payload = task.payload
        file_path = payload.get("file_path", "")
        content = payload.get("content", "")
        
        if not file_path:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error="file_path is required"
            )
        
        try:
            await self._file_writer.write_file(file_path, content)
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "file_path": file_path,
                    "created": True
                }
            )
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _generic_code_task(self, task: Task) -> TaskResult:
        """Handle generic code-related tasks."""
        prompt = f"""You are a code writing agent. Execute this task:

Task: {task.title}
Description: {task.description}
Payload: {task.payload}

Determine what code needs to be written and provide it.
Respond with JSON:
{{
    "code": "the code to write",
    "file_path": "suggested file path or null",
    "explanation": "what this code does"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "file_path": {"type": ["string", "null"]},
                    "explanation": {"type": "string"}
                }
            })
            
            file_path = result.get("file_path")
            if file_path:
                await self._file_writer.write_file(file_path, result.get("code", ""))
            
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
