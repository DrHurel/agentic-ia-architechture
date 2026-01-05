"""
Architect Agent - Entry Point
"""

import asyncio

from src.core.config import get_settings
from src.infrastructure.llm_client import LlamaClient
from src.infrastructure.os_access import FileReader
from src.infrastructure.message_bus import MessageBus

from src.agents.architect_agent import ArchitectAgent
from src.agents.runner import AgentRunner


async def main():
    settings = get_settings()
    
    llm_client = LlamaClient(settings.llama)
    await llm_client.connect()
    
    message_bus = MessageBus(settings.kafka, "agent-architect")
    await message_bus.start()
    
    file_reader = FileReader(settings.service.workspace_path)
    
    agent = ArchitectAgent(message_bus, llm_client, file_reader)
    
    runner = AgentRunner(settings, agent)
    await runner.run()
    
    await llm_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
