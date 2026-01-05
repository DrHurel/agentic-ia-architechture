"""
LLM Client implementation for Ollama local model.
Follows Single Responsibility Principle - handles only LLM communication.
"""

import json
import httpx
import structlog
from typing import Optional

from src.core.interfaces import ILLMClient
from src.core.config import LlamaSettings


logger = structlog.get_logger()


class LlamaClient(ILLMClient):
    """
    Client for interacting with Ollama local model server.
    Compatible with Ollama API.
    """
    
    def __init__(self, settings: LlamaSettings, model: Optional[str] = None):
        self._settings = settings
        self._model = model or settings.model
        self._client: Optional[httpx.AsyncClient] = None
        self._logger = logger.bind(component="LlamaClient")
    
    async def connect(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self._settings.api_url,
            timeout=120.0  # LLM requests can be slow
        )
        self._logger.info("LlamaClient connected", url=self._settings.api_url)
    
    async def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            self._logger.info("LlamaClient disconnected")
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate text from a prompt using Ollama."""
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() first.")
        
        # Build messages for Ollama chat API
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self._settings.temperature,
                "num_predict": self._settings.max_tokens,
            }
        }
        
        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()
            return result.get("message", {}).get("content", "").strip()
        except httpx.HTTPError as e:
            self._logger.error("LLM request failed", error=str(e))
            raise
    
    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        """
        Generate structured output matching the schema.
        Uses prompt engineering to get JSON output.
        """
        structured_prompt = f"""You must respond with valid JSON matching this schema:
{json.dumps(schema, indent=2)}

{prompt}

Respond ONLY with valid JSON, no other text:"""
        
        system_prompt = "You are a helpful assistant that always responds with valid JSON."
        
        response = await self.generate(structured_prompt, system_prompt)
        
        # Try to parse JSON from response
        try:
            # Clean response - extract JSON if wrapped in other text
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            
            return json.loads(response.strip())
        except json.JSONDecodeError as e:
            self._logger.error("Failed to parse LLM JSON response", error=str(e), response=response)
            raise ValueError(f"LLM did not return valid JSON: {e}")
    
    async def health_check(self) -> bool:
        """Check if the Ollama server is healthy."""
        if not self._client:
            return False
        
        try:
            response = await self._client.get("/api/tags")
            return response.status_code == 200
        except Exception:
            return False
    
    async def ensure_model(self) -> bool:
        """Ensure the model is available, pull if needed."""
        if not self._client:
            return False
        
        try:
            # Check if model exists
            response = await self._client.get("/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "").split(":")[0] for m in models]
                if self._model.split(":")[0] in model_names:
                    return True
            
            # Pull the model
            self._logger.info("Pulling model", model=self._model)
            response = await self._client.post(
                "/api/pull",
                json={"name": self._model},
                timeout=600.0  # Model download can take a while
            )
            return response.status_code == 200
        except Exception as e:
            self._logger.error("Failed to ensure model", error=str(e))
            return False


class LlamaClientFactory:
    """Factory for creating LlamaClient instances."""
    
    @staticmethod
    async def create(settings: LlamaSettings, model: Optional[str] = None) -> LlamaClient:
        """Create and connect a LlamaClient."""
        client = LlamaClient(settings, model)
        await client.connect()
        return client
