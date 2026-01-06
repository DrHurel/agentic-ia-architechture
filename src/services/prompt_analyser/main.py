"""
Prompt Analyser Service - Main entry point.
Orchestrates the prompt analysis pipeline.
"""

import asyncio
import structlog

from src.core.base import BaseService
from src.core.config import AppSettings
from src.core.interfaces import Task, TaskResult, TaskStatus
from src.infrastructure.message_bus import MessageBus, Topics
from src.infrastructure.llm_client import LlamaClient
from src.infrastructure.os_access import FileReader

from .task_formulator import TaskFormulator
from .rule_enforcer import RuleEnforcer
from .task_scheduler import TaskScheduler
from .result_interpreter import ResultInterpreter


logger = structlog.get_logger()


class PromptAnalyserService(BaseService):
    """
    Main service coordinating the prompt analysis pipeline.
    Handles: Input -> Formulation -> Validation -> Scheduling -> Result Interpretation
    """
    
    def __init__(self, settings: AppSettings):
        super().__init__("PromptAnalyserService")
        self._settings = settings
        
        # Will be initialized in _on_start
        self._message_bus: MessageBus = None
        self._llm_client: LlamaClient = None
        self._file_reader: FileReader = None
        self._task_formulator: TaskFormulator = None
        self._rule_enforcer: RuleEnforcer = None
        self._task_scheduler: TaskScheduler = None
        self._result_interpreter: ResultInterpreter = None
    
    async def _on_start(self) -> None:
        """Initialize all components."""
        # Initialize LLM client
        self._llm_client = LlamaClient(self._settings.llama)
        await self._llm_client.connect()
        
        # Initialize file reader for project orchestrator
        self._file_reader = FileReader(self._settings.service.workspace_path)
        
        # Initialize message bus
        self._message_bus = MessageBus(self._settings.kafka, "prompt-analyser")
        await self._message_bus.start()
        
        # Initialize pipeline components
        self._task_formulator = TaskFormulator(self._llm_client)
        self._rule_enforcer = RuleEnforcer(self._llm_client)
        self._task_scheduler = TaskScheduler(self._message_bus)
        self._result_interpreter = ResultInterpreter(self._llm_client, self._task_formulator)
        
        # Set up result interpreter callbacks
        self._result_interpreter.on_completion(self._on_task_completed)
        self._result_interpreter.on_new_tasks(self._on_new_tasks)
        
        # Subscribe to result topic
        await self._message_bus.subscribe(Topics.TASK_RESULT, self._handle_result)
        
        self._logger.info("PromptAnalyserService initialized")
    
    async def _on_stop(self) -> None:
        """Clean up resources."""
        self._message_bus.stop_consuming()
        await self._message_bus.stop()
        await self._llm_client.disconnect()
    
    async def process_input(self, natural_language_input: str) -> list[Task]:
        """
        Process user input through the full pipeline.
        Returns list of scheduled tasks.
        """
        self._logger.info("Processing user input", input_length=len(natural_language_input))
        
        # Step 1: Formulate tasks
        tasks = await self._task_formulator.formulate(natural_language_input)
        self._logger.info("Tasks formulated", count=len(tasks))
        
        scheduled_tasks = []
        
        for task in tasks:
            # Step 2: Validate task
            is_valid, rejection_reason = await self._rule_enforcer.validate(task)
            
            if not is_valid:
                # Request amendment
                amended_task = await self._rule_enforcer.request_amendment(task, rejection_reason)
                
                # Re-validate
                is_valid, rejection_reason = await self._rule_enforcer.validate(amended_task)
                
                if not is_valid:
                    self._logger.warning(
                        "Task rejected after amendment",
                        task_id=task.id,
                        reason=rejection_reason
                    )
                    continue
                
                task = amended_task
            
            # Step 3: Schedule task
            await self._task_scheduler.schedule(task)
            scheduled_tasks.append(task)
        
        self._logger.info("Tasks scheduled", count=len(scheduled_tasks))
        return scheduled_tasks
    
    async def _handle_result(self, message: dict) -> None:
        """Handle incoming task results."""
        try:
            result = TaskResult(**message)
            
            # Update task status
            await self._task_scheduler.update_task_status(result.task_id, result.status)
            
            # Interpret result
            interpretation = await self._result_interpreter.interpret(result)
            
            # Publish user feedback
            if interpretation.get("user_feedback"):
                await self._message_bus.publish_feedback(interpretation["user_feedback"])
            
            # Complete the task
            await self._result_interpreter.complete_task(result)
            
        except Exception as e:
            self._logger.error("Failed to handle result", error=str(e), message=message)
    
    async def _on_task_completed(self, result: TaskResult) -> None:
        """Callback when a task is completed."""
        self._logger.info("Task completed", task_id=result.task_id)
    
    async def _on_new_tasks(self, tasks: list[Task]) -> None:
        """Callback when new follow-up tasks are generated."""
        for task in tasks:
            is_valid, reason = await self._rule_enforcer.validate(task)
            if is_valid:
                await self._task_scheduler.schedule(task)
            else:
                self._logger.warning("Follow-up task rejected", task_id=task.id, reason=reason)
    
    async def get_queue_stats(self) -> dict:
        """Get current queue statistics."""
        return await self._task_scheduler.get_queue_stats()


async def main():
    """Main entry point for the prompt analyser service."""
    from src.core.config import get_settings
    
    settings = get_settings()
    service = PromptAnalyserService(settings)
    
    try:
        await service.start()
        
        # Start consuming messages
        await service._message_bus.start_consuming()
        
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(main())
