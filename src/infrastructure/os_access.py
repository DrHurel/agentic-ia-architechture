"""
OS/VSCode API Access implementations.
Follows Interface Segregation Principle - separate interfaces for read, write, and command.
"""

import os
import asyncio
import aiofiles
from pathlib import Path
from typing import Optional
import structlog

from src.core.interfaces import IFileReader, IFileWriter, ICommandExecutor
from src.core.config import ServiceSettings


logger = structlog.get_logger()


class FileReader(IFileReader):
    """Implementation of read-only file access."""
    
    def __init__(self, workspace_path: str):
        self._workspace = Path(workspace_path)
        self._logger = logger.bind(component="FileReader")
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve and validate path within workspace."""
        resolved = self._workspace / path
        # Security: ensure path is within workspace
        try:
            resolved.resolve().relative_to(self._workspace.resolve())
        except ValueError:
            raise PermissionError(f"Path {path} is outside workspace")
        return resolved
    
    async def read_file(self, path: str) -> str:
        """Read file content."""
        file_path = self._resolve_path(path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            self._logger.debug("File read", path=path, size=len(content))
            return content
    
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
    """Implementation of write file access."""
    
    def __init__(self, workspace_path: str):
        self._workspace = Path(workspace_path)
        self._logger = logger.bind(component="FileWriter")
    
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
    
    async def delete_file(self, path: str) -> None:
        """Delete a file."""
        file_path = self._resolve_path(path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if file_path.is_dir():
            raise IsADirectoryError(f"Cannot delete directory with delete_file: {path}")
        
        file_path.unlink()
        self._logger.info("File deleted", path=path)
    
    async def create_directory(self, path: str) -> None:
        """Create a directory."""
        dir_path = self._resolve_path(path)
        dir_path.mkdir(parents=True, exist_ok=True)
        self._logger.info("Directory created", path=path)


class CommandExecutor(ICommandExecutor):
    """Implementation of command execution."""
    
    def __init__(self, workspace_path: str, allowed_commands: Optional[list[str]] = None):
        self._workspace = Path(workspace_path)
        self._allowed_commands = allowed_commands  # None means all allowed (careful!)
        self._logger = logger.bind(component="CommandExecutor")
    
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
    
    async def execute_command(self, command: str, cwd: Optional[str] = None) -> tuple[str, str, int]:
        """Execute a shell command."""
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
