"""
CI/CD Agent - Manages continuous integration and deployment.
Has Command access for build/deploy operations.
"""

import structlog

from src.core.base import BaseAgent
from src.core.interfaces import (
    AgentType, Task, TaskResult, TaskStatus,
    IMessagePublisher, ILLMClient, ICommandExecutor
)


logger = structlog.get_logger()


class CICDAgent(BaseAgent):
    """
    Agent responsible for CI/CD pipeline management.
    Capabilities: Create pipelines, run builds, manage deployments.
    """
    
    SYSTEM_PROMPT = """You are an expert CI/CD agent.
Your job is to create and manage build, test, and deployment pipelines.

When creating CI/CD configurations:
1. Follow security best practices
2. Optimize for speed and reliability
3. Include proper error handling
4. Use caching where appropriate
5. Include notifications for failures

Supported platforms:
- GitHub Actions
- GitLab CI
- Docker
- Docker Compose
"""
    
    def __init__(
        self,
        message_publisher: IMessagePublisher,
        llm_client: ILLMClient,
        command_executor: ICommandExecutor
    ):
        super().__init__(message_publisher, llm_client, "CICDAgent")
        self._command_executor = command_executor
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.CICD
    
    async def _do_execute(self, task: Task) -> TaskResult:
        """Execute a CI/CD task."""
        # Set context for command approval
        self._command_executor.set_context(
            agent_type=str(self.agent_type),
            task_id=task.id,
            project_id=task.payload.get("project_id")
        )
        
        payload = task.payload
        action = payload.get("action", "create_pipeline")
        
        if action == "create_pipeline":
            return await self._create_pipeline(task)
        elif action == "run_build":
            return await self._run_build(task)
        elif action == "deploy":
            return await self._deploy(task)
        elif action == "create_dockerfile":
            return await self._create_dockerfile(task)
        else:
            return await self._create_pipeline(task)
    
    async def _create_pipeline(self, task: Task) -> TaskResult:
        """Create CI/CD pipeline configuration."""
        payload = task.payload
        platform = payload.get("platform", "github_actions")
        project_type = payload.get("project_type", "python")
        
        prompt = f"""Create a CI/CD pipeline configuration:

Platform: {platform}
Project Type: {project_type}
Description: {task.description}

Include:
1. Build stage
2. Test stage
3. Lint/quality checks
4. Deployment stage (if applicable)
5. Proper caching
6. Environment management

Respond with JSON:
{{
    "pipeline_config": "complete pipeline configuration (YAML or JSON)",
    "file_path": "where to save the config",
    "stages": [
        {{
            "name": "stage name",
            "purpose": "what it does",
            "commands": ["commands run"]
        }}
    ],
    "secrets_needed": ["list of secrets to configure"],
    "setup_instructions": "how to set up the pipeline"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "pipeline_config": {"type": "string"},
                    "file_path": {"type": "string"},
                    "stages": {"type": "array"},
                    "secrets_needed": {"type": "array"},
                    "setup_instructions": {"type": "string"}
                },
                "required": ["pipeline_config", "file_path", "stages"]
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
    
    async def _run_build(self, task: Task) -> TaskResult:
        """Run a build command."""
        payload = task.payload
        build_command = payload.get("command", "docker build -t app .")
        
        try:
            stdout, stderr, return_code = await self._command_executor.execute_command(
                build_command,
                reason=f"Running build command for task: {task.title}"
            )
            
            success = return_code == 0
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
                output={
                    "command": build_command,
                    "return_code": return_code,
                    "stdout": stdout[-5000:],  # Limit output size
                    "stderr": stderr[-2000:],
                    "success": success
                },
                error=None if success else f"Build failed with return code {return_code}"
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _deploy(self, task: Task) -> TaskResult:
        """Execute deployment."""
        payload = task.payload
        deploy_command = payload.get("command", "docker-compose up -d")
        environment = payload.get("environment", "development")
        
        self._logger.info("Starting deployment", environment=environment)
        
        try:
            stdout, stderr, return_code = await self._command_executor.execute_command(
                deploy_command,
                reason=f"Deploying to {environment} environment"
            )
            
            success = return_code == 0
            
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
                output={
                    "command": deploy_command,
                    "environment": environment,
                    "return_code": return_code,
                    "stdout": stdout[-5000:],
                    "stderr": stderr[-2000:],
                    "success": success
                },
                error=None if success else f"Deployment failed with return code {return_code}"
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e)
            )
    
    async def _create_dockerfile(self, task: Task) -> TaskResult:
        """Create a Dockerfile for the project."""
        payload = task.payload
        project_type = payload.get("project_type", "python")
        base_image = payload.get("base_image", "python:3.11-slim")
        
        prompt = f"""Create a Dockerfile:

Project Type: {project_type}
Base Image: {base_image}
Description: {task.description}

Include:
1. Optimized layer caching
2. Security best practices (non-root user)
3. Multi-stage build if beneficial
4. Proper COPY ordering
5. Health checks

Respond with JSON:
{{
    "dockerfile": "complete Dockerfile content",
    "docker_compose": "optional docker-compose.yml if useful",
    "build_args": ["list of build args"],
    "environment_variables": ["env vars needed at runtime"],
    "notes": "any important notes"
}}"""
        
        try:
            result = await self._llm_client.generate_structured(prompt, {
                "type": "object",
                "properties": {
                    "dockerfile": {"type": "string"},
                    "docker_compose": {"type": "string"},
                    "build_args": {"type": "array"},
                    "environment_variables": {"type": "array"},
                    "notes": {"type": "string"}
                },
                "required": ["dockerfile"]
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
