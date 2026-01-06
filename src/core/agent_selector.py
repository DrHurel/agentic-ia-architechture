"""
Agent Selector - Selects the most efficient agent for each task.
Uses capability scoring to match tasks with optimal agents.
"""

from typing import Optional
from dataclasses import dataclass
import structlog

from src.core.interfaces import Task, AgentType


logger = structlog.get_logger()


@dataclass
class AgentCapability:
    """Describes an agent's capability for a specific task type."""
    agent_type: AgentType
    task_patterns: list[str]  # Keywords that match this agent
    efficiency_score: float  # 0.0 to 1.0, higher is better
    supports_commands: bool = False
    supports_file_io: bool = False
    supports_llm: bool = True


# Define agent capabilities and patterns
AGENT_CAPABILITIES = {
    AgentType.ARCHITECT: AgentCapability(
        agent_type=AgentType.ARCHITECT,
        task_patterns=[
            "design", "architecture", "structure", "plan", "blueprint",
            "specification", "spec", "diagram", "flow", "component",
            "system", "overview", "layout", "organize", "scaffold",
            "create project", "setup project", "init", "initialize"
        ],
        efficiency_score=0.9,
        supports_commands=True,  # Now supports commands for scaffolding
        supports_file_io=True,
        supports_llm=True
    ),
    AgentType.CODE_WRITER: AgentCapability(
        agent_type=AgentType.CODE_WRITER,
        task_patterns=[
            "implement", "write", "create", "code", "function", "class",
            "method", "script", "module", "feature", "build", "develop",
            "program", "add", "make", "generate code", "fix", "update"
        ],
        efficiency_score=0.85,
        supports_commands=False,
        supports_file_io=True,
        supports_llm=True
    ),
    AgentType.CODE_QUALITY: AgentCapability(
        agent_type=AgentType.CODE_QUALITY,
        task_patterns=[
            "lint", "quality", "review", "analyze", "check", "validate",
            "standard", "convention", "style", "format", "refactor",
            "clean", "improve", "optimize code", "smell", "issue"
        ],
        efficiency_score=0.8,
        supports_commands=False,
        supports_file_io=True,
        supports_llm=True
    ),
    AgentType.TESTER_WHITEBOX: AgentCapability(
        agent_type=AgentType.TESTER_WHITEBOX,
        task_patterns=[
            "unit test", "test function", "test class", "coverage",
            "mock", "stub", "integration test", "pytest", "unittest",
            "test code", "verify logic", "test internal"
        ],
        efficiency_score=0.75,
        supports_commands=True,
        supports_file_io=True,
        supports_llm=True
    ),
    AgentType.TESTER_BLACKBOX: AgentCapability(
        agent_type=AgentType.TESTER_BLACKBOX,
        task_patterns=[
            "e2e test", "end-to-end", "api test", "functional test",
            "acceptance test", "smoke test", "regression", "user flow",
            "scenario test", "behavior test", "test api"
        ],
        efficiency_score=0.75,
        supports_commands=True,
        supports_file_io=True,
        supports_llm=True
    ),
    AgentType.CICD: AgentCapability(
        agent_type=AgentType.CICD,
        task_patterns=[
            "deploy", "ci", "cd", "pipeline", "build", "docker",
            "container", "kubernetes", "github actions", "gitlab",
            "jenkins", "automation", "release", "publish"
        ],
        efficiency_score=0.7,
        supports_commands=True,
        supports_file_io=True,
        supports_llm=True
    )
}


