"""
Message Bus Service - Standalone service entry point.
Routes messages between components.
"""

import asyncio
import structlog

from src.core.config import get_settings
from src.infrastructure.message_bus import MessageBus, Topics


logger = structlog.get_logger()


async def main():
    """Run the message bus service."""
    settings = get_settings()
    
    logger.info("Starting Message Bus Service...")
    
    # Create message bus with dedicated group
    message_bus = MessageBus(settings.kafka, "message-bus-service")
    await message_bus.start()
    
    logger.info("Message Bus Service started")
    
    try:
        # Just keep the service running - it routes messages
        await message_bus.start_consuming()
    except KeyboardInterrupt:
        logger.info("Shutting down Message Bus Service...")
    finally:
        await message_bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
