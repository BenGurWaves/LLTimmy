# LLTimmy Project Log

## [2026-02-26] Initial Setup

**What**: Created LLTimmy project structure and initial configuration
**How**:
- Created directory structure for memory, updates, projects, logs
- Configured requirements.txt with all necessary dependencies
- Set up config.json with default models and settings
- Created ben_profile.json user profile template
- Initialized log files (tim_audit.log, doctor_actions.log)

**Status**: Complete

---

## [2026-02-26] Core Implementation

**What**: Implemented all core components — agent, UI, doctor, memory, tools
**How**:

### agent_core.py
- ReAct loop (Thought → Action → Observation) with up to 10 iterations
- Streams tokens from Ollama `/api/chat` endpoint
- Parses tool calls from model output, executes, feeds observations back
- Maintains conversation history (last 20 messages)
- Integrates subconscious memory for context retrieval
- Model switching at runtime via `set_model()`

### main.py — Gradio UI (port 7860)
- Dark theme: #111111 bg, #eeeeee text, #D2B48C accent buttons
- Chatbot with markdown rendering and streaming
- File drag-and-drop upload
- Model dropdown refreshed from `ollama list` every 30s
- Doctor + Timmy status indicators (auto-refresh)
- Midnight rollover: summarise day → archive → clear chat
- Writes PID to /tmp/timmy.pid for Doctor monitoring

### doctor.py — Watchdog (port 7861)
- Monitors /tmp/timmy.pid, auto-restarts within 3s on crash
- Applies updates from /updates/ folder (copies .py/.json → base dir, restarts)
- Model switch via file signal (/tmp/timmy_status.json)
- Separate Gradio chat UI with commands: status, restart, stop, start, switch, logs
- Logs only to doctor_actions.log

### memory_manager.py
- Conscious: daily JSON chat files in raw_chats/
- Subconscious: ChromaDB + Ollama nomic-embed-text embeddings (graceful fallback)
- Long-term: compressed daily summaries in compressed/, gzip archives in archive/
- Auto-archive after 30 days

### tools.py
- terminal_command with RiskEngine (low/medium/high + banned paths)
- web_search via duckduckgo-search library with SourceEvaluator (confidence + bullshit flag)
- playwright_browser for page text extraction
- download_url, extract_zip, run_blender, run_applescript
- run_comfyui_workflow, open_application, github_operations
- da_vinci_resolve_script (stub — dynamic import)
- All calls logged to tim_audit.log

### Shell scripts
- run_tim.sh, run_doctor.sh, open_doctor_ui.sh (all chmod +x)

### requirements.txt
- Fixed: removed invalid nomic-embed-text pip package
- Added: duckduckgo-search, psutil, beautifulsoup4

**Status**: Complete — ready for dependency install and first run

---

## Next Steps

- Install dependencies: `pip install -r requirements.txt`
- Install Playwright browsers: `playwright install chromium`
- Pull Ollama models: `ollama pull qwen3:30b && ollama pull nomic-embed-text`
- First run: Terminal 1 → `./run_doctor.sh` | Terminal 2 → `./run_tim.sh`
- Open http://127.0.0.1:7860

## [2026-02-26 18:01:18] Timmy Started
PID: 696

## [2026-02-26 18:28:45] Timmy Started
PID: 1604

## [2026-02-26 18:28:52] Timmy Started
PID: 1621

## [2026-02-26 18:29:01] Timmy Started
PID: 1646

## [2026-02-26 18:29:10] Timmy Started
PID: 1647

## [2026-02-26 18:29:17] Timmy Started
PID: 1650

## [2026-02-26 18:29:26] Timmy Started
PID: 1653

## [2026-02-26 18:29:35] Timmy Started
PID: 1654

## [2026-02-26 18:29:44] Timmy Started
PID: 1658

## [2026-02-26 18:29:53] Timmy Started
PID: 1659

## [2026-02-26 18:30:02] Timmy Started
PID: 1664

## [2026-02-27 08:14:34] Timmy Started
PID: 5542

## [2026-02-27 08:14:43] Timmy Started
PID: 5550

## [2026-02-27 08:14:52] Timmy Started
PID: 5557

## [2026-02-27 08:15:01] Timmy Started
PID: 5558

## [2026-02-27 08:15:10] Timmy Started
PID: 5564

## [2026-02-27 08:15:19] Timmy Started
PID: 5565

## [2026-02-27 08:15:28] Timmy Started
PID: 5566

## [2026-02-27 08:15:35] Timmy Started
PID: 5569

## [2026-02-27 08:15:44] Timmy Started
PID: 5570

## [2026-02-27 08:15:53] Timmy Started
PID: 5572

## [2026-02-27 08:38:56] Timmy Started
PID: 5819

## [2026-02-27 08:39:05] Timmy Started
PID: 5826

## [2026-02-27 08:39:14] Timmy Started
PID: 5828

## [2026-02-27 08:39:23] Timmy Started
PID: 5831

## [2026-02-27 08:39:32] Timmy Started
PID: 5867