class AgentSelector:
    """
    Selects the most efficient agent for a task.
    
    Uses pattern matching and scoring to determine the best agent.
    Also considers:
    - Task context (if architect designed it, prefer code_writer for implementation)
    - Agent availability (future: load balancing)
    - Required capabilities (commands, file I/O)
    """
    
    def __init__(self):
        self._logger = logger.bind(component="AgentSelector")
        self._agent_performance: dict[AgentType, float] = {}  # Running average success rate
    
    def select_agent(
        self,
        task: Task,
        architect_plan: Optional[dict] = None,
        require_commands: bool = False
    ) -> AgentType:
        """
        Select the most efficient agent for a task.
        
        Args:
            task: The task to assign
            architect_plan: Optional architect output to guide selection
            require_commands: Whether the task requires command execution
            
        Returns:
            Best agent type for the task
        """
        # If task already has explicit agent type that's not a fallback, use it
        if task.agent_type and task.payload.get("explicit_agent"):
            return task.agent_type
        
        # Score each agent
        scores = {}
        for agent_type, capability in AGENT_CAPABILITIES.items():
            # Skip if doesn't support required capabilities
            if require_commands and not capability.supports_commands:
                continue
            
            score = self._calculate_score(task, capability, architect_plan)
            scores[agent_type] = score
        
        if not scores:
            # Fallback to original agent type or code_writer
            return task.agent_type or AgentType.CODE_WRITER
        
        # Select highest scoring agent
        best_agent = max(scores.items(), key=lambda x: x[1])
        
        self._logger.debug(
            "Agent selected",
            task_id=task.id,
            task_title=task.title,
            selected=best_agent[0].value,
            score=best_agent[1],
            all_scores={k.value: v for k, v in scores.items()}
        )
        
        return best_agent[0]
    
    def _calculate_score(
        self,
        task: Task,
        capability: AgentCapability,
        architect_plan: Optional[dict]
    ) -> float:
        """Calculate efficiency score for an agent-task pair."""
        score = capability.efficiency_score
        
        # Pattern matching on task title and description
        text = f"{task.title} {task.description}".lower()
        pattern_matches = sum(1 for p in capability.task_patterns if p in text)
        score += pattern_matches * 0.1
        
        # Bonus for architect plan alignment
        if architect_plan and capability.agent_type == AgentType.CODE_WRITER:
            impl_tasks = architect_plan.get("implementation_tasks", [])
            for impl in impl_tasks:
                if impl.get("agent_type") == "code_writer" and impl.get("title", "").lower() in text:
                    score += 0.2
                    break
        
        # If this is an architecture task, strongly prefer architect
        if any(kw in text for kw in ["design", "architecture", "structure", "plan"]):
            if capability.agent_type == AgentType.ARCHITECT:
                score += 0.5
        
        # If this looks like scaffolding/setup, architect is good
        if any(kw in text for kw in ["scaffold", "setup", "initialize", "create project"]):
            if capability.agent_type == AgentType.ARCHITECT:
                score += 0.3
        
        # Adjust based on historical performance
        perf = self._agent_performance.get(capability.agent_type, 0.8)
        score *= perf
        
        return min(score, 2.0)  # Cap at 2.0
    
    def update_performance(self, agent_type: AgentType, success: bool) -> None:
        """Update agent performance tracking."""
        current = self._agent_performance.get(agent_type, 0.8)
        # Exponential moving average
        alpha = 0.1
        new_value = 1.0 if success else 0.0
        self._agent_performance[agent_type] = current * (1 - alpha) + new_value * alpha
    
    def should_architect_first(self, prompt: str) -> bool:
        """
        Determine if a prompt should go to architect first.
        
        Complex projects benefit from architecture design before implementation.
        """
        prompt_lower = prompt.lower()
        
        # Strong indicators for architect first
        architect_keywords = [
            "create a project", "build a system", "design", "architect",
            "application", "service", "api", "full stack", "microservice",
            "multi-file", "complex", "with tests", "production"
        ]
        
        # Simple tasks that don't need architect
        simple_keywords = [
            "hello world", "simple", "quick", "one file", "script",
            "snippet", "example", "demo", "fix bug", "add feature"
        ]
        
        # Count matches
        architect_score = sum(1 for kw in architect_keywords if kw in prompt_lower)
        simple_score = sum(1 for kw in simple_keywords if kw in prompt_lower)
        
        # Architect first if complex enough
        return architect_score > simple_score and architect_score >= 2
    
    def assign_agents_with_optimization(
        self,
        tasks: list[Task],
        architect_plan: Optional[dict] = None
    ) -> list[Task]:
        """
        Assign optimal agents to a list of tasks.
        
        Returns modified tasks with potentially reassigned agents.
        """
        optimized = []
        for task in tasks:
            best_agent = self.select_agent(task, architect_plan)
            if best_agent != task.agent_type:
                self._logger.info(
                    "Reassigning task to more efficient agent",
                    task_id=task.id,
                    original=task.agent_type.value if task.agent_type else None,
                    new=best_agent.value
                )
                task.agent_type = best_agent
            optimized.append(task)
        return optimized


# Global singleton
_agent_selector: Optional[AgentSelector] = None


def get_agent_selector() -> AgentSelector:
    """Get the global AgentSelector instance."""
    global _agent_selector
    if _agent_selector is None:
        _agent_selector = AgentSelector()
    return _agent_selector
