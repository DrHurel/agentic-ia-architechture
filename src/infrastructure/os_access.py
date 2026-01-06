"""
OS/VSCode API Access implementations.
Follows Interface Segregation Principle - separate interfaces for read, write, and command.

Optimizations:
- File content caching with TTL
- Batch file operations
- Parallel I/O where safe
"""

import os
import asyncio
import aiofiles
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
import structlog

from src.core.interfaces import IFileReader, IFileWriter, ICommandExecutor
from src.core.config import ServiceSettings


logger = structlog.get_logger()


class CachedEntry:
    """A cached file entry with TTL."""
    def __init__(self, content: str, ttl_seconds: float = 30.0):
        self.content = content
        self.created_at = datetime.now()
        self.ttl = timedelta(seconds=ttl_seconds)
    
    @property
    def is_valid(self) -> bool:
        return datetime.now() - self.created_at < self.ttl


class FileReader(IFileReader):
    """
    Implementation of read-only file access with caching.
    
    Optimizations:
    - In-memory cache with TTL for repeated reads
    - Batch read support for multiple files
    """
    
    def __init__(self, workspace_path: str, cache_ttl: float = 30.0):
        self._workspace = Path(workspace_path)
        self._logger = logger.bind(component="FileReader")
        self._cache: Dict[str, CachedEntry] = {}
        self._cache_ttl = cache_ttl
        self._cache_lock = asyncio.Lock()
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve and validate path within workspace."""
        resolved = self._workspace / path
        # Security: ensure path is within workspace
        try:
            resolved.resolve().relative_to(self._workspace.resolve())
        except ValueError:
            raise PermissionError(f"Path {path} is outside workspace")
        return resolved
    
    async def read_file(self, path: str, use_cache: bool = True) -> str:
        """Read file content with optional caching."""
        # Check cache first
        if use_cache:
            async with self._cache_lock:
                if path in self._cache and self._cache[path].is_valid:
                    self._logger.debug("Cache hit", path=path)
                    return self._cache[path].content
        
        file_path = self._resolve_path(path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        # Update cache
        if use_cache:
            async with self._cache_lock:
                self._cache[path] = CachedEntry(content, self._cache_ttl)
        
        self._logger.debug("File read", path=path, size=len(content))
        return content
    
    async def read_files_batch(self, paths: List[str]) -> Dict[str, str]:
        """
        Read multiple files in parallel.
        
        Returns dict mapping path to content (or error string for failed reads).
        """
        async def read_one(path: str) -> Tuple[str, str]:
            try:
                content = await self.read_file(path)
                return (path, content)
            except Exception as e:
                return (path, f"ERROR: {e}")
        
        results = await asyncio.gather(*[read_one(p) for p in paths])
        return dict(results)
    
    def invalidate_cache(self, path: Optional[str] = None) -> None:
        """Invalidate cache for a path or all paths."""
        if path:
            self._cache.pop(path, None)
        else:
            self._cache.clear()
    
    async def list_directory(self, path: str) -> list[str]:
        """List directory contents."""
        dir_path = self._resolve_path(path)
        
        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
        
        entries = []
        for entry in dir_path.iterdir():
            name = entry.name
            if entry.is_dir():
                name += "/"
            entries.append(name)
        
        self._logger.debug("Directory listed", path=path, entries=len(entries))
        return sorted(entries)
    
    async def file_exists(self, path: str) -> bool:
        """Check if file exists."""
        try:
            file_path = self._resolve_path(path)
            return file_path.exists()
        except PermissionError:
            return False


class FileWriter(IFileWriter):
    """
    Implementation of write file access.
    
    Optimizations:
    - Batch write support
    - Invalidates reader cache on write
    """
    
    def __init__(self, workspace_path: str, file_reader: Optional[FileReader] = None):
        self._workspace = Path(workspace_path)
        self._logger = logger.bind(component="FileWriter")
        self._reader = file_reader  # For cache invalidation
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve and validate path within workspace."""
        resolved = self._workspace / path
        # Security: ensure path is within workspace
        try:
            resolved.resolve().relative_to(self._workspace.resolve())
        except ValueError:
            raise PermissionError(f"Path {path} is outside workspace")
        return resolved
    
    async def write_file(self, path: str, content: str) -> None:
        """Write content to file."""
        file_path = self._resolve_path(path)
        
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
            self._logger.info("File written", path=path, size=len(content))
        
        # Invalidate cache
        if self._reader:
            self._reader.invalidate_cache(path)
    
    async def write_files_batch(self, files: Dict[str, str]) -> Dict[str, bool]:
        """
        Write multiple files in parallel.
        
        Args:
            files: Dict mapping path to content
            
        Returns:
            Dict mapping path to success status
        """
        async def write_one(path: str, content: str) -> Tuple[str, bool]:
            try:
                await self.write_file(path, content)
                return (path, True)
            except Exception as e:
                self._logger.error("Batch write failed", path=path, error=str(e))
                return (path, False)
        
        results = await asyncio.gather(*[write_one(p, c) for p, c in files.items()])
        return dict(results)
    
    async def delete_file(self, path: str) -> None:
        """Delete a file."""
        file_path = self._resolve_path(path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if file_path.is_dir():
            raise IsADirectoryError(f"Cannot delete directory with delete_file: {path}")
        
        file_path.unlink()
        self._logger.info("File deleted", path=path)
        
        # Invalidate cache
        if self._reader:
            self._reader.invalidate_cache(path)
    
    async def create_directory(self, path: str) -> None:
        """Create a directory."""
        dir_path = self._resolve_path(path)
        dir_path.mkdir(parents=True, exist_ok=True)
        self._logger.info("Directory created", path=path)


class CommandExecutor(ICommandExecutor):
    """
    Implementation of command execution with approval support.
    
    Commands can require user approval before execution.
    Session-level settings can enable blanket approval.
    """
    
    def __init__(
        self, 
        workspace_path: str, 
        allowed_commands: Optional[list[str]] = None,
        require_approval: bool = True,
        agent_type: str = "unknown",
        task_id: str = "",
        project_id: Optional[str] = None
    ):
        self._workspace = Path(workspace_path)
        self._allowed_commands = allowed_commands  # None means all allowed (careful!)
        self._require_approval = require_approval
        self._agent_type = agent_type
        self._task_id = task_id
        self._project_id = project_id
        self._logger = logger.bind(component="CommandExecutor")
    
    def set_context(self, agent_type: str, task_id: str, project_id: Optional[str] = None) -> None:
        """Set execution context for approval requests."""
        self._agent_type = agent_type
        self._task_id = task_id
        self._project_id = project_id
    
    def _validate_command(self, command: str) -> bool:
        """Validate command against allowlist."""
        if self._allowed_commands is None:
            return True
        
        # Check if command starts with any allowed command
        cmd_parts = command.split()
        if not cmd_parts:
            return False
        
        base_cmd = cmd_parts[0]
        return base_cmd in self._allowed_commands
    
    async def execute_command(
        self, 
        command: str, 
        cwd: Optional[str] = None,
        reason: str = "Agent requested command execution"
    ) -> tuple[str, str, int]:
        """
        Execute a shell command with optional approval.
        
        If require_approval is True and session_allow_all is False,
        this will request user approval before executing.
        """
        if not self._validate_command(command):
            raise PermissionError(f"Command not allowed: {command}")
        
        work_dir = self._workspace
        if cwd:
            work_dir = self._workspace / cwd
            # Security check
            try:
                work_dir.resolve().relative_to(self._workspace.resolve())
            except ValueError:
                raise PermissionError(f"Working directory {cwd} is outside workspace")
        
        # Request approval if required
        if self._require_approval:
            from src.core.command_approval import get_approval_manager
            import uuid
            
            manager = get_approval_manager()
            request_id = str(uuid.uuid4())
            
            approved, message = await manager.request_approval(
                request_id=request_id,
                command=command,
                agent_type=self._agent_type,
                task_id=self._task_id,
                project_id=self._project_id,
                working_dir=str(work_dir),
                reason=reason
            )
            
            if not approved:
                self._logger.warning("Command rejected", command=command, message=message)
                raise PermissionError(f"Command rejected: {message}")
        
        self._logger.info("Executing command", command=command, cwd=str(work_dir))
        
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        return_code = process.returncode or 0
        
        stdout_str = stdout.decode('utf-8', errors='replace')
        stderr_str = stderr.decode('utf-8', errors='replace')
        
        self._logger.info(
            "Command completed",
            command=command,
            return_code=return_code,
            stdout_len=len(stdout_str),
            stderr_len=len(stderr_str)
        )
        
        return stdout_str, stderr_str, return_code


class OSAccessFactory:
    """Factory for creating OS access instances."""
    
    @staticmethod
    def create_reader(settings: ServiceSettings) -> FileReader:
        """Create a FileReader."""
        return FileReader(settings.workspace_path)
    
    @staticmethod
    def create_writer(settings: ServiceSettings) -> FileWriter:
        """Create a FileWriter."""
        return FileWriter(settings.workspace_path)
    
    @staticmethod
    def create_executor(
        settings: ServiceSettings,
        allowed_commands: Optional[list[str]] = None
    ) -> CommandExecutor:
        """Create a CommandExecutor."""
        return CommandExecutor(settings.workspace_path, allowed_commands)