## [2026-02-27 08:39:41] Timmy Started
PID: 5877

## [2026-02-27 08:39:50] Timmy Started
PID: 5897

## [2026-02-27 08:39:57] Timmy Started
PID: 5915

## [2026-02-27 08:40:06] Timmy Started
PID: 5920

## [2026-02-27 08:40:15] Timmy Started
PID: 5931

## [2026-02-27 08:41:04] Timmy Started
PID: 6008

## [2026-02-27 08:41:13] Timmy Started
PID: 6018

## [2026-02-27 08:41:22] Timmy Started
PID: 6025

## [2026-02-27 08:41:31] Timmy Started
PID: 6035

## [2026-02-27 08:41:40] Timmy Started
PID: 6042

## [2026-02-27 08:41:49] Timmy Started
PID: 6046

## [2026-02-27 08:41:58] Timmy Started
PID: 6053

## [2026-02-27 08:42:05] Timmy Started
PID: 6064

## [2026-02-27 08:42:15] Timmy Started
PID: 6070

## [2026-02-27 08:42:24] Timmy Started
PID: 6079

## [2026-02-27 08:59:48] Timmy Started
PID: 6535

## [2026-02-27 08:59:57] Timmy Started
PID: 6539

## [2026-02-27 09:00:06] Timmy Started
PID: 6553

## [2026-02-27 09:00:15] Timmy Started
PID: 6555

## [2026-02-27 09:00:24] Timmy Started
PID: 6558

## [2026-02-27 09:00:33] Timmy Started
PID: 6561

## [2026-02-27 09:00:42] Timmy Started
PID: 6562

## [2026-02-27 09:00:49] Timmy Started
PID: 6569

## [2026-02-27 09:00:58] Timmy Started
PID: 6574

## [2026-02-27 09:01:07] Timmy Started
PID: 6591

## [2026-02-27 09:10:42] Timmy Started
PID: 6963

## [2026-02-27 09:10:51] Timmy Started
PID: 6988

## [2026-02-27 09:11:00] Timmy Started
PID: 6989

## [2026-02-27 09:11:09] Timmy Started
PID: 6990

## [2026-02-27 09:11:18] Timmy Started
PID: 6999

## [2026-02-27 09:11:27] Timmy Started
PID: 7007

## [2026-02-27 09:11:36] Timmy Started
PID: 7018

## [2026-02-27 09:11:43] Timmy Started
PID: 7025

## [2026-02-27 09:11:52] Timmy Started
PID: 7036

## [2026-02-27 09:12:01] Timmy Started
PID: 7045

## [2026-02-27 09:26:26] Timmy Started
PID: 7462

## [2026-02-27 09:26:35] Timmy Started
PID: 7481

## [2026-02-27 09:26:44] Timmy Started
PID: 7482

## [2026-02-27 09:26:53] Timmy Started
PID: 7483

## [2026-02-27 09:27:02] Timmy Started
PID: 7488

## [2026-02-27 09:27:11] Timmy Started
PID: 7497

## [2026-02-27 09:27:20] Timmy Started
PID: 7513

## [2026-02-27 09:27:27] Timmy Started
PID: 7520

## [2026-02-27 09:27:36] Timmy Started
PID: 7521

## [2026-02-27 09:27:45] Timmy Started
PID: 7529

## [2026-02-27 09:37:36] Timmy Started
PID: 7901

## [2026-02-27 09:37:45] Timmy Started
PID: 7908

## [2026-02-27 09:37:54] Timmy Started
PID: 7909

## [2026-02-27 09:38:03] Timmy Started
PID: 7910

## [2026-02-27 09:38:12] Timmy Started
PID: 7913

## [2026-02-27 09:38:21] Timmy Started
PID: 7915

## [2026-02-27 09:38:30] Timmy Started
PID: 7916

## [2026-02-27 09:38:37] Timmy Started
PID: 7921

## [2026-02-27 09:38:46] Timmy Started
PID: 7922

## [2026-02-27 09:38:55] Timmy Started
PID: 7923

## [2026-02-27 09:49:28] Timmy Started
PID: 8241

## [2026-02-27 09:49:37] Timmy Started
PID: 8246

## [2026-02-27 09:49:46] Timmy Started
PID: 8250

## [2026-02-27 09:49:55] Timmy Started
PID: 8260

## [2026-02-27 09:50:04] Timmy Started
PID: 8265

## [2026-02-27 09:50:13] Timmy Started
PID: 8273

## [2026-02-27 09:50:22] Timmy Started
PID: 8276

## [2026-02-27 09:50:29] Timmy Started
PID: 8283

## [2026-02-27 09:50:38] Timmy Started
PID: 8291

## [2026-02-27 09:50:47] Timmy Started
PID: 8293

## [2026-02-27 09:55:16] Timmy Started
PID: 8441

## [2026-02-27 09:55:25] Timmy Started
PID: 8446

## [2026-02-27 09:55:34] Timmy Started
PID: 8447

## [2026-02-27 09:55:43] Timmy Started
PID: 8454

## [2026-02-27 09:55:52] Timmy Started
PID: 8462

