#!/bin/bash
# Complex Demo - Task Management API Project
# Demonstrates multi-agent collaboration on a real project

set -e

API_URL="http://localhost:8000"
WORKSPACE="/home/hurel/repo/agentic-ia-architechture/workspace"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

print_step() {
    echo -e "${YELLOW}▶ $1${NC}"
}

submit_task() {
    local prompt="$1"
    echo -e "${BLUE}📝 Submitting:${NC} ${prompt:0:70}..."
    
    response=$(curl -s -X POST "${API_URL}/tasks" \
        -H "Content-Type: application/json" \
        -d "{\"input\": \"$prompt\"}")
    
    count=$(echo "$response" | grep -oE '"message":"Created [0-9]+ task' | grep -oE '[0-9]+' || echo "0")
    echo -e "${GREEN}   ✓ Created $count task(s)${NC}"
    echo ""
    sleep 2
}

wait_processing() {
    local seconds=$1
    echo -n "⏳ Processing"
    for i in $(seq 1 $seconds); do
        echo -n "."
        sleep 1
    done
    echo " done!"
}

show_files() {
    print_header "Generated Project Structure"
    
    if [ -d "$WORKSPACE" ]; then
        echo "📁 workspace/"
        find "$WORKSPACE" -type f 2>/dev/null | sort | while read f; do
            rel="${f#$WORKSPACE/}"
            size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null || echo "?")
            depth=$(echo "$rel" | tr -cd '/' | wc -c)
            indent=$(printf '%*s' $((depth * 2)) '')
            basename=$(basename "$f")
            echo "   ${indent}├── 📄 $basename ($size bytes)"
        done
    fi
}

show_file_content() {
    local file="$1"
    local name="$2"
    if [ -f "$WORKSPACE/$file" ]; then
        echo ""
        echo -e "${CYAN}━━━ $name ($file) ━━━${NC}"
        cat "$WORKSPACE/$file"
        echo ""
    fi
}

main() {
    echo ""
    echo "🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀"
    echo "        COMPLEX DEMO: Task Management API Project"
    echo "🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀"
    echo ""
    echo "This demo creates a complete Task Management API with:"
    echo "  • Data models with validation"
    echo "  • Business logic layer"
    echo "  • REST API endpoints"
    echo "  • Unit tests"
    echo ""
    
    # Check health
    print_step "Checking system health..."
    health=$(curl -s "${API_URL}/health" | grep -o '"status":"healthy"' || echo "")
    if [ -z "$health" ]; then
        echo "❌ System not healthy. Run: docker compose up -d"
        exit 1
    fi
    echo -e "${GREEN}✓ System healthy${NC}"
    echo ""

    # Clean workspace
    rm -rf "$WORKSPACE"/*
    mkdir -p "$WORKSPACE"

    print_header "Phase 1: Data Models"
    
    submit_task "Create /workspace/models.py with Python dataclasses for a Task Management system: Task class with id (uuid string), title (string), description (string), status (enum: pending, in_progress, completed), priority (enum: low, medium, high), created_at (datetime), and due_date (optional datetime). Include proper type hints and a TaskStatus and Priority enum."

    submit_task "Create /workspace/exceptions.py with custom exceptions: TaskNotFoundError, ValidationError, and DuplicateTaskError. Each should have a message attribute."

    print_header "Phase 2: Business Logic"

    submit_task "Create /workspace/repository.py with a TaskRepository class that stores tasks in memory using a dict. Methods: add(task), get(task_id), get_all(), update(task), delete(task_id), find_by_status(status). Raise TaskNotFoundError when task not found."

    submit_task "Create /workspace/service.py with a TaskService class that uses TaskRepository. Methods: create_task(title, description, priority, due_date) - generates uuid and timestamps, get_task(id), list_tasks(status_filter=None), update_status(id, new_status), delete_task(id). Include input validation."

    print_header "Phase 3: API Layer"

    submit_task "Create /workspace/api.py with a simple Flask-like API class (no external deps). Methods: route decorator, get/post/put/delete handlers for /tasks endpoints. Include request/response handling with JSON serialization."

    submit_task "Create /workspace/main.py that imports all modules and creates a demo: instantiate repository, service, and api. Add 3 sample tasks, list them, update one status, and print results."

    print_header "Phase 4: Tests"

    submit_task "Create /workspace/test_models.py with unit tests using unittest: test Task creation, test enum values, test validation of required fields."

    submit_task "Create /workspace/test_service.py with unit tests for TaskService: test create_task, test get_task, test update_status, test list_tasks with filter, test delete_task, test error cases."

    # Wait for all tasks to process
    print_header "Processing Tasks..."
    wait_processing 60

    # Show results
    show_files

    print_header "Generated Code Preview"
    
    show_file_content "models.py" "Data Models"
    show_file_content "service.py" "Business Logic"
    show_file_content "main.py" "Main Entry Point"

    print_header "Demo Complete!"
    echo ""
    echo "📁 All files generated in: $WORKSPACE"
    echo ""
    echo "To run the project:"
    echo "  cd $WORKSPACE"
    echo "  python3 main.py"
    echo ""
    echo "To run tests:"
    echo "  cd $WORKSPACE"
    echo "  python3 -m pytest test_*.py -v"
    echo ""
}

main "$@"
