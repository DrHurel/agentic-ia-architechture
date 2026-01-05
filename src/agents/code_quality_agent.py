"""
Code Quality Analyser Agent - Analyzes code quality.
Has Read access to analyze code.
"""

import structlog

from src.core.base import BaseAgent
from src.core.interfaces import (
    AgentType, Task, TaskResult, TaskStatus,
    IMessagePublisher, ILLMClient, IFileReader
)


logger = structlog.get_logger()


class CodeQualityAgent(BaseAgent):
    """
    Agent responsible for code quality analysis.
    Capabilities: Linting, style checking, best practices analysis.
    """
    
    SYSTEM_PROMPT = """You are an expert code quality analyst agent.
Your job is to review code and identify quality issues.

When analyzing code:
1. Check for code smells and anti-patterns
2. Verify adherence to coding standards
3. Look for potential bugs and security issues
4. Assess code readability and maintainability
5. Check documentation quality

Severity levels:
- critical: Security vulnerabilities, data loss risks
- high: Bugs, significant performance issues
- medium: Code smells, maintainability issues
- low: Style issues, minor improvements
"""
    
    def __init__(
        self,
        message_publisher: IMessagePublisher,
        llm_client: ILLMClient,
        file_reader: IFileReader
    ):
        super().__init__(message_publisher, llm_client, "CodeQualityAgent")
        self._file_reader = file_reader
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.CODE_QUALITY
    
    async def _do_execute(self, task: Task) -> TaskResult:
        """Execute a code quality analysis task."""
        payload = task.payload
        action = payload.get("action", "analyze")
        
        if action == "analyze":
            return await self._analyze_code(task)
        elif action == "lint":
            return await self._lint_code(task)
        elif action == "security_scan":
            return await self._security_scan(task)
        else:
            return await self._analyze_code(task)
    
    async def _analyze_code(self, task: Task) -> TaskResult:
        """Perform comprehensive code quality analysis."""
        payload = task.payload
        file_path = payload.get("file_path", "")
        
        if not file_path:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error="file_path is required for code analysis"
            )
        
        try:
            code = await self._file_reader.read_file(file_path)
        except FileNotFoundError:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=f"File not found: {file_path}"
            )
        
        prompt = f"""Analyze this code for quality issues:

File: {file_path}
```
{code}
```

Perform a comprehensive quality analysis covering:
1. Code structure and organization
2. Naming conventions
3. Error handling
4. Documentation
5. Potential bugs
6. Performance concerns
7. Security issues
8. Best practices adherence

Respond with JSON:
{{
    "overall_score": 1-10,
    "issues": [
        {{
            "severity": "critical/high/medium/low",
            "type": "bug/security/style/performance/maintainability",
            "line": null or line number,
            "description": "issue description",
            "suggestion": "how to fix"
        }}
    ],
    "metrics": {{
        "complexity": "low/medium/high",
        "readability": 1-10,
        "documentation": 1-10,
        "test_coverage_estimate": "low/medium/high"
    }},
    "summary": "overall assessment",
    "recommendations": ["top improvement recommendations"]
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "overall_score": {"type": "number"},
                    "issues": {"type": "array"},
                    "metrics": {"type": "object"},
                    "summary": {"type": "string"},
                    "recommendations": {"type": "array"}
                },
                "required": ["overall_score", "issues", "summary"]
            })
            
            # Determine if follow-up is needed
            critical_issues = [i for i in result.get("issues", []) if i.get("severity") == "critical"]
            suggested_followup = None
            if critical_issues:
                suggested_followup = f"Fix {len(critical_issues)} critical issues in {file_path}"
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
                    "file_path": file_path,
                    **result,
                    "suggested_followup": suggested_followup
                }
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _lint_code(self, task: Task) -> TaskResult:
        """Perform linting-style analysis."""
        payload = task.payload
        file_path = payload.get("file_path", "")
        
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
        
        prompt = f"""Lint this code and find style/formatting issues:

File: {file_path}
```
{code}
```

Focus on:
1. Indentation and spacing
2. Line length
3. Naming conventions
4. Import organization
5. Trailing whitespace
6. Missing semicolons/commas (if applicable)

Respond with JSON:
{{
    "issues": [
        {{
            "line": line number,
            "column": column number,
            "rule": "rule name",
            "message": "issue description",
            "fixable": true/false
        }}
    ],
    "fixable_count": number,
    "total_count": number
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "issues": {"type": "array"},
                    "fixable_count": {"type": "number"},
                    "total_count": {"type": "number"}
                }
            })
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={"file_path": file_path, **result}
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _security_scan(self, task: Task) -> TaskResult:
        """Perform security-focused analysis."""
        payload = task.payload
        file_path = payload.get("file_path", "")
        
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
        
        prompt = f"""Perform a security analysis on this code:

File: {file_path}
```
{code}
```

Look for:
1. SQL injection vulnerabilities
2. XSS vulnerabilities
3. Command injection
4. Path traversal
5. Hardcoded secrets
6. Insecure cryptography
7. Authentication/authorization issues
8. Input validation issues

Respond with JSON:
{{
    "vulnerabilities": [
        {{
            "severity": "critical/high/medium/low",
            "type": "vulnerability type",
            "line": line number or null,
            "description": "what's the issue",
            "impact": "potential impact",
            "remediation": "how to fix"
        }}
    ],
    "security_score": 1-10,
    "summary": "overall security assessment"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "vulnerabilities": {"type": "array"},
                    "security_score": {"type": "number"},
                    "summary": {"type": "string"}
                }
            })
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={"file_path": file_path, **result}
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
