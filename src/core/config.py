"""
Configuration management using pydantic-settings.
Follows Single Responsibility Principle.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
import os


class KafkaSettings(BaseSettings):
    """Kafka-specific configuration."""
    bootstrap_servers: str = Field(default="localhost:29092")
    consumer_group: str = Field(default="agentic-ia")
    auto_offset_reset: str = Field(default="earliest")
    
    class Config:
        env_prefix = "KAFKA_"


class LlamaSettings(BaseSettings):
    """Ollama LLM configuration."""
    api_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="qwen2.5-coder:7b")  # Powerful coding model
    context_size: int = Field(default=8192)  # Larger context for complex tasks
    temperature: float = Field(default=0.2)  # Lower for more consistent code
    max_tokens: int = Field(default=8192)  # Increased for larger code outputs
    
    class Config:
        env_prefix = "LLAMA_"


class ServiceSettings(BaseSettings):
    """General service configuration."""
    name: str = Field(default="agentic-service")
    log_level: str = Field(default="INFO")
    workspace_path: str = Field(default="/workspace")
    
    class Config:
        env_prefix = "SERVICE_"


class AppSettings(BaseSettings):
    """Main application settings aggregating all configs."""
    
    class Config:
        env_file = ".env"
    
    @property
    def kafka(self) -> KafkaSettings:
        return KafkaSettings()
    
    @property
    def llama(self) -> LlamaSettings:
        return LlamaSettings()
    
    @property
    def service(self) -> ServiceSettings:
        return ServiceSettings()


@lru_cache()
def get_settings() -> AppSettings:
    """Get cached application settings."""
    return AppSettings()
