"""
CI/CD Agent - Entry Point
"""

import asyncio

from src.core.config import get_settings
from src.infrastructure.llm_client import LlamaClient
from src.infrastructure.os_access import CommandExecutor
from src.infrastructure.message_bus import MessageBus

from src.agents.cicd_agent import CICDAgent
from src.agents.runner import AgentRunner


async def main():
    settings = get_settings()
    
    llm_client = LlamaClient(settings.llama)
    await llm_client.connect()
    
    message_bus = MessageBus(settings.kafka, "agent-cicd")
    await message_bus.start()
    
    # CI/CD agent has more command permissions
    command_executor = CommandExecutor(
        settings.service.workspace_path,
        allowed_commands=["docker", "docker-compose", "git", "make", "npm", "pip", "python"]
    )
    
    agent = CICDAgent(message_bus, llm_client, command_executor)
    
    runner = AgentRunner(settings, agent)
    await runner.run()
    
    await llm_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
