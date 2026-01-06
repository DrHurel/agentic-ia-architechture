#!/bin/bash
# =============================================================================
# Autonomous Project Demo
# =============================================================================
# This demo shows the autonomous agentic system in action.
# Submit ONE request -> System works until a working product is produced.
# =============================================================================

set -e

WORKSPACE="/home/hurel/repo/agentic-ia-architechture/workspace"
API_URL="http://localhost:8000"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "   🤖 AUTONOMOUS AGENTIC SYSTEM DEMO"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# Check if services are running
echo -e "${CYAN}Checking services...${NC}"
if ! curl -s "$API_URL/health" > /dev/null 2>&1; then
    echo -e "${RED}❌ API not running. Please start with: docker compose up -d${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Services are running${NC}"
echo ""

# Clean workspace
echo -e "${YELLOW}Cleaning workspace...${NC}"
sudo rm -rf "$WORKSPACE"/* 2>/dev/null || true
mkdir -p "$WORKSPACE"
echo -e "${GREEN}✓ Workspace cleaned${NC}"
echo ""

# The project request
PROJECT_REQUEST="Create a Python command-line todo list application with the following features:
1. A Todo class with id, title, description, completed fields
2. Functions to add, remove, complete, and list todos
3. Save and load todos from a JSON file
4. A main.py that provides a CLI menu
5. Unit tests for the core functionality

The application should be fully functional and well-organized."

echo "═══════════════════════════════════════════════════════════════════════"
echo "   PROJECT REQUEST:"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""
echo -e "${BLUE}$PROJECT_REQUEST${NC}"
echo ""

# Start the autonomous project
echo "═══════════════════════════════════════════════════════════════════════"
echo "   STARTING AUTONOMOUS EXECUTION"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

RESPONSE=$(curl -s -X POST "$API_URL/projects" \
    -H "Content-Type: application/json" \
    -d "{\"input\": \"$PROJECT_REQUEST\"}")

PROJECT_ID=$(echo "$RESPONSE" | grep -o '"project_id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}❌ Failed to start project${NC}"
    echo "$RESPONSE"
    exit 1
fi

echo -e "${GREEN}✓ Project started: $PROJECT_ID${NC}"
echo ""
echo -e "${CYAN}The system is now working autonomously...${NC}"
echo -e "${YELLOW}Press Ctrl+C to interrupt${NC}"
echo ""

# Monitor progress
monitor_project() {
    local last_status=""
    local last_files=""
    
    while true; do
        # Get project status
        STATUS_JSON=$(curl -s "$API_URL/projects/$PROJECT_ID" 2>/dev/null || echo '{"status":"unknown"}')
        STATUS=$(echo "$STATUS_JSON" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
        COMPLETED=$(echo "$STATUS_JSON" | grep -o '"completed_tasks":[0-9]*' | cut -d':' -f2)
        TOTAL=$(echo "$STATUS_JSON" | grep -o '"total_tasks":[0-9]*' | cut -d':' -f2)
        
        # Show status if changed
        if [ "$STATUS" != "$last_status" ]; then
            case "$STATUS" in
                "initializing") echo -e "📋 Status: ${CYAN}Initializing...${NC}" ;;
                "planning")     echo -e "🏗️  Status: ${YELLOW}Planning architecture...${NC}" ;;
                "in_progress")  echo -e "⚙️  Status: ${BLUE}Working on tasks...${NC}" ;;
                "testing")      echo -e "🧪 Status: ${CYAN}Running tests...${NC}" ;;
                "validating")   echo -e "✅ Status: ${YELLOW}Validating results...${NC}" ;;
                "completed")    echo -e "🎉 Status: ${GREEN}Project completed!${NC}" ;;
                "failed")       echo -e "❌ Status: ${RED}Project failed${NC}" ;;
                "interrupted")  echo -e "🛑 Status: ${YELLOW}Project interrupted${NC}" ;;
            esac
            last_status="$STATUS"
        fi
        
        # Show progress
        if [ -n "$COMPLETED" ] && [ -n "$TOTAL" ]; then
            echo -ne "\r   Progress: $COMPLETED/$TOTAL tasks completed"
        fi
        
        # Show new files
        current_files=$(ls "$WORKSPACE"/*.py 2>/dev/null | sort | tr '\n' ' ')
        if [ "$current_files" != "$last_files" ]; then
            echo ""
            echo -e "   ${GREEN}Files created:${NC}"
            for f in $WORKSPACE/*.py; do
                [ -f "$f" ] && echo "      📄 $(basename $f)"
            done
            last_files="$current_files"
        fi
        
        # Check if done
        if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] || [ "$STATUS" = "interrupted" ]; then
            break
        fi
        
        sleep 5
    done
}

# Handle Ctrl+C for interrupt
trap 'echo ""; echo -e "${YELLOW}Interrupting project...${NC}"; curl -s -X POST "$API_URL/projects/$PROJECT_ID/interrupt" > /dev/null; exit 0' INT

# Start monitoring
monitor_project

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "   RESULTS"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# List created files
echo -e "${CYAN}Created files:${NC}"
ls -la "$WORKSPACE"/*.py 2>/dev/null | awk '{print "  📄 " $NF " (" $5 " bytes)"}'
echo ""

# Show final status
echo -e "${CYAN}Final project status:${NC}"
curl -s "$API_URL/projects/$PROJECT_ID" | python3 -m json.tool 2>/dev/null || echo "Could not fetch status"
echo ""

# Try to run the project
if [ -f "$WORKSPACE/main.py" ]; then
    echo "═══════════════════════════════════════════════════════════════════════"
    echo "   RUNNING THE PROJECT"
    echo "═══════════════════════════════════════════════════════════════════════"
    echo ""
    echo -e "${YELLOW}Command: cd $WORKSPACE && python3 main.py${NC}"
    echo ""
    cd "$WORKSPACE"
    timeout 10 python3 main.py 2>&1 || echo "(timeout or error)"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "   DEMO COMPLETE"
echo "═══════════════════════════════════════════════════════════════════════"
