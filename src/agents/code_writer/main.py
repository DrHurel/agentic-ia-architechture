"""
Code Writer Agent - Entry Point
"""

import asyncio

from src.core.config import get_settings
from src.infrastructure.llm_client import LlamaClient
from src.infrastructure.os_access import FileReader, FileWriter
from src.infrastructure.message_bus import MessageBus

from src.agents.code_writer_agent import CodeWriterAgent
from src.agents.runner import AgentRunner


async def main():
    settings = get_settings()
    
    # Initialize dependencies
    llm_client = LlamaClient(settings.llama)
    await llm_client.connect()
    
    message_bus = MessageBus(settings.kafka, "agent-code-writer")
    await message_bus.start()
    
    file_reader = FileReader(settings.service.workspace_path)
    file_writer = FileWriter(settings.service.workspace_path)
    
    # Create agent
    agent = CodeWriterAgent(message_bus, llm_client, file_reader, file_writer)
    
    # Run
    runner = AgentRunner(settings, agent)
    await runner.run()
    
    # Cleanup
    await llm_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
