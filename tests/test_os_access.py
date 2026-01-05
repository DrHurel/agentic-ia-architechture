"""
Tests for OS access implementations.
"""

import pytest
import tempfile
import os
from pathlib import Path

from src.infrastructure.os_access import FileReader, FileWriter, CommandExecutor


class TestFileReader:
    """Tests for FileReader."""
    
    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("Hello, World!")
            
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()
            (subdir / "nested.txt").write_text("Nested content")
            
            yield tmpdir
    
    @pytest.fixture
    def file_reader(self, temp_workspace):
        return FileReader(temp_workspace)
    
    @pytest.mark.asyncio
    async def test_read_file(self, file_reader):
        content = await file_reader.read_file("test.txt")
        assert content == "Hello, World!"
    
    @pytest.mark.asyncio
    async def test_read_nested_file(self, file_reader):
        content = await file_reader.read_file("subdir/nested.txt")
        assert content == "Nested content"
    
    @pytest.mark.asyncio
    async def test_file_not_found(self, file_reader):
        with pytest.raises(FileNotFoundError):
            await file_reader.read_file("nonexistent.txt")
    
    @pytest.mark.asyncio
    async def test_list_directory(self, file_reader):
        entries = await file_reader.list_directory(".")
        assert "test.txt" in entries
        assert "subdir/" in entries
    
    @pytest.mark.asyncio
    async def test_file_exists(self, file_reader):
        assert await file_reader.file_exists("test.txt") is True
        assert await file_reader.file_exists("nonexistent.txt") is False
    
    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, file_reader):
        with pytest.raises(PermissionError):
            await file_reader.read_file("../outside.txt")


class TestFileWriter:
    """Tests for FileWriter."""
    
    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def file_writer(self, temp_workspace):
        return FileWriter(temp_workspace)
    
    @pytest.mark.asyncio
    async def test_write_file(self, file_writer, temp_workspace):
        await file_writer.write_file("new_file.txt", "New content")
        
        content = (Path(temp_workspace) / "new_file.txt").read_text()
        assert content == "New content"
    
    @pytest.mark.asyncio
    async def test_write_creates_directories(self, file_writer, temp_workspace):
        await file_writer.write_file("new/nested/file.txt", "Content")
        
        content = (Path(temp_workspace) / "new/nested/file.txt").read_text()
        assert content == "Content"
    
    @pytest.mark.asyncio
    async def test_delete_file(self, file_writer, temp_workspace):
        file_path = Path(temp_workspace) / "to_delete.txt"
        file_path.write_text("Delete me")
        
        await file_writer.delete_file("to_delete.txt")
        
        assert not file_path.exists()
    
    @pytest.mark.asyncio
    async def test_create_directory(self, file_writer, temp_workspace):
        await file_writer.create_directory("new_dir/nested")
        
        assert (Path(temp_workspace) / "new_dir/nested").is_dir()


class TestCommandExecutor:
    """Tests for CommandExecutor."""
    
    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def command_executor(self, temp_workspace):
        return CommandExecutor(temp_workspace, allowed_commands=["echo", "ls", "cat"])
    
    @pytest.mark.asyncio
    async def test_execute_command(self, command_executor):
        stdout, stderr, code = await command_executor.execute_command("echo 'hello'")
        
        assert "hello" in stdout
        assert code == 0
    
    @pytest.mark.asyncio
    async def test_blocked_command(self, command_executor):
        with pytest.raises(PermissionError):
            await command_executor.execute_command("rm -rf /")