## [2026-02-27 09:56:01] Timmy Started
PID: 8467

## [2026-02-27 09:56:10] Timmy Started
PID: 8469

## [2026-02-27 09:56:17] Timmy Started
PID: 8473

## [2026-02-27 09:56:26] Timmy Started
PID: 8474

## [2026-02-27 09:56:35] Timmy Started
PID: 8475

## [2026-02-27 10:17:15] Timmy Started
PID: 8944

## [2026-02-27 10:17:24] Timmy Started
PID: 8953

## [2026-02-27 10:17:33] Timmy Started
PID: 8962

## [2026-02-27 10:17:42] Timmy Started
PID: 8978

## [2026-02-27 10:17:51] Timmy Started
PID: 8996

## [2026-02-27 10:18:00] Timmy Started
PID: 8998

## [2026-02-27 10:18:09] Timmy Started
PID: 9003

## [2026-02-27 10:18:16] Timmy Started
PID: 9006

## [2026-02-27 10:18:25] Timmy Started
PID: 9007

## [2026-02-27 10:18:34] Timmy Started
PID: 9008

## [2026-02-27 10:21:29] Timmy Started
PID: 9103

## [2026-02-27 10:21:38] Timmy Started
PID: 9110

## [2026-02-27 10:21:47] Timmy Started
PID: 9116

## [2026-02-27 10:21:56] Timmy Started
PID: 9121

## [2026-02-27 10:22:05] Timmy Started
PID: 9133

## [2026-02-27 10:22:14] Timmy Started
PID: 9138

## [2026-02-27 10:22:23] Timmy Started
PID: 9142

## [2026-02-27 10:22:30] Timmy Started
PID: 9145

## [2026-02-27 10:22:49] Timmy Started
PID: 9183

## [2026-02-27 10:40:47] Timmy Started
PID: 9808

## [2026-02-27 10:40:59] Timmy Started
PID: 9823

## [2026-02-27 10:41:08] Timmy Started
PID: 9828

## [2026-02-27 10:41:17] Timmy Started
PID: 9835

## [2026-02-27 10:41:26] Timmy Started
PID: 9841

## [2026-02-27 10:41:35] Timmy Started
PID: 9849

## [2026-02-27 10:41:44] Timmy Started
PID: 9852

## [2026-02-27 10:41:53] Timmy Started
PID: 9856

## [2026-02-27 10:42:02] Timmy Started
PID: 9864

## [2026-02-27 10:42:11] Timmy Started
PID: 9869

## [2026-02-27 12:00:31] Timmy Started
PID: 11015

## [2026-02-27 12:00:48] Timmy Started
PID: 11032

## [2026-02-27 12:01:03] Timmy Started
PID: 11040

## [2026-02-27 12:01:20] Timmy Started
PID: 11043

## [2026-02-27 12:01:35] Timmy Started
PID: 11048

## [2026-02-27 12:01:49] Timmy Started
PID: 11076

## [2026-02-27 12:02:06] Timmy Started
PID: 11091

## [2026-02-27 12:02:21] Timmy Started
PID: 11094

## [2026-02-27 12:02:38] Timmy Started
PID: 11096

## [2026-02-27 12:02:53] Timmy Started
PID: 11101

## [2026-02-27 12:03:10] Timmy Started
PID: 11106

## [2026-02-27 12:03:25] Timmy Started
PID: 11110

## [2026-02-27 12:03:42] Timmy Started
PID: 11111

## [2026-02-27 12:03:57] Timmy Started
PID: 11116

## [2026-02-27 12:04:14] Timmy Started
PID: 11117

## [2026-02-27 12:36:54] Timmy Started
PID: 14849

## [2026-02-27 12:56:45] Timmy Started
PID: 15248

## [2026-02-27 12:58:50] Timmy Started
PID: 15285

## [2026-02-27 12:59:05] Timmy Started
PID: 15291

## [2026-02-27 12:59:20] Timmy Started
PID: 15295

## [2026-02-27 13:14:59] Timmy Started
PID: 15504

## [2026-02-27 13:15:16] Timmy Started
PID: 15514

## [2026-02-27 13:15:31] Timmy Started
PID: 15551

## [2026-02-27 13:15:48] Timmy Started
PID: 15568

## [2026-02-27 13:16:03] Timmy Started
PID: 15591

## [2026-02-27 13:16:58] Timmy Started
PID: 15629

## [2026-02-27 13:17:15] Timmy Started
PID: 15644

## [2026-02-27 13:17:30] Timmy Started
PID: 15653

## [2026-02-27 13:26:59] Timmy Started
PID: 15915

## [2026-02-27 13:43:41] Timmy Started
PID: 16948

## [2026-02-27 13:53:22] Timmy Started
PID: 17431

## [2026-02-27 14:10:53] Timmy Started
PID: 17984

## [2026-02-27 14:11:18] Timmy Started
PID: 18034

## [2026-02-27 14:17:49] Timmy Started
PID: 18338

## [2026-02-27 14:18:06] Timmy Started
PID: 18378
