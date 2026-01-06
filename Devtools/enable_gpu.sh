#!/bin/bash
# =============================================================================
# Enable NVIDIA GPU Support for Docker
# =============================================================================
# This script installs the NVIDIA Container Toolkit and configures Docker
# to use your GPU for faster LLM inference.
#
# Requirements:
# - NVIDIA GPU with drivers installed (nvidia-smi should work)
# - Docker installed (Docker Desktop or native)
# - Ubuntu/Debian based system (or WSL2)
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "   🎮 NVIDIA Container Toolkit Installation"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# Check for NVIDIA GPU
if ! command -v nvidia-smi &> /dev/null; then
    echo -e "${RED}❌ nvidia-smi not found. Please install NVIDIA drivers first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ NVIDIA GPU detected:${NC}"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
echo ""

# Check if already installed
if command -v nvidia-ctk &> /dev/null; then
    echo -e "${YELLOW}⚠️  NVIDIA Container Toolkit is already installed.${NC}"
    nvidia-ctk --version
fi

echo -e "${YELLOW}Installing/Updating NVIDIA Container Toolkit...${NC}"
echo ""

# Add NVIDIA repository
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null || true

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Update and install
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Configure Docker runtime
echo ""
echo -e "${YELLOW}Configuring Docker runtime...${NC}"
sudo nvidia-ctk runtime configure --runtime=docker

# Detect if running in WSL2 or native Linux
if grep -qi microsoft /proc/version 2>/dev/null; then
    echo ""
    echo -e "${YELLOW}WSL2 detected - Docker Desktop manages the daemon${NC}"
    echo ""
    echo -e "${GREEN}✓ NVIDIA Container Toolkit installed!${NC}"
    echo ""
    echo "═══════════════════════════════════════════════════════════════════════"
    echo "   FOR WSL2 + DOCKER DESKTOP:"
    echo "═══════════════════════════════════════════════════════════════════════"
    echo ""
    echo "1. Open Docker Desktop on Windows"
    echo "2. Go to Settings → Resources → WSL Integration"
    echo "3. Ensure your WSL distro is enabled"
    echo "4. Go to Settings → Docker Engine"
    echo "5. Add this to the JSON config:"
    echo ""
    echo '   "runtimes": {'
    echo '     "nvidia": {'
    echo '       "path": "nvidia-container-runtime",'
    echo '       "runtimeArgs": []'
    echo '     }'
    echo '   }'
    echo ""
    echo "6. Click 'Apply & Restart'"
    echo ""
    echo "7. Then uncomment GPU config in docker-compose.yml and restart:"
    echo "   docker compose down && docker compose up -d"
    echo ""
else
    # Native Linux - restart docker service
    echo ""
    if sudo systemctl restart docker 2>/dev/null; then
        echo -e "${GREEN}✓ Docker daemon restarted${NC}"
    else
        echo -e "${YELLOW}⚠️  Could not restart Docker. Please restart manually.${NC}"
    fi
    
    echo ""
    echo -e "${GREEN}✓ NVIDIA Container Toolkit installed successfully!${NC}"
    echo ""
    echo "═══════════════════════════════════════════════════════════════════════"
    echo "   NEXT STEPS:"
    echo "═══════════════════════════════════════════════════════════════════════"
    echo ""
    echo "1. Uncomment GPU config in docker-compose.yml"
    echo "2. Restart containers: docker compose down && docker compose up -d"
    echo "3. Verify: docker exec ollama-server nvidia-smi"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
