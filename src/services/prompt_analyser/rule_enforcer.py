"""
Rule Enforcer - Validates tasks against system rules before scheduling.
Follows Single Responsibility Principle.
"""

from typing import Optional, Callable
import structlog

from src.core.interfaces import IRuleEnforcer, ILLMClient, Task, AgentType, TaskPriority


logger = structlog.get_logger()


class ValidationRule:
    """Represents a single validation rule."""
    
    def __init__(
        self,
        name: str,
        validator: Callable[[Task], tuple[bool, Optional[str]]],
        severity: str = "error"  # error, warning
    ):
        self.name = name
        self.validator = validator
        self.severity = severity
    
    def validate(self, task: Task) -> tuple[bool, Optional[str]]:
        """Run the validation rule."""
        return self.validator(task)


class RuleEnforcer(IRuleEnforcer):
    """
    Validates tasks against business and technical rules.
    Can request amendments for non-compliant tasks.
    """
    
    def __init__(self, llm_client: ILLMClient):
        self._llm_client = llm_client
        self._logger = logger.bind(component="RuleEnforcer")
        self._rules = self._build_default_rules()
    
    def _build_default_rules(self) -> list[ValidationRule]:
        """Build the default set of validation rules."""
        return [
            ValidationRule(
                "has_title",
                lambda t: (bool(t.title.strip()), "Task must have a non-empty title")
            ),
            ValidationRule(
                "has_description",
                lambda t: (bool(t.description.strip()), "Task must have a non-empty description")
            ),
            ValidationRule(
                "valid_agent_type",
                lambda t: (t.agent_type in AgentType, f"Invalid agent type: {t.agent_type}")
            ),
            ValidationRule(
                "reasonable_title_length",
                lambda t: (len(t.title) <= 200, "Task title must be 200 characters or less"),
                severity="warning"
            ),
            ValidationRule(
                "no_dangerous_payload",
                self._check_dangerous_payload
            ),
            ValidationRule(
                "valid_file_paths",
                self._check_file_paths
            ),
        ]
    
    def _check_dangerous_payload(self, task: Task) -> tuple[bool, Optional[str]]:
        """Check for potentially dangerous payload contents."""
        dangerous_patterns = [
            "rm -rf /",
            "sudo rm",
            "format c:",
            ":(){:|:&};:",  # Fork bomb
            "dd if=/dev/zero",
        ]
        
        payload_str = str(task.payload).lower()
        for pattern in dangerous_patterns:
            if pattern.lower() in payload_str:
                return False, f"Dangerous command detected: {pattern}"
        
        return True, None
    
    def _check_file_paths(self, task: Task) -> tuple[bool, Optional[str]]:
        """Check that file paths in payload are within workspace."""
        file_path = task.payload.get("file_path", "")
        if file_path:
            # Check for path traversal
            if ".." in file_path:
                return False, "Path traversal detected in file_path"
            if file_path.startswith("/") and not file_path.startswith("/workspace"):
                return False, "Absolute paths must be within /workspace"
        
        return True, None
    
    def add_rule(self, rule: ValidationRule) -> None:
        """Add a custom validation rule."""
        self._rules.append(rule)
        self._logger.info("Rule added", rule_name=rule.name)
    
    async def validate(self, task: Task) -> tuple[bool, Optional[str]]:
        """
        Validate a task against all rules.
        Returns (is_valid, rejection_reason).
        """
        self._logger.info("Validating task", task_id=task.id, task_title=task.title)
        
        errors = []
        warnings = []
        
        for rule in self._rules:
            is_valid, reason = rule.validate(task)
            if not is_valid:
                if rule.severity == "error":
                    errors.append(f"[{rule.name}] {reason}")
                else:
                    warnings.append(f"[{rule.name}] {reason}")
        
        # Log warnings but don't reject
        for warning in warnings:
            self._logger.warning("Validation warning", task_id=task.id, warning=warning)
        
        if errors:
            rejection_reason = "; ".join(errors)
            self._logger.info("Task rejected", task_id=task.id, reason=rejection_reason)
            return False, rejection_reason
        
        self._logger.info("Task validated successfully", task_id=task.id)
        return True, None
    
    async def request_amendment(self, task: Task, reason: str) -> Task:
        """
        Request task amendment using LLM.
        Returns an amended task.
        """
        self._logger.info("Requesting task amendment", task_id=task.id, reason=reason)
        
        prompt = f"""The following task was rejected for validation:

Task:
- Title: {task.title}
- Description: {task.description}
- Agent Type: {task.agent_type.value}
- Payload: {task.payload}

Rejection Reason: {reason}

Please amend the task to fix the validation issues.
Keep the original intent but make it compliant with the rules.

Return a JSON object with the amended task fields:
{{
    "title": "amended title",
    "description": "amended description",
    "payload": {{}}
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "payload": {"type": "object"}
                }
            })
            
            # Create amended task
            amended_task = task.model_copy(update={
                "title": result.get("title", task.title),
                "description": result.get("description", task.description),
                "payload": result.get("payload", task.payload),
                "metadata": {**task.metadata, "amended": True, "amendment_reason": reason}
            })
            
            self._logger.info("Task amended", task_id=task.id)
            return amended_task
            
        except Exception as e:
            self._logger.error("Failed to amend task", task_id=task.id, error=str(e))
            # Return original task if amendment fails
            return task


class RuleEnforcerFactory:
    """Factory for creating RuleEnforcer instances."""
    
    @staticmethod
    def create(llm_client: ILLMClient) -> RuleEnforcer:
        """Create a RuleEnforcer instance."""
        return RuleEnforcer(llm_client)
