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
        structured_prompt = f"""{prompt}

Respond with ONLY valid JSON (no markdown, no explanation), matching this schema:
{json.dumps(schema, indent=2)}"""
        
        system_prompt = "You are a coding assistant. Always respond with pure JSON only, never markdown code blocks."
        
        response = await self.generate(structured_prompt, system_prompt)
        
        # Try to parse JSON from response
        try:
            # Clean response - extract JSON if wrapped
            response = response.strip()
            
            # Remove markdown code blocks
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                parts = response.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("{") or part.startswith("["):
                        response = part
                        break
            
            # Find JSON in response
            start_brace = response.find("{")
            start_bracket = response.find("[")
            
            if start_brace >= 0 and (start_bracket < 0 or start_brace < start_bracket):
                # Find matching closing brace
                depth = 0
                for i, c in enumerate(response[start_brace:]):
                    if c == "{": depth += 1
                    elif c == "}": depth -= 1
                    if depth == 0:
                        response = response[start_brace:start_brace+i+1]
                        break
            elif start_bracket >= 0:
                depth = 0
                for i, c in enumerate(response[start_bracket:]):
                    if c == "[": depth += 1
                    elif c == "]": depth -= 1
                    if depth == 0:
                        response = response[start_bracket:start_bracket+i+1]
                        break
            
            return json.loads(response.strip())
        except json.JSONDecodeError as e:
            self._logger.error("Failed to parse LLM JSON response", error=str(e), response=response[:200])
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
