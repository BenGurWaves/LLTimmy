# LLTimmy -- Local AGI Agent

A fully local AI agent system with a supervisor (Doctor), multi-layer memory, self-evolution, calendar, and a Quiet Luxury dark UI built with CustomTkinter.

## Quick Start

```bash
# Start Timmy (native macOS app)
./run_tim.sh

# Start Doctor (supervisor)
./run_doctor.sh

# Build /Applications/LLTimmy.app
./build_app.sh
```

## Architecture

```
User --> LLTimmy.app (CustomTkinter) --> Agent Core --> Ollama (local LLM)
         Doctor (watchdog daemon)    --> Auto-restart, idle evolution, resource monitor
```

### Core Files

| File | Purpose |
|------|---------|
| `src/app.py` | Native CustomTkinter UI -- Quiet Luxury dark theme, See Reasoning toggle, model selector |
| `agent_core.py` | ReAct loop, streaming, interrupt/queue, calendar tool, auto-verify, task auto-completion |
| `doctor.py` | Watchdog, pure-code updater, LLM boost, idle evolution, resource monitor |
| `memory_manager.py` | Conscious + Subconscious (ChromaDB) + Graph + Long-term, unified search |
| `tools.py` | 19 tools with context-aware risk engine, smart app opening, screenshots |
| `self_evolution.py` | Daily reviews, capability tracking, idle-time research |
| `scheduler.py` | Internal calendar/cron with flexible date parsing, recurring events |
| `task_manager.py` | Persistent tasks with urgency, scheduling modes, progress tracking |
| `config.json` | All configuration keys |

## Features

- **19 Tools**: terminal, file I/O, web search, browser, Blender, AppleScript, GitHub, ComfyUI, model management, notifications, service status, calendar, screenshot capture
- **ReAct Loop**: up to 15 reasoning steps with tool execution
- **See Reasoning**: toggle to show/hide agent thought process
- **Transparency Engine**: honest failure reporting with raw proof
- **4-Layer Memory**: Conscious (daily JSON), Subconscious (ChromaDB vectors), Long-term (compressed summaries), Graph (entity-relationship)
- **Doctor Watchdog**: auto-restart in <3s, idle evolution, resource monitoring
- **Calendar/Scheduler**: flexible date parsing, recurring events, macOS notifications
- **Task Management**: urgency levels, progress tracking, auto-completion

## Configuration

Key settings in `config.json`:

```json
{
  "primary_model": "ollama/qwen3:30b",
  "doctor_llm_enabled": true,
  "self_evolution": { "idle_research": true },
  "calendar": { "enabled": true },
  "notifications": { "enabled": true }
}
```

## Dependencies

```
pip install -r requirements.txt
```

Requires: Ollama running locally with at least one model pulled.
