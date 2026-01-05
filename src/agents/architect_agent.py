"""
Architect Agent - Designs system architecture and technical specifications.
Has Read access to analyze existing code structure.
"""

from typing import Optional
import structlog

from src.core.base import BaseAgent
from src.core.interfaces import (
    AgentType, Task, TaskResult, TaskStatus,
    IMessagePublisher, ILLMClient, IFileReader
)


logger = structlog.get_logger()


class ArchitectAgent(BaseAgent):
    """
    Agent responsible for system architecture and design.
    Capabilities: Analyze structure, create specs, design patterns.
    """
    
    SYSTEM_PROMPT = """You are an expert software architect agent.
Your job is to design robust, scalable, and maintainable systems.

When designing architecture:
1. Follow SOLID principles
2. Consider scalability and performance
3. Use appropriate design patterns
4. Document architectural decisions
5. Consider security implications
6. Plan for testability

When responding to architecture tasks, provide:
1. High-level design overview
2. Component breakdown
3. Data flow descriptions
4. API contracts if applicable
5. Technology recommendations
"""
    
    def __init__(
        self,
        message_publisher: IMessagePublisher,
        llm_client: ILLMClient,
        file_reader: IFileReader
    ):
        super().__init__(message_publisher, llm_client, "ArchitectAgent")
        self._file_reader = file_reader
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.ARCHITECT
    
    async def _do_execute(self, task: Task) -> TaskResult:
        """Execute an architecture task."""
        payload = task.payload
        action = payload.get("action", "design")
        
        if action == "design":
            return await self._design_architecture(task)
        elif action == "analyze":
            return await self._analyze_codebase(task)
        elif action == "review":
            return await self._review_design(task)
        else:
            return await self._generic_architecture_task(task)
    
    async def _design_architecture(self, task: Task) -> TaskResult:
        """Design system architecture."""
        payload = task.payload
        
        # Gather context
        context = await self._gather_context(payload.get("context_paths", []))
        
        prompt = f"""Design the architecture for:

Task: {task.title}
Description: {task.description}
Requirements: {payload.get('requirements', [])}

{f'Existing codebase context:{context}' if context else ''}

Provide a comprehensive architecture design.
Respond with JSON:
{{
    "overview": "high-level architecture description",
    "components": [
        {{
            "name": "component name",
            "responsibility": "what it does",
            "interfaces": ["exposed interfaces"],
            "dependencies": ["what it depends on"]
        }}
    ],
    "data_flow": "description of data flow",
    "patterns": ["design patterns used"],
    "technologies": ["recommended technologies"],
    "file_structure": {{
        "description": "recommended file/folder structure"
    }},
    "implementation_tasks": [
        {{
            "title": "task title",
            "description": "what to implement",
            "agent_type": "code_writer/tester_whitebox/etc"
        }}
    ]
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "overview": {"type": "string"},
                    "components": {"type": "array"},
                    "data_flow": {"type": "string"},
                    "patterns": {"type": "array"},
                    "technologies": {"type": "array"},
                    "file_structure": {"type": "object"},
                    "implementation_tasks": {"type": "array"}
                },
                "required": ["overview", "components"]
            })
            
            # Generate follow-up task suggestions
            impl_tasks = result.get("implementation_tasks", [])
            suggested_followup = None
            if impl_tasks:
                suggested_followup = "Implementation tasks: " + ", ".join(
                    t.get("title", "") for t in impl_tasks[:3]
                )
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output={
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
    
    async def _analyze_codebase(self, task: Task) -> TaskResult:
        """Analyze existing codebase structure."""
        payload = task.payload
        paths = payload.get("paths", ["."])
        
        analysis = {
            "files_analyzed": [],
            "structure": {},
            "patterns_detected": [],
            "issues": [],
            "recommendations": []
        }
        
        for path in paths:
            try:
                entries = await self._file_reader.list_directory(path)
                analysis["structure"][path] = entries
                
                # Analyze individual files
                for entry in entries:
                    if not entry.endswith("/"):
                        full_path = f"{path}/{entry}" if path != "." else entry
                        try:
                            content = await self._file_reader.read_file(full_path)
                            analysis["files_analyzed"].append({
                                "path": full_path,
                                "lines": len(content.split("\n")),
                                "size": len(content)
                            })
                        except Exception:
                            pass
            except Exception as e:
                self._logger.warning("Could not analyze path", path=path, error=str(e))
        
        # Use LLM to interpret the analysis
        prompt = f"""Analyze this codebase structure:

Structure: {analysis['structure']}
Files: {analysis['files_analyzed']}

Provide insights about:
1. Code organization
2. Architectural patterns
3. Potential issues
4. Improvement recommendations

Respond with JSON:
{{
    "patterns": ["detected patterns"],
    "issues": ["potential issues"],
    "recommendations": ["improvement suggestions"],
    "summary": "overall assessment"
}}"""
        
        try:
            llm_analysis = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "patterns": {"type": "array"},
                    "issues": {"type": "array"},
                    "recommendations": {"type": "array"},
                    "summary": {"type": "string"}
                }
            })
            
            analysis.update(llm_analysis)
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                output=analysis
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _review_design(self, task: Task) -> TaskResult:
        """Review an existing design or architecture."""
        payload = task.payload
        design = payload.get("design", {})
        
        prompt = f"""Review this software design:

Design:
{design}

Task context: {task.description}

Evaluate the design against:
1. SOLID principles
2. Scalability
3. Maintainability
4. Security
5. Performance

Respond with JSON:
{{
    "score": 1-10,
    "strengths": ["list of strengths"],
    "weaknesses": ["list of weaknesses"],
    "suggestions": ["improvement suggestions"],
    "verdict": "overall assessment"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "score": {"type": "number"},
                    "strengths": {"type": "array"},
                    "weaknesses": {"type": "array"},
                    "suggestions": {"type": "array"},
                    "verdict": {"type": "string"}
                }
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
    
    async def _gather_context(self, paths: list[str]) -> str:
        """Gather context from specified paths."""
        context = ""
        for path in paths:
            try:
                content = await self._file_reader.read_file(path)
                context += f"\n--- {path} ---\n{content}\n"
            except Exception:
                pass
        return context
    
    async def _generic_architecture_task(self, task: Task) -> TaskResult:
        """Handle generic architecture tasks."""
        prompt = f"""You are a software architect. Execute this task:

Task: {task.title}
Description: {task.description}

Provide architectural guidance.
Respond with JSON:
{{
    "analysis": "your analysis",
    "recommendations": ["list of recommendations"],
    "next_steps": ["suggested next steps"]
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "analysis": {"type": "string"},
                    "recommendations": {"type": "array"},
                    "next_steps": {"type": "array"}
                }
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
