#!/bin/bash
# Demo Script - Demonstrates the Agentic IA Architecture
# Shows end-to-end task processing with actual file creation

set -e

API_URL="http://localhost:8000"
WORKSPACE="/home/hurel/repo/agentic-ia-architechture/workspace"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_header() {
    echo ""
    echo "============================================================"
    echo -e "  ${BLUE}$1${NC}"
    echo "============================================================"
    echo ""
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}📝 $1${NC}"
}

# Check if system is healthy
check_health() {
    print_header "Checking System Health"
    
    response=$(curl -s "${API_URL}/health" 2>/dev/null || echo '{"status":"error"}')
    status=$(echo "$response" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    
    if [ "$status" = "healthy" ]; then
        print_success "System is healthy!"
        return 0
    else
        print_error "System is not healthy. Please run 'docker compose up -d' first."
        return 1
    fi
}

# Submit a task
submit_task() {
    local prompt="$1"
    local name="$2"
    
    print_info "Task: $name"
    echo "   Prompt: ${prompt:0:60}..."
    echo ""
    
    response=$(curl -s -X POST "${API_URL}/tasks" \
        -H "Content-Type: application/json" \
        -d "{\"input\": \"$prompt\"}")
    
    success=$(echo "$response" | grep -o '"success":true' || echo "")
    
    if [ -n "$success" ]; then
        count=$(echo "$response" | grep -o '"message":"Created [0-9]* task' | grep -oE '[0-9]+')
        print_success "Created $count task(s)"
        
        # Show task details
        echo "$response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for task in data.get('tasks', [])[:3]:
        print(f\"   📋 {task['title']} -> {task['agent_type']}\")
except: pass
" 2>/dev/null || true
    else
        print_error "Failed to create task"
        echo "   Response: $response"
    fi
    echo ""
}

# Wait for processing
wait_for_processing() {
    local seconds=$1
    echo ""
    echo -n "⏳ Waiting ${seconds}s for agents to process tasks"
    for i in $(seq 1 $seconds); do
        echo -n "."
        sleep 1
    done
    echo " Done!"
    echo ""
}

# Show feedback
show_feedback() {
    print_header "System Feedback (Last 5)"
    
    curl -s "${API_URL}/feedback" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    feedback = data.get('feedback', [])[-5:]
    for fb in feedback:
        icon = '✅' if fb.get('type') == 'success' else '❌'
        msg = fb.get('message', '')[:80]
        if len(fb.get('message', '')) > 80:
            msg += '...'
        print(f'  {icon} {msg}')
except Exception as e:
    print(f'  Error reading feedback: {e}')
" 2>/dev/null || echo "  Could not read feedback"
}

# Show workspace files
show_workspace() {
    print_header "Workspace Files"
    
    mkdir -p "$WORKSPACE"
    
    if [ -z "$(ls -A $WORKSPACE 2>/dev/null)" ]; then
        echo "  📁 Workspace is empty"
    else
        find "$WORKSPACE" -type f 2>/dev/null | while read f; do
            size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo "?")
            rel="${f#$WORKSPACE/}"
            echo "  📄 $rel ($size bytes)"
        done
        
        echo ""
        echo "  File contents:"
        find "$WORKSPACE" -type f -name "*.py" 2>/dev/null | head -3 | while read f; do
            echo ""
            echo "  --- ${f#$WORKSPACE/} ---"
            head -20 "$f" 2>/dev/null | sed 's/^/  /'
            lines=$(wc -l < "$f" 2>/dev/null || echo 0)
            if [ "$lines" -gt 20 ]; then
                echo "  ... ($lines total lines)"
            fi
        done
    fi
}

# Main demo
main() {
    echo ""
    echo "🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖"
    echo "    AGENTIC IA ARCHITECTURE - DEMO"
    echo "🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖🤖"
    
    # Check health first
    if ! check_health; then
        exit 1
    fi
    
    # Create workspace
    mkdir -p "$WORKSPACE"
    
    # Demo 1: Hello World
    print_header "Demo 1: Hello World Script"
    submit_task "Create a Python script called hello.py that prints Hello World and the current date. Save it to /workspace/hello.py" "Hello World"
    
    # Demo 2: Calculator
    print_header "Demo 2: Calculator Module"
    submit_task "Create a Python calculator module at /workspace/calculator.py with add, subtract, multiply, divide functions. Include docstrings and handle division by zero." "Calculator"
    
    # Demo 3: Simple API
    print_header "Demo 3: Simple Data Class"
    submit_task "Create a Python file /workspace/models.py with a User dataclass that has name, email, and age fields. Include validation." "Data Models"
    
    # Wait for processing
    wait_for_processing 25
    
    # Show results
    show_feedback
    show_workspace
    
    print_header "Demo Complete!"
    echo "  You can submit more tasks with:"
    echo ""
    echo "  curl -X POST http://localhost:8000/tasks \\"
    echo "    -H 'Content-Type: application/json' \\"
    echo "    -d '{\"input\": \"Your task description\"}'"
    echo ""
    echo "  Check feedback:"
    echo "  curl http://localhost:8000/feedback | python3 -m json.tool"
    echo ""
}

main "$@"
