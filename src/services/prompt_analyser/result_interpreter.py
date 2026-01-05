"""
Result Interpreter - Interprets task results and determines next actions.
Follows Single Responsibility Principle.
"""

from typing import Optional
import structlog

from src.core.interfaces import (
    IResultInterpreter, ILLMClient, ITaskFormulator,
    Task, TaskResult, TaskStatus
)


logger = structlog.get_logger()


class ResultInterpreter(IResultInterpreter):
    """
    Interprets task results and determines follow-up actions.
    Can generate new tasks based on results or mark tasks complete.
    """
    
    def __init__(
        self,
        llm_client: ILLMClient,
        task_formulator: ITaskFormulator
    ):
        self._llm_client = llm_client
        self._task_formulator = task_formulator
        self._logger = logger.bind(component="ResultInterpreter")
        
        # Callbacks for result handling
        self._completion_callbacks: list = []
        self._new_task_callbacks: list = []
    
    def on_completion(self, callback) -> None:
        """Register a callback for task completion."""
        self._completion_callbacks.append(callback)
    
    def on_new_tasks(self, callback) -> None:
        """Register a callback for new task generation."""
        self._new_task_callbacks.append(callback)
    
    async def interpret(self, result: TaskResult) -> dict:
        """
        Interpret a task result and determine next actions.
        Returns interpretation details including any follow-up tasks.
        """
        self._logger.info(
            "Interpreting result",
            task_id=result.task_id,
            status=result.status.value
        )
        
        interpretation = {
            "task_id": result.task_id,
            "status": result.status.value,
            "needs_followup": False,
            "followup_tasks": [],
            "summary": "",
            "user_feedback": None
        }
        
        if result.status == TaskStatus.COMPLETED:
            interpretation = await self._interpret_success(result, interpretation)
        elif result.status == TaskStatus.FAILED:
            interpretation = await self._interpret_failure(result, interpretation)
        elif result.status == TaskStatus.REJECTED:
            interpretation = await self._interpret_rejection(result, interpretation)
        
        self._logger.info(
            "Interpretation complete",
            task_id=result.task_id,
            needs_followup=interpretation["needs_followup"]
        )
        
        return interpretation
    
    async def _interpret_success(self, result: TaskResult, interpretation: dict) -> dict:
        """Interpret a successful task result."""
        output = result.output or {}
        
        # Check if result suggests follow-up work
        if isinstance(output, dict):
            suggested_followup = output.get("suggested_followup")
            if suggested_followup:
                interpretation["needs_followup"] = True
                # Generate follow-up tasks
                followup_tasks = await self._task_formulator.formulate(suggested_followup)
                interpretation["followup_tasks"] = [t.model_dump() for t in followup_tasks]
                
                # Notify callbacks
                for callback in self._new_task_callbacks:
                    await callback(followup_tasks)
        
        # Generate summary for user
        interpretation["summary"] = await self._generate_summary(result)
        interpretation["user_feedback"] = {
            "type": "success",
            "message": f"Task completed: {interpretation['summary']}"
        }
        
        return interpretation
    
    async def _interpret_failure(self, result: TaskResult, interpretation: dict) -> dict:
        """Interpret a failed task result."""
        error = result.error or "Unknown error"
        
        # Analyze failure and determine if retry or alternative approach needed
        prompt = f"""A task failed with the following error:

Error: {error}

Output: {result.output}

Metadata: {result.metadata}

Analyze this failure and determine:
1. Is this a recoverable error?
2. Should we retry the task?
3. Should we try an alternative approach?
4. What feedback should we give the user?

Respond with JSON:
{{
    "recoverable": true/false,
    "should_retry": true/false,
    "alternative_approach": "description or null",
    "user_message": "message for user"
}}"""
        
        try:
            analysis = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "recoverable": {"type": "boolean"},
                    "should_retry": {"type": "boolean"},
                    "alternative_approach": {"type": ["string", "null"]},
                    "user_message": {"type": "string"}
                }
            })
            
            interpretation["needs_followup"] = analysis.get("should_retry", False)
            interpretation["summary"] = analysis.get("user_message", f"Task failed: {error}")
            
            if analysis.get("alternative_approach"):
                # Generate alternative tasks
                alt_tasks = await self._task_formulator.formulate(analysis["alternative_approach"])
                interpretation["followup_tasks"] = [t.model_dump() for t in alt_tasks]
                
                for callback in self._new_task_callbacks:
                    await callback(alt_tasks)
            
            interpretation["user_feedback"] = {
                "type": "error",
                "message": interpretation["summary"],
                "recoverable": analysis.get("recoverable", False)
            }
            
        except Exception as e:
            self._logger.error("Failed to analyze task failure", error=str(e))
            interpretation["summary"] = f"Task failed: {error}"
            interpretation["user_feedback"] = {
                "type": "error",
                "message": interpretation["summary"]
            }
        
        return interpretation
    
    async def _interpret_rejection(self, result: TaskResult, interpretation: dict) -> dict:
        """Interpret a rejected task result."""
        interpretation["summary"] = f"Task was rejected: {result.error}"
        interpretation["user_feedback"] = {
            "type": "warning",
            "message": interpretation["summary"]
        }
        return interpretation
    
    async def _generate_summary(self, result: TaskResult) -> str:
        """Generate a human-readable summary of the result."""
        output = result.output
        if not output:
            return "Task completed successfully."
        
        # If output is short, use it directly
        if isinstance(output, str) and len(output) < 200:
            return output
        
        # Use LLM to summarize longer outputs
        prompt = f"""Summarize this task result in one or two sentences:

{str(output)[:2000]}

Keep the summary concise and informative."""
        
        try:
            summary = await self._llm_client.generate(prompt)
            return summary.strip()
        except Exception:
            return "Task completed successfully."
    
    async def complete_task(self, result: TaskResult) -> None:
        """Mark a task as complete and notify callbacks."""
        self._logger.info("Completing task", task_id=result.task_id)
        
        for callback in self._completion_callbacks:
            try:
                await callback(result)
            except Exception as e:
                self._logger.error(
                    "Completion callback failed",
                    task_id=result.task_id,
                    error=str(e)
                )


class ResultInterpreterFactory:
    """Factory for creating ResultInterpreter instances."""
    
    @staticmethod
    def create(llm_client: ILLMClient, task_formulator: ITaskFormulator) -> ResultInterpreter:
        """Create a ResultInterpreter instance."""
        return ResultInterpreter(llm_client, task_formulator)
