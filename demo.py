#!/usr/bin/env python3
"""
Demo Script - Demonstrates the Agentic IA Architecture
Shows end-to-end task processing with actual file creation.
"""

import httpx
import asyncio
import json
import time
from pathlib import Path

API_URL = "http://localhost:8000"
WORKSPACE = Path("/home/hurel/repo/agentic-ia-architechture/workspace")


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def print_task(task: dict):
    print(f"  📋 Task: {task['title']}")
    print(f"     Agent: {task['agent_type']}")
    print(f"     Priority: {task['priority']}")
    print(f"     ID: {task['id'][:8]}...")


async def create_task(prompt: str) -> dict:
    """Submit a task to the system."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_URL}/tasks",
            json={"input": prompt},
            timeout=60.0
        )
        return response.json()


async def get_feedback() -> list:
    """Get feedback from the system."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_URL}/feedback")
        return response.json().get("feedback", [])


async def check_health() -> bool:
    """Check if the system is healthy."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_URL}/health", timeout=5.0)
            return response.json().get("status") == "healthy"
    except:
        return False


async def demo_hello_world():
    """Demo: Create a Hello World Python script."""
    print_header("Demo 1: Hello World Script")
    
    prompt = """Create a Python script called hello.py in the workspace that:
    1. Prints 'Hello, World!' 
    2. Prints the current date and time
    3. Has a main() function
    Save it to /workspace/hello.py"""
    
    print(f"📝 Prompt: {prompt[:80]}...")
    print()
    
    result = await create_task(prompt)
    
    if result.get("success"):
        print(f"✅ Created {len(result['tasks'])} task(s):")
        for task in result["tasks"]:
            print_task(task)
    else:
        print(f"❌ Failed: {result.get('message')}")
    
    return result


async def demo_calculator():
    """Demo: Create a calculator module."""
    print_header("Demo 2: Calculator Module")
    
    prompt = """Create a Python calculator module at /workspace/calculator.py with:
    - add(a, b) function
    - subtract(a, b) function  
    - multiply(a, b) function
    - divide(a, b) function with zero division handling
    Include docstrings and type hints."""
    
    print(f"📝 Prompt: {prompt[:80]}...")
    print()
    
    result = await create_task(prompt)
    
    if result.get("success"):
        print(f"✅ Created {len(result['tasks'])} task(s):")
        for task in result["tasks"]:
            print_task(task)
    else:
        print(f"❌ Failed: {result.get('message')}")
    
    return result


async def demo_web_scraper():
    """Demo: Design a web scraper architecture."""
    print_header("Demo 3: Web Scraper Architecture")
    
    prompt = """Design and create a web scraper project structure at /workspace/scraper/:
    - Create scraper/main.py with a Scraper class
    - Create scraper/utils.py with helper functions
    - Create scraper/__init__.py
    The scraper should be able to fetch URLs and parse HTML."""
    
    print(f"📝 Prompt: {prompt[:80]}...")
    print()
    
    result = await create_task(prompt)
    
    if result.get("success"):
        print(f"✅ Created {len(result['tasks'])} task(s):")
        for task in result["tasks"]:
            print_task(task)
    else:
        print(f"❌ Failed: {result.get('message')}")
    
    return result


async def wait_for_processing(seconds: int = 15):
    """Wait for tasks to be processed."""
    print(f"\n⏳ Waiting {seconds}s for agents to process tasks...")
    for i in range(seconds):
        print(f"   {seconds - i}...", end="\r")
        await asyncio.sleep(1)
    print("   Done!    ")


async def show_feedback():
    """Display feedback from the system."""
    print_header("System Feedback")
    
    feedback = await get_feedback()
    
    if not feedback:
        print("  No feedback yet.")
        return
    
    for i, fb in enumerate(feedback[-5:], 1):  # Show last 5
        icon = "✅" if fb["type"] == "success" else "❌"
        msg = fb["message"][:100] + "..." if len(fb["message"]) > 100 else fb["message"]
        print(f"  {icon} {msg}")


async def show_workspace():
    """Show files in workspace."""
    print_header("Workspace Files")
    
    if not WORKSPACE.exists():
        print("  Workspace directory not found.")
        return
    
    files = list(WORKSPACE.rglob("*"))
    if not files:
        print("  No files created yet.")
        return
    
    for f in files:
        if f.is_file():
            rel = f.relative_to(WORKSPACE)
            size = f.stat().st_size
            print(f"  📄 {rel} ({size} bytes)")


async def main():
    """Run the demo."""
    print("\n" + "🤖 " * 20)
    print("    AGENTIC IA ARCHITECTURE - DEMO")
    print("🤖 " * 20)
    
    # Check health
    print("\n🔍 Checking system health...")
    if not await check_health():
        print("❌ System is not healthy. Please run 'docker compose up -d' first.")
        return
    print("✅ System is healthy!")
    
    # Create workspace if needed
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    
    # Run demos
    await demo_hello_world()
    await demo_calculator()
    await demo_web_scraper()
    
    # Wait for processing
    await wait_for_processing(20)
    
    # Show results
    await show_feedback()
    await show_workspace()
    
    print_header("Demo Complete!")
    print("  You can submit more tasks with:")
    print("  curl -X POST http://localhost:8000/tasks \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -d '{\"input\": \"Your task description\"}'")
    print()


if __name__ == "__main__":
    asyncio.run(main())
