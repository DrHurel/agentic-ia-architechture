#!/bin/bash
# Direct Code Generation Demo
# Bypasses complex task formulation for more reliable results

WORKSPACE="/home/hurel/repo/agentic-ia-architechture/workspace"
API_URL="http://localhost:8000"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "🎯 Direct Code Generation Demo"
echo "================================"
echo ""

# Clean workspace
sudo rm -rf "$WORKSPACE"/* 2>/dev/null
mkdir -p "$WORKSPACE"

# Function to submit and wait
submit() {
    local name="$1"
    local prompt="$2"
    
    echo -e "${BLUE}📝 Creating: $name${NC}"
    curl -s -X POST "$API_URL/tasks" \
        -H "Content-Type: application/json" \
        -d "{\"input\": \"$prompt\"}" > /dev/null
    sleep 12
    
    if [ -f "$WORKSPACE/$name" ]; then
        echo -e "${GREEN}✓ Created $name${NC}"
        echo "  Content:"
        head -20 "$WORKSPACE/$name" | sed 's/^/    /'
        lines=$(wc -l < "$WORKSPACE/$name")
        [ "$lines" -gt 20 ] && echo "    ... ($lines total lines)"
    else
        echo "  ⏳ Processing..."
    fi
    echo ""
}

echo "Step 1: Creating models.py"
submit "models.py" "Write to /workspace/models.py: from dataclasses import dataclass. Create @dataclass class Task with id: str, title: str, done: bool = False"

echo "Step 2: Creating utils.py"  
submit "utils.py" "Write to /workspace/utils.py: def generate_id(): import uuid; return str(uuid.uuid4()). def format_task(task): return f'[{task.id[:8]}] {task.title}'"

echo "Step 3: Creating main.py"
submit "main.py" "Write to /workspace/main.py: from models import Task. from utils import generate_id, format_task. task = Task(id=generate_id(), title='Demo Task'). print(format_task(task))"

echo ""
echo "════════════════════════════════════════"
echo "  Final Results"
echo "════════════════════════════════════════"
echo ""
echo "Files in workspace:"
ls -la "$WORKSPACE"/*.py 2>/dev/null | awk '{print "  📄 " $NF " (" $5 " bytes)"}'

echo ""
echo "To run: cd $WORKSPACE && python3 main.py"
