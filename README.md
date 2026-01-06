# Agentic IA Architecture

A multi-agent AI system for software development, implementing the architecture defined in `archi.puml`.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      VSCODE INTERFACE                            │
│  ┌──────────────┐                    ┌──────────────┐           │
│  │  PO/PM Input │                    │User Feedback │           │
│  └──────┬───────┘                    └──────▲───────┘           │
└─────────┼───────────────────────────────────┼───────────────────┘
          │                                   │
┌─────────▼───────────────────────────────────┼───────────────────┐
│              PROMPT ANALYSER & TASK PROVIDER                     │
│  ┌────────────────┐  ┌──────────────┐  ┌───────────────┐        │
│  │Task Formulator │◄─│Rule Enforcer │  │Result         │        │
│  │(NL -> Tasks)   │  │              │  │Interpreter    │        │
│  └───────┬────────┘  └──────────────┘  └───────▲───────┘        │
│          │                                      │                │
│          ▼                                      │                │
│  ┌────────────────┐                            │                │
│  │Task Scheduler  │────────────────────────────┘                │
│  └───────┬────────┘                                             │
└──────────┼──────────────────────────────────────────────────────┘
           │
     ┌─────▼─────┐
     │   KAFKA   │
     └─────┬─────┘
           │
┌──────────▼──────────────────────────────────────────────────────┐
│                        MESSAGE BUS                               │
└──────────┬──────────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────┐
│                      AGENT WORKFORCE                             │
│  ┌────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐ ┌─────────────┐│
│  │ CI/CD  │ │Code      │ │Code    │ │Architect│ │Tester       ││
│  │        │ │Quality   │ │Writer  │ │         │ │WhiteBox/    ││
│  │        │ │Analyser  │ │        │ │         │ │BlackBox     ││
│  └────┬───┘ └────┬─────┘ └───┬────┘ └────┬────┘ └──────┬──────┘│
└───────┼──────────┼───────────┼───────────┼─────────────┼────────┘
        │          │           │           │             │
┌───────▼──────────▼───────────▼───────────▼─────────────▼────────┐
│                      OS / VSCODE API                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │  R Access    │  │  W Access    │  │  Cmd Access  │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### Prompt Analyser & Task Provider
- **Task Formulator**: Transforms natural language input into structured tasks (PDL)
- **Rule Enforcer**: Validates tasks against business and technical rules
- **Task Scheduler**: Manages task queue with priority and dependency handling
- **Result Interpreter**: Interprets agent results and generates follow-up tasks

### Agent Workforce
- **Code Writer**: Writes and modifies code (R/W access)
- **Architect**: Designs system architecture (R access)
- **Code Quality Analyser**: Analyzes code quality (R access)
- **Tester WhiteBox**: Creates unit/integration tests (R/Cmd access)
- **Tester BlackBox**: Creates E2E/API tests (Cmd access)
- **CI/CD**: Manages build and deployment pipelines (Cmd access)

### Infrastructure
- **Kafka**: Message queue for task distribution
- **Message Bus**: Routes messages between components
- **Llama**: Local LLM for agent intelligence

## Tech Stack

- **Language**: Python 3.11+
- **Deployment**: Docker Compose
- **Model**: Llama (local)
- **Principles**: SOLID

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Python 3.11+ (for local development)
- NVIDIA GPU (optional, for Llama acceleration)

### Setup

1. **Clone and setup environment**
```bash
./Devtools/init.sh
```

2. **Configure environment**
```bash
cp .env.example .env
# Edit .env with your settings
```

3. **Download Llama model**
```bash
mkdir -p models
# Download a GGUF model and place it in models/llama-model.gguf
```

4. **Start the system**
```bash
docker-compose up -d
```

5. **Access the API**
```bash
# Submit a task
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"input": "Create a REST API for user management"}'

# Check task status
curl http://localhost:8000/tasks

# Get feedback
curl http://localhost:8000/feedback
```

## Development

### Local Development
```bash
# Activate virtual environment
source .venv/bin/activate

# Start infrastructure only
docker-compose up -d zookeeper kafka llama

# Run services locally
python -m src.api.main  # API server
python -m src.services.prompt_analyser.main  # Prompt analyser
python -m src.agents.code_writer.main  # Code writer agent
# ... etc
```

### Running Tests
```bash
pytest tests/ -v
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/tasks` | POST | Submit a new task |
| `/tasks` | GET | Get task queue status |
| `/tasks/{id}` | GET | Get specific task |
| `/feedback` | GET | Poll for feedback |
| `/ws/feedback` | WS | Real-time feedback |

## Project Structure

```
src/
├── core/               # Core interfaces and base classes
│   ├── interfaces.py   # Abstract interfaces (SOLID ISP)
│   ├── base.py        # Base implementations
│   └── config.py      # Configuration management
├── infrastructure/     # Infrastructure implementations
│   ├── kafka.py       # Kafka producer/consumer
│   ├── message_bus.py # Message bus routing
│   ├── llm_client.py  # Llama client
│   └── os_access.py   # File/command access
├── services/          # Services
│   └── prompt_analyser/
│       ├── task_formulator.py
│       ├── rule_enforcer.py
│       ├── task_scheduler.py
│       └── result_interpreter.py
├── agents/            # Agent implementations
│   ├── code_writer.py
│   ├── architect.py
│   ├── code_quality.py
│   ├── tester_whitebox.py
│   ├── tester_blackbox.py
│   └── cicd.py
├── api/              # REST API
│   └── main.py
└── main.py           # Application entry point
```

## License

MIT License


```bash

# Start a project - system will work until completion
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"input": "Create a Python web scraper that extracts article titles from news websites"}'

# Check status
curl http://localhost:8000/projects/{project_id}

# Interrupt if needed
curl -X POST http://localhost:8000/projects/{project_id}/interrupt

```