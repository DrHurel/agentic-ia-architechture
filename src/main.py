"""
Main application entry point.
Orchestrates all services and agents.
"""

import asyncio
import signal
import structlog
from typing import Optional

from src.core.config import get_settings, AppSettings


logger = structlog.get_logger()


class Application:
    """
    Main application orchestrator.
    Manages the lifecycle of all services and agents.
    """
    
    def __init__(self, settings: AppSettings):
        self._settings = settings
        self._running = False
        self._tasks: list[asyncio.Task] = []
    
    async def start(self) -> None:
        """Start all services."""
        logger.info("Starting Agentic IA System...")
        self._running = True
        
        # Note: In Docker Compose deployment, each service runs in its own container
        # This main.py is for local development/testing all services together
        
        logger.info("Agentic IA System started")
        logger.info("Services are designed to run in separate containers via docker-compose")
        logger.info("Use 'docker-compose up' to start the full system")
    
    async def stop(self) -> None:
        """Stop all services."""
        logger.info("Stopping Agentic IA System...")
        self._running = False
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        logger.info("Agentic IA System stopped")
    
    async def run(self) -> None:
        """Run the application."""
        await self.start()
        
        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
        
        # Keep running
        while self._running:
            await asyncio.sleep(1)


async def main():
    """Application entry point."""
    # Configure structured logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer()
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    settings = get_settings()
    app = Application(settings)
    
    try:
        await app.run()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
