"""
Tester BlackBox Agent - Entry Point
"""

import asyncio

from src.core.config import get_settings
from src.infrastructure.llm_client import LlamaClient
from src.infrastructure.os_access import CommandExecutor
from src.infrastructure.message_bus import MessageBus

from src.agents.tester_blackbox_agent import TesterBlackBoxAgent
from src.agents.runner import AgentRunner


async def main():
    settings = get_settings()
    
    llm_client = LlamaClient(settings.llama)
    await llm_client.connect()
    
    message_bus = MessageBus(settings.kafka, "agent-tester-blackbox")
    await message_bus.start()
    
    command_executor = CommandExecutor(
        settings.service.workspace_path,
        allowed_commands=["pytest", "python", "curl", "httpie"]
    )
    
    agent = TesterBlackBoxAgent(message_bus, llm_client, command_executor)
    
    runner = AgentRunner(settings, agent)
    await runner.run()
    
    await llm_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
