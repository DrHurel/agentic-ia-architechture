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
    
    def _extract_file_path(self, task: Task) -> str:
        """Extract file path from task description or payload."""
        import re
        
        # First check payload - this is the preferred source
        if task.payload.get("file_path"):
            path = task.payload["file_path"]
            # Ensure it starts with /workspace
            if not path.startswith('/workspace'):
                path = f"/workspace/{path.lstrip('/')}"
            return path
        
        # Try to extract from description or title
        text = f"{task.title} {task.description}"
        
        # Common patterns for file paths - prefer explicit paths
        patterns = [
            r'/workspace/[\w/.-]+\.\w+',  # /workspace/file.py (any extension)
            r'[\w/]+\.\w{1,4}',  # any file with extension
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                path = match.group(0)
                # Ensure it starts with /workspace
                if not path.startswith('/workspace'):
                    path = f"/workspace/{path.lstrip('/')}"
                return path
        
        # Last resort: generate a meaningful filename from task context
        # Look for class or module names in the description
        class_match = re.search(r'class\s+(\w+)', text, re.IGNORECASE)
        if class_match:
            return f"/workspace/{class_match.group(1).lower()}.py"
        
        func_match = re.search(r'function\s+(\w+)', text, re.IGNORECASE)
        if func_match:
            return f"/workspace/{func_match.group(1).lower()}.py"
        
        # Extract meaningful name from title using keyword mapping
        clean_title = task.title.lower()
        
        # Map common task patterns to clean filenames
        filename_mappings = {
            'unit test': 'test',
            'unit tests': 'tests',
            'integration test': 'test_integration',
            'integration tests': 'tests_integration',
            'scraper': 'scraper',
            'parser': 'parser',
            'web scraper': 'web_scraper',
            'main entry': 'main',
            'entry point': 'main',
            'utility': 'utils',
            'utilities': 'utils',
            'helper': 'helpers',
            'config': 'config',
            'settings': 'settings',
        }
        
        # Check for common patterns first
        for pattern, filename in filename_mappings.items():
            if pattern in clean_title:
                # Add subject if found (e.g., "parser" from "unit tests for parser")
                for subject in ['scraper', 'parser', 'utils', 'main', 'config']:
                    if subject in clean_title and subject != filename:
                        return f"/workspace/{filename}_{subject}.py"
                return f"/workspace/{filename}.py"
        
        # Fallback: Remove action words and create clean filename
        for word in ['create', 'write', 'implement', 'build', 'add', 'make', 'the', 'a', 'an', 'for', 'logic', 'with']:
            clean_title = clean_title.replace(word, ' ')
        
        # Create snake_case filename - keep full words, limit to 40 chars
        safe_name = re.sub(r'[^\w\s]', '', clean_title).strip()
        safe_name = re.sub(r'\s+', '_', safe_name).strip('_')
        
        # Limit length but try to keep complete words
        if len(safe_name) > 40:
            parts = safe_name.split('_')
            safe_name = ''
            for part in parts:
                if len(safe_name) + len(part) + 1 <= 40:
                    safe_name = f"{safe_name}_{part}" if safe_name else part
                else:
                    break
        
        if not safe_name:
            safe_name = "output"
        
        return f"/workspace/{safe_name}.py"

    async def _write_code(self, task: Task) -> TaskResult:
        """Write new code based on the task description."""
        payload = task.payload
        file_path = self._extract_file_path(task)  # Use extraction
        language = payload.get("language", "python")
        
        self._logger.info("Writing code", file_path=file_path, task=task.title)
        
        # Get context from existing files if specified
        context = ""
        context_files = payload.get("context_files", [])
        for ctx_file in context_files:
            try:
                content = await self._file_reader.read_file(ctx_file)
                context += f"\n--- {ctx_file} ---\n{content}\n"
            except Exception as e:
                self._logger.warning("Could not read context file", file=ctx_file, error=str(e))
        
        # Get architect plan if available - code writer should follow it
        architect_plan = payload.get("architect_plan", {})
        plan_context = ""
        if architect_plan:
            plan_context = f"""
ARCHITECT'S PLAN (you MUST follow this design):
- Components: {architect_plan.get('components', [])}
- File Structure: {architect_plan.get('file_structure', {})}
- Patterns: {architect_plan.get('patterns', [])}
- Technologies: {architect_plan.get('technologies', [])}

This file should implement part of the above architecture.
"""
            # Find this file's specific purpose from the plan
            for file_info in architect_plan.get("files", []):
                if file_info.get("path") == file_path:
                    plan_context += f"\nFile Purpose: {file_info.get('purpose', 'Not specified')}\n"
                    break
        
        prompt = f"""Write {language} code for:

Task: {task.title}
Description: {task.description}
File: {file_path}

{plan_context}

{f'Context from existing files:{context}' if context else ''}

Write ONLY the code, no explanations, no markdown, just pure {language} code.
Follow the architect's plan if provided."""
        
        try:
            # Get raw code from LLM
            code = await self._llm_client.generate(prompt)
            code = self._clean_code_response(code)
            
            # Write the file if path specified
            if file_path and code:
                await self._file_writer.write_file(file_path, code)
                self._logger.info("Code written to file", path=file_path, size=len(code))
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "file_path": file_path,
                    "code_length": len(code),
                    "code_written": bool(code),
                    "followed_architect_plan": bool(architect_plan)
                }
            )
            
        except Exception as e:
            self._logger.error("Code writing failed", error=str(e))
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    def _clean_code_response(self, code: str) -> str:
        """Clean up code response from LLM."""
        code = code.strip()
        
        # Remove markdown code blocks if present
        if code.startswith("```python"):
            code = code[9:]
        elif code.startswith("```"):
            code = code[3:]
        
        # Check if there's a closing ``` and extract only the code before it
        if "```" in code:
            code = code.split("```")[0]
        
        # Remove trailing explanatory text after the last function/class
        lines = code.split('\n')
        clean_lines = []
        in_code = True
        last_code_line = 0
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Check if line looks like code (not pure prose)
            if stripped and not stripped.startswith('#'):
                # Check for code-like patterns
                if any(x in stripped for x in ['def ', 'class ', 'import ', 'from ', 'return ', '=', '(', ')', '[', ']', '{', '}']):
                    last_code_line = i
                elif stripped.startswith(('if ', 'else:', 'elif ', 'for ', 'while ', 'try:', 'except', 'finally:', 'with ')):
                    last_code_line = i
                elif stripped and not any(c.isalpha() for c in stripped):  # pure symbols/numbers
                    last_code_line = i
        
        # Include all lines up to and including the last code line
        clean_lines = lines[:last_code_line + 1] if last_code_line > 0 else lines
        
        return '\n'.join(clean_lines).strip()
    
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

Write ONLY the complete modified code, no explanations, no markdown."""
        
        try:
            code = await self._llm_client.generate(prompt)
            modified_code = self._clean_code_response(code)
            await self._file_writer.write_file(file_path, modified_code)
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "file_path": file_path,
                    "code_length": len(modified_code)
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
        # Try to extract file path from task
        file_path = self._extract_file_path(task)
        
        prompt = f"""Write Python code for this task.

Task: {task.title}
Details: {task.description}
Save to: {file_path}

Write ONLY the Python code, nothing else. No JSON, no markdown, just pure Python code."""
        
        try:
            # Get raw code from LLM without JSON wrapping
            code = await self._llm_client.generate(prompt)
            code = self._clean_code_response(code)
        except Exception as e:
            self._logger.error("Generic code task failed", error=str(e))
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
