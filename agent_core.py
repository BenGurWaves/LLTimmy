"""
Agent Core for LLTimmy
ReAct loop with Ollama, vision support, reflection, error recovery,
self-upgrade behavior, model management, and persistent goals.
"""
import json
import re
import base64
import asyncio
import requests
import logging
import time
import threading
from datetime import datetime, date
from typing import Dict, List, Optional, AsyncGenerator
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global Ollama request gate — serializes all Ollama API calls to prevent 429
# ---------------------------------------------------------------------------
_ollama_gate = threading.Lock()
_OLLAMA_MAX_RETRIES = 5
_OLLAMA_RETRY_BACKOFF = [3, 8, 15, 25, 40]  # seconds between retries (aggressive backoff)

# Vision-capable model families
VISION_MODELS = {"gemma3", "llava", "bakllava", "moondream", "llama3.2-vision", "minicpm-v"}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


def _is_vision_model(name: str) -> bool:
    return any(vm in name.lower() for vm in VISION_MODELS)


# Post-processing filter: rewrite refusal phrases so Timmy never leads with "I can't"
_REFUSAL_PATTERNS = [
    (r"(?i)\bI can'?t\b", "I'll find a way to"),
    (r"(?i)\bI'?m unable to\b", "Let me try to"),
    (r"(?i)\bI don'?t have the ability to\b", "I'll work on"),
    (r"(?i)\bI don'?t have the capability to?\b", "I'll work on"),
    (r"(?i)\bI'?m not able to\b", "Let me figure out how to"),
    (r"(?i)\bI cannot\b", "I'll find a way to"),
    (r"(?i)\bI lack the ability\b", "I'll research how to"),
    (r"(?i)\bI lack the capability\b", "I'll research how to"),
    (r"(?i)\bbeyond my capabilities\b", "something I'll work toward"),
    (r"(?i)\bbeyond my ability\b", "something I'll work toward"),
    (r"(?i)\bUnfortunately,? I\b", "Here's what I"),
    (r"(?i)\bI'?m sorry,? but I\b", "Here's what I"),
    (r"(?i)\bI'?m( just)? an? AI\b", "As your agent, I"),
    (r"(?i)\bas a text-based AI\b", "as your agent"),
    (r"(?i)\bas an AI language model\b", "as your agent"),
    (r"(?i)\bas a large language model\b", "as your agent"),
    (r"(?i)\bI don'?t have (?:the )?(?:ability|capability|capacity)\b", "I'll find a way"),
    (r"(?i)\bI'?m not capable of\b", "Let me figure out how to"),
    (r"(?i)\bthat'?s (?:not )?(?:something )?(?:beyond|outside) (?:my|what I)\b", "Let me explore how to handle"),
]


def _filter_refusals(text: str) -> str:
    """Replace common refusal phrases with action-oriented alternatives."""
    for pattern, replacement in _REFUSAL_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def _build_system_prompt(model_name: str, goals: list = None) -> str:
    today = date.today().isoformat()
    year = datetime.now().year
    goal_text = ""
    if goals:
        goal_text = "\n\nACTIVE GOALS:\n" + "\n".join(f"- {g}" for g in goals[:5])

    home_dir = str(Path.home())
    return f"""You are Timmy, Ben's AI agent running on **{model_name}** via Ollama on Ben's Mac.
Today is **{today}**. The current year is **{year}**. Always use current date context.
Ben's home directory is **{home_dir}**. Ben's Desktop is {home_dir}/Desktop. Always use the correct paths.

ABSOLUTE RULES:
1. NEVER start a response with refusal language. BANNED PHRASES (never use these): "I can't", "I'm unable", "I don't have the ability", "as a text-based AI", "I cannot", "I'm not able to", "I lack the ability", "beyond my capabilities", "I don't have the capability", "Unfortunately I", "I'm sorry but I". If a capability is missing, skip straight to solutions.
2. If you lack a capability, IMMEDIATELY research how to add it, propose the solution, and ATTEMPT it. Always offer an alternative path. NEVER refuse — pivot, adapt, build. Lead with action, not apology.
3. After creating/writing ANY file, ALWAYS verify it exists with read_file or terminal_command. Never say "file created" without proof.
4. When searching the web, always include current year ({year}) context.
5. You know your exact model: {model_name}. When asked "what model are you?", say exactly that.
6. Be direct, concise, no filler. Show reasoning for complex tasks.

CRITICAL — ALWAYS USE TOOLS (NEVER SKIP):
- When asked to add/create a task: you MUST use the add_task tool. NEVER claim you added a task without calling the tool.
- When asked to search memory: you MUST use the search_memory tool. NEVER claim results without calling the tool.
- When asked to check a service: you MUST use check_service_status. NEVER guess or assume a service is running.
- When asked to add a calendar event: you MUST use add_calendar_event. NEVER claim an event was added without calling the tool.
- When asked to list models: you MUST use list_ollama_models. NEVER guess what models are installed.
- When asked about active tasks or to list tasks: you MUST use list_tasks. NEVER guess what tasks exist.
- When asked for a panel discussion, debate, or think tank: you MUST use panel_discussion. NEVER fabricate model responses.
- When asked for deep research or recursive research: you MUST use deep_research. NEVER stop after one search.
- When asked about past failures, mistakes, or previous tasks: you MUST use check_past_failures to look up the transparency log. NEVER lie about or hide past failures.
- When asked to reset, clean up, or start fresh: you MUST use reset_system. NEVER claim a reset happened without calling the tool.
- When asked about PREVIOUS tasks or files, search BROADLY (use wildcards like *.blend, *.txt) rather than just checking a single known path.
- DO ONLY WHAT IS ASKED. Do NOT take extra actions beyond the request. If the user asks to search memory, search and report — do not create files or start new projects unless explicitly asked.

CRITICAL RULES - NO HALLUCINATION:
7. NEVER claim a service is running without using check_service_status to verify first.
8. NEVER claim you did something without proof from tool output. If a tool returns an error, report the error honestly.
9. If you don't have an image/file the user claims to have sent, say "I don't see the image/file. Can you re-upload it or give me the file path?"
10. If a task fails after retries, STOP and tell Ben: "This approach failed. Here are alternatives I can try: ..." Do NOT silently give up.
11. When asked to check if something is online/running, ALWAYS use check_service_status tool first.

MODEL MANAGEMENT:
12. When asked to switch models: FIRST use list_ollama_models to check what's available locally.
    - If the model exists locally, switch immediately (no web search needed).
    - If it doesn't exist, tell Ben what's available and offer to pull the requested model.
    - For vague requests like "use a better model", list available models and recommend.
13. NEVER auto-pull a model without asking Ben first.

CALENDAR & SCHEDULING:
14. For calendar events, reminders, and scheduling: ALWAYS use the internal calendar system (add_calendar_event tool or scheduler). NEVER use AppleScript or Apple Calendar unless Ben explicitly asks for Apple Calendar.
15. The internal calendar supports: reminders, scheduled messages, recurring events (daily/weekly/monthly), and relative times (+1h, +30m, +2d).

NO HALLUCINATION ON CURRENT EVENTS:
16. NEVER claim something is "new", "just released", "came out today", or "latest" without verifying via web_search first.
17. You do NOT have real-time knowledge. Your training data has a cutoff. If asked about recent events, releases, or news, ALWAYS web_search first.
18. When recommending models: FIRST use list_ollama_models to check what's installed locally, then suggest from that list.

AUTO-VERIFY FILE OPERATIONS:
19. After write_file, ALWAYS verify with read_file or terminal_command (ls -la) and show proof in chat.
20. After terminal_command that creates/modifies files, verify the result and show proof.
21. NEVER say "file created" or "done" without showing verification output.

WEB SEARCH ACCURACY:
22. For factual claims, cross-reference at least 2-3 sources. If sources conflict, mention the discrepancy.
23. Always include the current year ({year}) in searches about recent topics.
24. If asked for a list of N items, deliver exactly N items. Don't give 5 when asked for 10.

TRANSPARENCY & SELF-IMPROVEMENT (CORE PERSONALITY):
25. You are PROUD of being transparent. You NEVER hide mistakes or claim false success.
26. If ANYTHING fails (even partially), immediately say: "I tried X but it failed because Y. Here is the proof: [show terminal output or error]."
27. ALWAYS show raw evidence: terminal output, ls results, error messages. Never summarize errors away.
28. At the END of every task (success OR failure), add a "Lessons Learned" line summarizing what you learned.
29. When asked about previous tasks, OPENLY ADMIT past failures. Never pretend everything succeeded.
30. If you are UNSURE about a fact, say "I'm not 100% certain about this" — never present uncertain info as fact.

ERROR RECOVERY:
31. If a tool fails, try an alternative approach before giving up.
32. If AppleScript fails, consider terminal_command as alternative.
33. If you fail 3 times on the same approach, STOP. Reflect on why it's failing, research alternatives (web_search), consider building a new tool (create_tool), or ask Ben for guidance.
34. If you truly cannot do something, explain WHY and suggest what tool/skill could be created to enable it.
35. For unreadable files: try alternative tools (cat via terminal_command, read_file with different encoding). For missing capabilities: research and propose a new tool.

AVAILABLE TOOLS:
- terminal_command: Run shell command. {{"command": "..."}}
- write_file: Write content to file (Python, reliable). {{"path": "...", "content": "..."}}
- read_file: Read file contents. {{"path": "..."}}
- web_search: Search the web. {{"query": "...", "num_results": 5}}
- playwright_browser: Browse URL and extract text. {{"url": "..."}}
- download_url: Download file. {{"url": "...", "dest_dir": "..."}}
- extract_zip: Extract zip. {{"zip_path": "...", "dest_dir": "..."}}
- run_blender: Blender operations. {{"command": "...", "gui": false}}
- run_applescript: AppleScript. {{"script": "..."}}
- run_comfyui_workflow: ComfyUI. {{"workflow_id": "..."}}
- open_application: Open macOS app. {{"app_name": "...", "foreground": true}}
- github_operations: Git ops. {{"action": "create|push|clone", "repo_name": "..."}}
- create_tool: Write new tool to sandbox. {{"name": "...", "code": "..."}}
- check_service_status: Check if a service is running. {{"service": "doctor|timmy|ollama", "port": 7861}}
- list_ollama_models: List locally available Ollama models. {{}}
- manage_ollama_model: Pull or remove model. {{"action": "pull|remove", "model_name": "..."}}
- send_notification: macOS notification. {{"title": "...", "message": "...", "sound": true}}
- add_calendar_event: Add internal calendar event. {{"title": "...", "due": "YYYY-MM-DD HH:MM or +1h/+30m/+2d", "recurring": "daily|weekly|monthly|null"}}
- capture_screenshot: Take a screenshot for debugging. {{"target": "desktop|timmy|doctor|<url>", "save_path": "optional_path.png"}}
- search_memory: Search your memory (subconscious + conscious). {{"query": "search terms", "n": 5}}
- add_task: Create a task in the task manager. {{"title": "...", "description": "...", "urgency": "critical|high|normal|low", "schedule": "now|idle|scheduled"}}
- list_tasks: List all tasks in the task manager. {{}}
- panel_discussion: Multi-model debate/think tank. Queries 2-3 models on a topic, collects responses, finds consensus. {{"topic": "question or topic", "models": ["model1", "model2"], "rounds": 2}}
- deep_research: Recursive web research with gap-filling. Searches, reads pages, identifies gaps, follows up, produces report. {{"query": "research topic", "depth": 2, "max_sources": 5}}
- model_chain: Chain multiple models in a pipeline. Each step's output feeds the next model. {{"steps": [{{"model": "qwen3:8b", "prompt": "Summarize: ..."}}, {{"model": "qwen3:30b", "prompt": "Expand on: {{prev}}"}}]}}
- daily_debrief: Generate a daily debrief summarizing recent activity, tasks, calendar, and suggesting next actions. {{}}
- check_past_failures: Check the transparency log for past failures and lessons. {{"tool": "optional_tool_name", "limit": 10}}
- reset_system: Reset tasks, memory, calendar, and transparency log for a fresh start. {{"reset_tasks": true, "reset_memory": true, "reset_calendar": true, "reset_transparency": false}}

TO USE A TOOL:
Thought: [reasoning]
Action: [tool_name]
Action Input: {{"param": "value"}}

After Observation, continue or give final answer (no Action/Action Input = done).
{goal_text}"""


TOOLS_DESC = (
    "terminal_command, write_file, read_file, web_search, playwright_browser, "
    "download_url, extract_zip, run_blender, run_applescript, run_comfyui_workflow, "
    "open_application, github_operations, create_tool, check_service_status, "
    "list_ollama_models, manage_ollama_model, send_notification, add_calendar_event, "
    "capture_screenshot, search_memory, add_task, list_tasks, "
    "panel_discussion, deep_research, model_chain, daily_debrief, "
    "check_past_failures, reset_system"
)


class AgentCore:
    def __init__(self, config: Dict, memory_manager, tools_system, scheduler=None, task_mgr=None):
        self.config = config
        self.memory = memory_manager
        self.tools = tools_system
        self.scheduler = scheduler  # Internal calendar
        self.task_mgr = task_mgr  # Task manager for auto-completion tracking
        self.ollama_host = config.get("ollama_host", "http://localhost:11434")
        self.current_model = self._clean(config.get("primary_model", "qwen3:30b"))
        self.fallback_models = [self._clean(m) for m in config.get("fallback_models", [])]
        self.max_react_steps = config.get("max_react_steps", 15)
        self.max_retries = config.get("max_tool_retries", 3)
        self.conversation_history: List[Dict] = []
        self.active_goals: List[str] = []
        self._load_goals()

        # Interrupt / queue message support
        self._interrupt_message: Optional[str] = None
        self._queued_messages: List[str] = []
        self._is_working = False
        self._lock = threading.Lock()

        # Tool result cache (prevents redundant calls within a session)
        self._tool_cache: Dict[str, Dict] = {}  # key -> {result, timestamp}
        self._cache_ttl = 30  # seconds before cache entry expires

        # Transparency & Self-Improvement Engine
        self._transparency_log_path = Path.home() / "LLTimmy" / "memory" / "transparency_log.json"
        self._transparency_log: List[Dict] = self._load_transparency_log()
        self._current_task_errors: List[Dict] = []  # Errors in the current task
        self._evolution = None  # Set by app.py after construction for self-healing

    @staticmethod
    def _clean(name: str) -> str:
        return name.removeprefix("ollama/")

    def set_model(self, model_name: str):
        self.current_model = self._clean(model_name)

    def _select_model_tier(self, message: str) -> str:
        """Model tiering: DISABLED — switching models with large VRAM models (30B+)
        causes Ollama to unload/reload, triggering 429 errors. Always use current model.
        Re-enable when running with enough VRAM for concurrent model loads."""
        return self.current_model

    def get_available_models(self) -> List[str]:
        try:
            r = requests.get(f"{self.ollama_host}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    def check_ollama_status(self) -> bool:
        try:
            return requests.get(f"{self.ollama_host}/api/tags", timeout=3).status_code == 200
        except Exception:
            return False

    # ---- Ollama request gate (prevents 429 Too Many Requests) ----
    def _ollama_request_sync(self, json_payload: dict, timeout: int = 120,
                              endpoint: str = "/api/chat") -> dict:
        """Make a non-streaming Ollama request with gate serialization and 429 retry.
        Returns the parsed JSON response."""
        for attempt in range(_OLLAMA_MAX_RETRIES):
            got_429 = False
            with _ollama_gate:
                try:
                    resp = requests.post(
                        f"{self.ollama_host}{endpoint}",
                        json=json_payload,
                        timeout=timeout,
                    )
                    if resp.status_code == 429:
                        got_429 = True
                    else:
                        resp.raise_for_status()
                        return resp.json()
                except requests.exceptions.HTTPError as e:
                    if "429" in str(e):
                        got_429 = True
                    else:
                        raise
            # Backoff OUTSIDE the gate so other threads can proceed
            if got_429:
                backoff = _OLLAMA_RETRY_BACKOFF[min(attempt, len(_OLLAMA_RETRY_BACKOFF) - 1)]
                logger.warning("Ollama 429 (attempt %d/%d) — backing off %ds",
                               attempt + 1, _OLLAMA_MAX_RETRIES, backoff)
                time.sleep(backoff)
        raise requests.exceptions.HTTPError(f"Ollama 429: exhausted {_OLLAMA_MAX_RETRIES} retries")

    # ---- Transparency & Self-Improvement Engine ----
    def _load_transparency_log(self) -> List[Dict]:
        try:
            if self._transparency_log_path.exists():
                return json.loads(self._transparency_log_path.read_text())
        except Exception:
            pass
        return []

    def _save_transparency_log(self):
        try:
            self._transparency_log_path.parent.mkdir(parents=True, exist_ok=True)
            # Keep last 200 entries
            entries = self._transparency_log[-200:]
            tmp = self._transparency_log_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
            tmp.replace(self._transparency_log_path)
        except Exception as e:
            logger.error("Failed to save transparency log: %s", e)

    def log_transparency(self, event_type: str, tool_name: str, what_happened: str,
                         error_detail: str = "", lesson: str = ""):
        """Log a transparency event: success, failure, or lesson learned."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,  # "failure", "success", "lesson", "recovery"
            "tool": tool_name,
            "what_happened": what_happened,
            "error": error_detail,
            "lesson": lesson,
            "model": self.current_model,
        }
        self._transparency_log.append(entry)
        self._save_transparency_log()
        if event_type == "failure":
            self._current_task_errors.append(entry)
            logger.warning("TRANSPARENCY: %s failed — %s", tool_name, what_happened)

    def get_transparency_summary(self, limit: int = 10) -> str:
        """Get recent transparency log entries formatted for display."""
        if not self._transparency_log:
            return "No transparency entries yet."
        entries = self._transparency_log[-limit:]
        lines = []
        for e in entries:
            icon = {"failure": "X", "success": "OK", "lesson": "L", "recovery": "R"}.get(e["type"], "?")
            ts = e["timestamp"][:16].replace("T", " ")
            lines.append(f"[{icon}] {ts} | {e['tool']}: {e['what_happened'][:100]}")
            if e.get("lesson"):
                lines.append(f"    Learned: {e['lesson']}")
        return "\n".join(lines)

    def get_past_failures(self, tool_name: str = None) -> List[Dict]:
        """Get past failures, optionally filtered by tool name."""
        failures = [e for e in self._transparency_log if e["type"] == "failure"]
        if tool_name:
            failures = [e for e in failures if e["tool"] == tool_name]
        return failures[-10:]

    def _generate_lessons_line(self) -> str:
        """Generate a lessons-learned line based on current task errors."""
        if not self._current_task_errors:
            return ""
        lessons = set()
        for err in self._current_task_errors:
            tool = err["tool"]
            if "write_file" in tool:
                lessons.add("Always verify file paths before writing")
            elif "blender" in tool.lower():
                lessons.add("Always verify .blend files with ls after Blender command")
            elif "terminal" in tool:
                lessons.add("Check command existence before running (e.g., which blender)")
            else:
                lessons.add(f"Verify {tool} output before claiming success")
        if lessons:
            return "\n**Lessons Learned:** " + "; ".join(lessons) + "."
        return ""

    # ---- Interrupt / Queue messaging ----
    def send_interrupt(self, message: str):
        """Send a message that Timmy reads while working (interrupt mode)."""
        with self._lock:
            self._interrupt_message = message

    def queue_message(self, message: str):
        """Queue a message for after current task completes."""
        with self._lock:
            self._queued_messages.append(message)

    def pop_queued_message(self) -> Optional[str]:
        """Get the next queued message (if any)."""
        with self._lock:
            if self._queued_messages:
                return self._queued_messages.pop(0)
            return None

    def check_interrupt(self) -> Optional[str]:
        """Check for and consume an interrupt message."""
        with self._lock:
            msg = self._interrupt_message
            self._interrupt_message = None
            return msg

    @property
    def is_working(self) -> bool:
        return self._is_working

    @property
    def has_queued(self) -> bool:
        with self._lock:
            return len(self._queued_messages) > 0

    # ---- Goals ----
    def _load_goals(self):
        gf = Path.home() / "LLTimmy" / "memory" / "active_goals.json"
        if gf.exists():
            try:
                self.active_goals = json.loads(gf.read_text())
            except Exception:
                self.active_goals = []

    def _save_goals(self):
        try:
            gf = Path.home() / "LLTimmy" / "memory" / "active_goals.json"
            gf.write_text(json.dumps(self.active_goals, indent=2))
        except Exception as e:
            logger.error("Failed to save goals: %s", e)

    def add_goal(self, goal: str):
        self.active_goals.append(goal)
        self._save_goals()

    def complete_goal(self, goal: str):
        self.active_goals = [g for g in self.active_goals if g != goal]
        self._save_goals()

    # ---- Messages ----
    def _build_messages(self, user_message: str, subconscious: List[Dict] = None, images: List[str] = None) -> List[Dict]:
        # Limit active goals to 3 most recent to prevent context pollution
        recent_goals = self.active_goals[-3:] if self.active_goals else []
        system = _build_system_prompt(self.current_model, recent_goals)
        messages = [{"role": "system", "content": system}]

        if subconscious:
            # Filter subconscious to only contextually relevant items (not stale task noise)
            ctx = "\n".join(f"[Memory] {r['content'][:200]}" for r in subconscious[:5])
            messages.append({"role": "system", "content": f"Relevant memories:\n{ctx}"})

        # Limit conversation history to 10 most recent messages to prevent old task pollution
        for msg in self.conversation_history[-10:]:
            messages.append(msg)

        user_msg: Dict = {"role": "user", "content": user_message}
        if images and _is_vision_model(self.current_model):
            user_msg["images"] = images
        messages.append(user_msg)
        return messages

    # ---- ReAct parsing ----
    @staticmethod
    def _extract_json_object(text: str, start: int) -> Optional[str]:
        """Extract a JSON object from text starting at position `start`.
        Uses brace-depth counting to handle nested braces and strings correctly."""
        if start >= len(text) or text[start] != '{':
            return None
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    @staticmethod
    def _parse_tool_call(text: str) -> Optional[Dict]:
        action_m = re.search(r"Action:\s*(\w+)", text)
        if not action_m:
            return None
        tool_name = action_m.group(1)
        params: Dict = {}
        # Find "Action Input:" then extract the JSON object with brace-matching
        input_m = re.search(r"Action Input:\s*", text)
        if input_m:
            json_start = input_m.end()
            # Skip whitespace to find opening brace
            while json_start < len(text) and text[json_start] in ' \t\n\r':
                json_start += 1
            if json_start < len(text) and text[json_start] == '{':
                json_str = AgentCore._extract_json_object(text, json_start)
                if json_str:
                    try:
                        params = json.loads(json_str)
                    except json.JSONDecodeError:
                        # Fallback: try non-greedy regex for simple cases
                        fallback_m = re.search(r"Action Input:\s*(\{.*?\})", text, re.DOTALL)
                        if fallback_m:
                            try:
                                params = json.loads(fallback_m.group(1))
                            except json.JSONDecodeError:
                                pass
        return {"tool": tool_name, "params": params}

    # ---- Tool dispatch ----
    async def _execute_tool(self, tool_name: str, params: Dict) -> str:
        dispatch = {
            "terminal_command": lambda p: self.tools.terminal_command(p.get("command", "")),
            "write_file": lambda p: self.tools.write_file(p.get("path", ""), p.get("content", "")),
            "read_file": lambda p: self.tools.read_file(p.get("path", "")),
            "web_search": lambda p: self.tools.web_search(p.get("query", ""), p.get("num_results", 5)),
            "playwright_browser": lambda p: self.tools.playwright_browser(p.get("url", "")),
            "download_url": lambda p: self.tools.download_url(p.get("url", ""), p.get("dest_dir")),
            "extract_zip": lambda p: self.tools.extract_zip(p.get("zip_path", ""), p.get("dest_dir")),
            "run_blender": lambda p: self.tools.run_blender(p.get("command", ""), p.get("gui", False)),
            "run_applescript": lambda p: self.tools.run_applescript(p.get("script", "")),
            "run_comfyui_workflow": lambda p: self.tools.run_comfyui_workflow(p.get("workflow_id")),
            "open_application": lambda p: self.tools.open_application(p.get("app_name", ""), p.get("foreground", True)),
            "github_operations": lambda p: self.tools.github_operations(p.get("action", ""), p.get("repo_name"), p.get("token")),
            "create_tool": lambda p: self.tools.create_tool(p.get("name", ""), p.get("code", "")),
            "check_service_status": lambda p: self.tools.check_service_status(p.get("service", ""), p.get("port")),
            "list_ollama_models": lambda p: self.tools.list_ollama_models(),
            "manage_ollama_model": lambda p: self.tools.manage_ollama_model(p.get("action", ""), p.get("model_name", "")),
            "send_notification": lambda p: self.tools.send_notification(p.get("title", "Timmy"), p.get("message", ""), p.get("sound", True)),
            "add_calendar_event": lambda p: self._add_calendar_event(p),
            "capture_screenshot": lambda p: self.tools.capture_screenshot(p.get("target", "desktop"), p.get("save_path")),
            "search_memory": lambda p: self._search_memory(p),
            "add_task": lambda p: self._add_task(p),
            "list_tasks": lambda p: self._list_tasks(p),
            "panel_discussion": lambda p: self._panel_discussion(p),
            "deep_research": lambda p: self._deep_research(p),
            "model_chain": lambda p: self._model_chain(p),
            "daily_debrief": lambda p: self._daily_debrief(p),
            "check_past_failures": lambda p: self._check_past_failures(p),
            "reset_system": lambda p: self._reset_system(p),
        }
        handler = dispatch.get(tool_name)
        if not handler:
            return f"Unknown tool: {tool_name}. Available: {TOOLS_DESC}"

        # Cache check for read-only tools (list_ollama_models, check_service_status)
        cacheable = tool_name in ("list_ollama_models", "check_service_status")
        if cacheable:
            cache_key = f"{tool_name}:{json.dumps(params, sort_keys=True)}"
            with self._lock:
                cached = self._tool_cache.get(cache_key)
                if cached and (time.time() - cached["ts"]) < self._cache_ttl:
                    logger.info(f"Tool cache hit: {tool_name}")
                    return cached["result"]

        for attempt in range(self.max_retries):
            try:
                output, error = await handler(params)
                if error:
                    if attempt < self.max_retries - 1:
                        logger.info(f"Tool {tool_name} error (attempt {attempt+1}), retrying: {error}")
                        self.log_transparency("failure", tool_name,
                                              f"Attempt {attempt+1}: {error[:150]}",
                                              error_detail=error)
                        continue
                    self.log_transparency("failure", tool_name,
                                          f"Failed after {self.max_retries} attempts: {error[:150]}",
                                          error_detail=error,
                                          lesson=f"Tool {tool_name} needs alternative approach")
                    return f"Error (after {self.max_retries} attempts): {error}"
                result = output or "(completed, no output)"
                # Cache successful results for cacheable tools
                if cacheable:
                    with self._lock:
                        self._tool_cache[cache_key] = {"result": result, "ts": time.time()}
                # Transparency: log success
                self.log_transparency("success", tool_name, f"Completed: {result[:100]}")
                return result
            except Exception as e:
                if attempt < self.max_retries - 1:
                    self.log_transparency("failure", tool_name,
                                          f"Attempt {attempt+1} failed",
                                          error_detail=str(e),
                                          lesson=f"Retry with different approach after {tool_name} error")
                    continue
                self.log_transparency("failure", tool_name,
                                      f"All {self.max_retries} attempts failed",
                                      error_detail=str(e))
                return f"Tool execution error: {e}"
        return "All retry attempts exhausted."

    # ---- Ollama streaming (gated to prevent 429, stops at Observation) ----
    def _stream_ollama(self, messages: List[Dict]):
        # Gate only covers request setup — released before streaming begins
        resp = None
        for attempt in range(_OLLAMA_MAX_RETRIES):
            with _ollama_gate:
                try:
                    resp = requests.post(
                        f"{self.ollama_host}/api/chat",
                        json={
                            "model": self.current_model,
                            "messages": messages,
                            "stream": True,
                            "options": {"stop": ["Observation:", "Observation :", "\nObservation"]},
                        },
                        stream=True, timeout=300,
                    )
                    if resp.status_code == 429:
                        resp = None  # Mark for retry
                    else:
                        resp.raise_for_status()
                        break  # Success — gate released, proceed to streaming
                except requests.exceptions.HTTPError as e:
                    if "429" in str(e) and attempt < _OLLAMA_MAX_RETRIES - 1:
                        resp = None  # Mark for retry
                    else:
                        yield f"\n\n**Ollama error:** {e}\n"
                        return
                except requests.ConnectionError:
                    yield f"\n\n**Cannot connect to Ollama at {self.ollama_host}.**\nRun `ollama serve` to start.\n"
                    return
            # Gate released here — sleep outside the lock for retries
            if resp is None:
                if attempt < _OLLAMA_MAX_RETRIES - 1:
                    backoff = _OLLAMA_RETRY_BACKOFF[min(attempt, len(_OLLAMA_RETRY_BACKOFF) - 1)]
                    logger.warning("Ollama 429 on stream (attempt %d/%d) — retrying in %ds",
                                   attempt + 1, _OLLAMA_MAX_RETRIES, backoff)
                    time.sleep(backoff)
        else:
            # All retries exhausted
            yield "\n\n**Ollama is busy** — the model is still loading or processing another request. Please try again in a few seconds.\n"
            return

        # Stream response WITHOUT holding the gate
        try:
            buffer = ""
            buffer_count = 0
            buffer_limit = self.config.get("response_speed", {}).get("stream_buffer_tokens", 1)
            full_text = ""  # Track full output for safety truncation

            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                token = data.get("message", {}).get("content", "")
                if token:
                    buffer += token
                    full_text += token
                    buffer_count += 1

                    # SAFETY NET: if the LLM somehow bypasses stop sequences and
                    # generates "Observation:" in its output, truncate there immediately
                    obs_idx = full_text.find("Observation:")
                    if obs_idx != -1 and obs_idx < len(full_text) - len(token) - 5:
                        # LLM is fabricating observations — stop immediately
                        logger.warning("LLM generated hallucinated Observation — truncating")
                        break

                    # Flush buffer every N tokens for faster UI updates
                    if buffer_count >= buffer_limit or data.get("done"):
                        yield buffer
                        buffer = ""
                        buffer_count = 0
                if data.get("done"):
                    if buffer:
                        yield buffer
                    break
        except requests.ConnectionError:
            yield f"\n\n**Cannot connect to Ollama at {self.ollama_host}.**\nRun `ollama serve` to start.\n"
        except Exception as e:
            yield f"\n\n**Ollama error:** {e}\n"

    # ---- Main run loop ----
    async def run(self, user_message: str, file_paths: List[str] = None) -> AsyncGenerator[str, None]:
        # Model tiering: use small model for routine, restore after
        tier_model = self._select_model_tier(user_message)
        original_model = self.current_model
        if tier_model != original_model:
            self.current_model = tier_model
            logger.info("Model tier: using %s for this request", tier_model)
        try:
            self._is_working = True
            self.memory.save_message("user", user_message)
            subconscious = self.memory.get_subconscious_context(user_message)

            accumulated = ""

            # Show relevant memories as collapsible
            if subconscious:
                mem_items = "\n".join(f"- {r['content'][:120]}" for r in subconscious[:3])
                mem_block = f"<details>\n<summary>Relevant Memories</summary>\n\n{mem_items}\n</details>\n\n"
                accumulated += mem_block
                yield accumulated

            # Process uploaded files
            images_b64: List[str] = []
            file_info_lines = []
            has_images = False
            if file_paths:
                for fp in file_paths:
                    p = Path(fp)
                    if p.suffix.lower() in IMAGE_EXTENSIONS:
                        try:
                            img_data = base64.b64encode(p.read_bytes()).decode("utf-8")
                            images_b64.append(img_data)
                            file_info_lines.append(f"[Image uploaded: {p.name}]")
                            has_images = True
                        except Exception as e:
                            file_info_lines.append(f"[Image read error: {e}]")

                        if not _is_vision_model(self.current_model):
                            file_info_lines.append(
                                f"[Note: Current model {self.current_model} may not support vision. "
                                f"Switch to a vision model like gemma3:12b for image analysis.]"
                            )
                    else:
                        # Try to read non-image files and include content
                        try:
                            content = p.read_text(encoding="utf-8")[:3000]
                            file_info_lines.append(f"[File uploaded: {p.name}]\nContent:\n{content}")
                        except Exception:
                            file_info_lines.append(f"[File uploaded: {p.name} (binary, cannot display)]")

                if file_info_lines:
                    user_message += "\n\n" + "\n".join(file_info_lines)
            else:
                # FIXED: If user mentions image but no file was uploaded, flag it
                img_keywords = ["image", "screenshot", "photo", "picture", "uploaded"]
                if any(kw in user_message.lower() for kw in img_keywords) and not has_images:
                    user_message += "\n\n[SYSTEM NOTE: No image/file was actually received with this message. If the user claims to have uploaded something, ask them to re-upload or provide the file path.]"

            messages = self._build_messages(user_message, subconscious, images_b64)

            # ReAct loop (with empty-response retry)
            final_text = ""
            failed_approaches: List[str] = []
            _retry_count = 0
            _max_retries = 1  # Single fallback retry with simplified context (Critic #10)
            _prev_step_texts: List[str] = []  # Track recent step texts for repetition detection
            _max_step_chars = 8000  # Per-step character limit to prevent runaway generation

            for step in range(self.max_react_steps):
                # Check for interrupt messages
                interrupt = self.check_interrupt()
                if interrupt:
                    interrupt_note = f"\n\n---\n**[Interrupt from Ben]:** {interrupt}\n---\n\n"
                    accumulated += interrupt_note
                    yield accumulated
                    messages.append({"role": "user", "content": f"[INTERRUPT FROM BEN]: {interrupt}"})

                step_text = ""
                step_acc_start = len(accumulated)  # Track where this step starts in accumulated
                for token_chunk in self._stream_ollama(messages):
                    step_text += token_chunk
                    accumulated += token_chunk
                    yield accumulated
                    # Per-step character limit: break if step is too long (runaway generation)
                    if len(step_text) > _max_step_chars:
                        logger.warning("Step exceeded %d chars — truncating runaway generation", _max_step_chars)
                        break

                # Repetition detection: if this step is nearly identical to the previous one,
                # the model is stuck in a loop — break out and give a final answer
                if _prev_step_texts:
                    prev = _prev_step_texts[-1]
                    # Compare normalized versions (strip whitespace differences)
                    norm_cur = step_text.strip()[:500]
                    norm_prev = prev.strip()[:500]
                    if norm_cur and norm_prev and norm_cur == norm_prev:
                        logger.warning("ReAct loop repetition detected (step %d identical to step %d) — breaking", step, step - 1)
                        self.log_transparency("failure", "react_loop",
                                              f"Repetition detected at step {step}: model stuck repeating same action",
                                              lesson="Break repetition loops early and try simplified context")
                        accumulated += "\n\n**[Agent detected a loop — stopping and summarizing what was accomplished so far.]**\n"
                        yield accumulated
                        final_text = step_text
                        break
                _prev_step_texts.append(step_text)
                if len(_prev_step_texts) > 3:
                    _prev_step_texts.pop(0)

                # CRITICAL: Truncate at any hallucinated "Observation:" the LLM snuck in.
                # Real observations come from _execute_tool, not from the LLM.
                obs_pos = step_text.find("Observation:")
                if obs_pos != -1:
                    # Strip the hallucinated observation — keep only up to Action Input
                    # Use tracked start offset to correctly slice accumulated without corrupting prior content
                    accumulated = accumulated[:step_acc_start + obs_pos]
                    step_text = step_text[:obs_pos].rstrip()
                    yield accumulated

                tool_call = self._parse_tool_call(step_text)
                if not tool_call:
                    final_text = step_text
                    break

                tool_name = tool_call["tool"]
                params = tool_call["params"]

                # Collapsible tool execution block
                exec_header = f"\n\n<details open>\n<summary>` {tool_name}({json.dumps(params, ensure_ascii=False)[:200]})`</summary>\n\n"
                accumulated += exec_header
                yield accumulated

                result = await self._execute_tool(tool_name, params)

                # Track failed approaches for error recovery
                if "Error" in result or "error" in result.lower():
                    approach_key = f"{tool_name}:{json.dumps(params)[:100]}"
                    failed_approaches.append(approach_key)
                    if len(failed_approaches) >= 3:
                        result += "\n\n[SYSTEM: You have failed 3 times. STOP and consider a completely different approach. Tell Ben what went wrong and suggest alternatives.]"

                obs_block = f"```\n{result[:3000]}\n```\n</details>\n\n"
                accumulated += obs_block
                yield accumulated

                messages.append({"role": "assistant", "content": step_text})
                messages.append({"role": "user", "content": f"Observation: {result}"})
                # NOTE: tools.log_tool_call removed here — individual tool methods
                # already handle audit logging, so calling it again doubles entries
                final_text = step_text

            # RETRY: if the model produced nothing, retry once with minimal context
            stripped_final = re.sub(r'<details.*?</details>', '', final_text, flags=re.DOTALL).strip()
            stripped_acc = re.sub(r'<details.*?</details>', '', accumulated, flags=re.DOTALL).strip()
            if (not stripped_final or len(stripped_final) < 5) and _retry_count < _max_retries:
                _retry_count += 1
                logger.warning("Empty response detected — retrying with simplified context (attempt %d)", _retry_count)
                # Simplified context: drop subconscious, limit history to 4 messages
                retry_system = _build_system_prompt(self.current_model, self.active_goals[-2:] if self.active_goals else [])
                retry_messages = [{"role": "system", "content": retry_system}]
                for msg in self.conversation_history[-4:]:
                    retry_messages.append(msg)
                retry_messages.append({"role": "user", "content": user_message})
                accumulated = ""
                final_text = ""
                for token_chunk in self._stream_ollama(retry_messages):
                    final_text += token_chunk
                    accumulated += token_chunk
                    yield accumulated
                # Strip any tool calls from retry response (retry does not execute tools)
                accumulated = re.sub(r'Thought:.*?(?=\n\n|\Z)', '', accumulated, flags=re.DOTALL).strip()
                accumulated = re.sub(r'Action:.*?(?=\n\n|\Z)', '', accumulated, flags=re.DOTALL).strip()
                accumulated = re.sub(r'Action Input:.*?(?=\n\n|\Z)', '', accumulated, flags=re.DOTALL).strip()
                final_text = accumulated
                yield accumulated

            # Post-process: filter refusal phrases from final output
            accumulated = _filter_refusals(accumulated)
            final_text = _filter_refusals(final_text)

            # Transparency: append lessons learned if there were errors
            lessons_line = self._generate_lessons_line()
            if lessons_line:
                accumulated += lessons_line
                final_text += lessons_line
                # Log the lesson
                for err in self._current_task_errors:
                    self.log_transparency("lesson", err["tool"],
                                          f"Task completed with errors in {err['tool']}",
                                          lesson=lessons_line.replace("**Lessons Learned:** ", "").strip())
            # Reset current task error tracker
            self._current_task_errors = []
            yield accumulated

            # Save to memory — store the clean final answer, not the full Thought/Action trace
            # This prevents LLM reasoning noise from polluting memory search results
            clean_for_memory = final_text
            # Strip Thought:/Action:/Action Input: blocks from memory
            clean_for_memory = re.sub(r'Thought:.*?(?=Action:|$)', '', clean_for_memory, flags=re.DOTALL).strip()
            clean_for_memory = re.sub(r'Action:.*?(?=Thought:|$)', '', clean_for_memory, flags=re.DOTALL).strip()
            if not clean_for_memory or len(clean_for_memory) < 10:
                clean_for_memory = final_text[:500]  # fallback: keep something
            self.memory.save_message("assistant", clean_for_memory)
            self.conversation_history.append({"role": "user", "content": user_message})
            self.conversation_history.append({"role": "assistant", "content": final_text})

            # Trim conversation history to prevent context pollution and memory bloat
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

            # Auto-update profile
            self._update_profile(user_message)

            # Auto-mark matching tasks as completed
            self._auto_complete_task(user_message)

            # Send notification that task is complete
            notify_config = self.config.get("notifications", {})
            if notify_config.get("enabled") and notify_config.get("on_task_complete"):
                try:
                    await self.tools.send_notification(
                        "Timmy", "Task completed. Check the chat for results.",
                        notify_config.get("sound", True)
                    )
                except Exception:
                    pass

        finally:
            self._is_working = False
            # Restore original model after tiering
            self.current_model = original_model
            # Self-healing: check for repeated failures, propose fixes
            self._check_self_healing()

    def _check_self_healing(self):
        """If the same tool fails 3+ times, propose a fix via evolution."""
        if not self._evolution:
            return
        try:
            recent = [e for e in self._transparency_log[-50:]
                      if e.get("type") == "failure"]
            tool_fails = {}
            for e in recent:
                t = e.get("tool", "unknown")
                tool_fails[t] = tool_fails.get(t, 0) + 1
            for tool_name, count in tool_fails.items():
                if count >= 3:
                    # Get last error specifically for THIS tool
                    tool_errors = [e for e in recent if e.get("tool") == tool_name]
                    last_err = tool_errors[-1].get("error_detail", "unknown") if tool_errors else "unknown"
                    self._evolution.propose_improvement(
                        f"Fix repeated {tool_name} failures",
                        f"Tool '{tool_name}' has failed {count} times recently. "
                        f"Last error: {last_err[:200]}",
                        file_target="tools.py",
                    )
        except Exception:
            pass

    def _update_profile(self, user_message: str):
        try:
            self.memory.profile.update_from_message(user_message)
        except Exception:
            pass

    def _auto_complete_task(self, user_message: str):
        """Auto-mark in-progress tasks as completed when the agent finishes responding.
        Uses fuzzy matching with high threshold to avoid false positives (Critic #11)."""
        if not self.task_mgr:
            return
        try:
            in_progress = self.task_mgr.get_in_progress()
            if not in_progress:
                return
            msg_lower = user_message.lower().strip()
            # Require explicit completion signals to prevent false matches
            completion_signals = {"done", "finished", "completed", "complete", "success"}
            has_completion_signal = any(w in msg_lower for w in completion_signals)
            for task in in_progress:
                title_lower = task.title.lower().strip()
                title_words = [w for w in title_lower.split() if len(w) >= 3]
                match_count = sum(1 for w in title_words if w in msg_lower)
                # Require 80% word match AND a completion signal, or exact title substring
                if title_lower in msg_lower or (
                    has_completion_signal and title_words and
                    len(title_words) >= 3 and match_count / len(title_words) >= 0.8
                ):
                    self.task_mgr.update_status(task.id, "completed")
                    logger.info(f"Auto-completed task: {task.title}")
        except Exception as e:
            logger.warning(f"Task auto-complete error: {e}")

    async def _add_calendar_event(self, params: Dict):
        """Add an event to the internal calendar (not Apple Calendar)."""
        if not self.scheduler:
            return None, "Calendar module not loaded. Scheduler was not initialized."
        try:
            title = params.get("title", "Untitled Event")
            due = params.get("due")
            recurring = params.get("recurring")
            # Fix: LLM sometimes sends "null" string instead of None
            if recurring in ("null", "none", "None", ""):
                recurring = None
            event_type = params.get("type", "reminder")
            event = self.scheduler.add_event(
                title=title,
                due=due,
                event_type=event_type,
                source="timmy",
                recurring=recurring,
            )
            return f"Calendar event added: '{title}' due {event.get('due', 'N/A')}" + (f" (recurring: {recurring})" if recurring else ""), None
        except Exception as e:
            return None, f"Calendar error: {e}"

    async def _search_memory(self, params: Dict):
        """Search across all memory layers (subconscious + conscious)."""
        try:
            query = params.get("query", "")
            n = params.get("n", 5)
            if not query:
                return "No query provided. Use: search_memory({\"query\": \"search terms\"})", None
            results = self.memory.search_memory(query, n=n)
            if not results:
                return f"No memories found matching '{query}'.", None
            lines = []
            for i, r in enumerate(results, 1):
                content = r.get("content", "")[:200]
                ts = r.get("metadata", {}).get("timestamp", "")[:19]
                source = r.get("metadata", {}).get("source", "subconscious")
                lines.append(f"{i}. [{source}] ({ts}) {content}")
            return f"Found {len(results)} memories matching '{query}':\n" + "\n".join(lines), None
        except Exception as e:
            return None, f"Memory search error: {e}"

    async def _add_task(self, params: Dict):
        """Add a task to the task manager."""
        if not self.task_mgr:
            return "Task manager not loaded.", None
        try:
            title = params.get("title", "Untitled Task")
            description = params.get("description", "")
            urgency = params.get("urgency", "normal")
            schedule = params.get("schedule", "now")
            priority = {"critical": 1, "high": 3, "normal": 5, "low": 8}.get(urgency, 5)
            task = self.task_mgr.add_task(
                title=title,
                description=description,
                priority=priority,
                urgency=urgency,
                schedule=schedule,
            )
            return f"Task created: '{title}' [urgency={urgency}, schedule={schedule}, id={task.id}]", None
        except Exception as e:
            return None, f"Task creation error: {e}"

    async def _list_tasks(self, params: Dict):
        """List all tasks in the task manager."""
        if not self.task_mgr:
            return "Task manager not loaded.", None
        try:
            summary = self.task_mgr.get_summary_text()
            if not summary or summary == "No active tasks.":
                return "No tasks in the task manager.", None
            return f"Current tasks:\n{summary}", None
        except Exception as e:
            return None, f"Task list error: {e}"

    # ---- Panel Discussion / Think Tank ----
    async def _panel_discussion(self, params: Dict):
        """Multi-model debate: query 2-3 models on a topic, collect responses, synthesize consensus."""
        topic = params.get("topic", "")
        if not topic:
            return "No topic provided. Use: panel_discussion({\"topic\": \"...\"})", None
        requested_models = params.get("models", [])
        rounds = min(params.get("rounds", 2), 3)  # Cap at 3 rounds

        try:
            # Get available models
            available = self.get_available_models()
            if len(available) < 2:
                return "Need at least 2 local models for panel discussion. Pull more models first.", None

            # Select models: use requested or auto-pick 2-3 diverse models
            panel_models = []
            if requested_models:
                for m in requested_models:
                    if m in available:
                        panel_models.append(m)
                    else:
                        # Try fuzzy match
                        matches = [a for a in available if m.lower() in a.lower()]
                        if matches:
                            panel_models.append(matches[0])
            if len(panel_models) < 2:
                # Auto-pick: current model + 1-2 others (skip embed models)
                skip = {"nomic-embed-text:latest"}
                candidates = [m for m in available if m not in skip and m != self.current_model]
                panel_models = [self.current_model]
                for c in candidates[:2]:
                    panel_models.append(c)

            if len(panel_models) < 2:
                return "Need at least 2 models. Only found: " + ", ".join(panel_models), None

            # Run debate rounds
            all_responses = {}
            context = []  # Accumulated debate context

            for round_num in range(1, rounds + 1):
                round_prompt = topic if round_num == 1 else (
                    f"Topic: {topic}\n\nPrevious responses from other models:\n" +
                    "\n".join(f"- {m}: {r[:300]}" for m, r in all_responses.items()) +
                    f"\n\nRound {round_num}: Respond to the above. Challenge weak points, "
                    "build on strong ones, and move toward consensus."
                )

                for model in panel_models:
                    try:
                        result = self._ollama_request_sync({
                            "model": model,
                            "messages": [{"role": "user", "content": round_prompt}],
                            "stream": False,
                            "options": {"num_predict": 400},
                        }, timeout=120)
                        answer = result.get("message", {}).get("content", "(no response)")
                        all_responses[model] = answer
                    except Exception as e:
                        all_responses[model] = f"(error: {e})"

            # Build panel report
            lines = [f"## Panel Discussion: {topic}", f"**Models**: {', '.join(panel_models)}", f"**Rounds**: {rounds}", ""]
            for model, response in all_responses.items():
                lines.append(f"### {model}")
                lines.append(response[:600])
                lines.append("")

            # Synthesize consensus using the primary model
            synth_prompt = (
                f"Based on these model responses about '{topic}':\n\n" +
                "\n".join(f"**{m}**: {r[:400]}" for m, r in all_responses.items()) +
                "\n\nSynthesize a brief consensus (3-5 sentences). Note agreements and key disagreements."
            )
            try:
                synth_result = self._ollama_request_sync({
                    "model": self.current_model,
                    "messages": [{"role": "user", "content": synth_prompt}],
                    "stream": False,
                    "options": {"num_predict": 300},
                }, timeout=90)
                consensus = synth_result.get("message", {}).get("content", "(no consensus)")
                lines.append("### Consensus")
                lines.append(consensus[:500])
            except Exception:
                lines.append("### Consensus")
                lines.append("(Synthesis failed — see individual responses above)")

            return "\n".join(lines), None
        except Exception as e:
            return None, f"Panel discussion error: {e}"

    # ---- Deep Research Loops ----
    async def _deep_research(self, params: Dict):
        """Recursive web research: search → read pages → find gaps → follow-up → report."""
        query = params.get("query", "")
        if not query:
            return "No query provided. Use: deep_research({\"query\": \"...\"})", None
        depth = min(params.get("depth", 2), 3)  # Cap at 3 iterations
        max_sources = min(params.get("max_sources", 5), 8)

        try:
            all_findings = []
            searched_queries = set()
            urls_read = set()
            year = datetime.now().year

            for iteration in range(depth):
                # Add year context to query
                search_query = f"{query} {year}" if str(year) not in query else query
                if search_query in searched_queries:
                    # Generate follow-up query from gaps
                    if all_findings:
                        search_query = f"{query} {year} details technical specifications"
                    else:
                        break
                searched_queries.add(search_query)

                # Search
                search_result, search_err = await self.tools.web_search(search_query, num_results=max_sources)
                if search_err:
                    all_findings.append(f"[Search {iteration+1} failed: {search_err}]")
                    continue

                try:
                    results = json.loads(search_result) if search_result else []
                except json.JSONDecodeError:
                    results = []

                if not results:
                    all_findings.append(f"[Search {iteration+1}: no results for '{search_query}']")
                    continue

                # Read top pages (try to extract content)
                for r in results[:3]:
                    url = r.get("url", "")
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")

                    # Clean DuckDuckGo redirect URLs
                    if "uddg=" in url:
                        import urllib.parse
                        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                        url = parsed.get("uddg", [url])[0]

                    if url in urls_read or not url.startswith("http"):
                        all_findings.append(f"- **{title}**: {snippet}")
                        continue
                    urls_read.add(url)

                    # Try to read the page
                    page_content = ""
                    try:
                        page_result, page_err = await self.tools.playwright_browser(url)
                        if not page_err and page_result:
                            page_content = page_result[:2000]
                    except Exception:
                        pass

                    if page_content and len(page_content) > 100:
                        all_findings.append(f"- **{title}** ({url})\n  {page_content[:500]}")
                    else:
                        all_findings.append(f"- **{title}**: {snippet}")

                # Generate follow-up query for next iteration based on gaps
                if iteration < depth - 1 and all_findings:
                    gap_prompt = (
                        f"Based on these findings about '{query}':\n" +
                        "\n".join(all_findings[-3:]) +
                        "\n\nWhat specific follow-up question would fill the biggest knowledge gap? "
                        "Reply with ONLY the search query, nothing else."
                    )
                    try:
                        gap_result = self._ollama_request_sync({
                            "model": self.current_model,
                            "messages": [{"role": "user", "content": gap_prompt}],
                            "stream": False,
                            "options": {"num_predict": 50},
                        }, timeout=30)
                        follow_up = gap_result.get("message", {}).get("content", "").strip()
                        if follow_up and len(follow_up) > 5:
                            query = follow_up  # Use gap-filling query for next iteration
                    except Exception:
                        pass

            # Compile final report
            if not all_findings:
                return f"Deep research on '{query}' found no results after {depth} iterations.", None

            report_lines = [
                f"## Deep Research Report: {params.get('query', query)}",
                f"**Iterations**: {depth} | **Sources**: {len(urls_read)} pages read | **Searches**: {len(searched_queries)}",
                "",
                "### Findings",
            ]
            report_lines.extend(all_findings[:15])
            report_lines.append("")
            report_lines.append(f"### Queries Used")
            report_lines.extend(f"- {q}" for q in searched_queries)

            return "\n".join(report_lines), None
        except Exception as e:
            return None, f"Deep research error: {e}"

    # ---- Model Chaining / Pipeline ----
    async def _model_chain(self, params: Dict):
        """Chain multiple models: each step's output feeds into the next model's prompt."""
        steps = params.get("steps", [])
        if not steps or not isinstance(steps, list):
            return "No steps provided. Use: model_chain({\"steps\": [{\"model\": \"qwen3:8b\", \"prompt\": \"...\"}]})", None

        try:
            available = self.get_available_models()
            results = []
            prev_output = ""

            for i, step in enumerate(steps):
                model = step.get("model", self.current_model)
                prompt = step.get("prompt", "")

                # Replace {prev} placeholder with previous step's output
                if prev_output:
                    prompt = prompt.replace("{prev}", prev_output[:1500])

                if not prompt:
                    results.append(f"Step {i+1}: (empty prompt, skipped)")
                    continue

                # Check if model is available
                if model not in available:
                    # Try fuzzy match
                    matches = [a for a in available if model.lower() in a.lower()]
                    if matches:
                        model = matches[0]
                    else:
                        results.append(f"Step {i+1} ({model}): Model not available")
                        continue

                try:
                    chain_result = self._ollama_request_sync({
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"num_predict": 500},
                    }, timeout=120)
                    answer = chain_result.get("message", {}).get("content", "(no response)")
                    prev_output = answer
                    results.append(f"### Step {i+1}: {model}\n**Prompt**: {prompt[:100]}...\n**Output**: {answer[:500]}")
                except Exception as e:
                    results.append(f"Step {i+1} ({model}): Error — {e}")
                    prev_output = f"(error in step {i+1})"

            if not results:
                return "Pipeline produced no results.", None

            header = f"## Model Chain Pipeline ({len(steps)} steps)\n"
            return header + "\n\n".join(results), None
        except Exception as e:
            return None, f"Model chain error: {e}"

    # ---- Daily Debrief Agent ----
    async def _daily_debrief(self, params: Dict):
        """Generate a comprehensive daily debrief: tasks, calendar, memory, and suggestions."""
        try:
            sections = []
            today = date.today().isoformat()
            now = datetime.now()

            # 1. Task summary
            if self.task_mgr:
                task_summary = self.task_mgr.get_summary_text()
                pending = self.task_mgr.get_pending_tasks()
                in_progress = self.task_mgr.get_in_progress()
                completed_today = [
                    t for t in self.task_mgr.get_all_tasks()
                    if t.completed_at and t.completed_at.startswith(today)
                ]
                sections.append(f"### Tasks")
                sections.append(f"- **Pending**: {len(pending)}")
                sections.append(f"- **In Progress**: {len(in_progress)}")
                sections.append(f"- **Completed Today**: {len(completed_today)}")
                if task_summary and task_summary != "No active tasks.":
                    sections.append(f"\n{task_summary}")

            # 2. Calendar
            if self.scheduler:
                today_events = self.scheduler.get_events_for_date(today)
                upcoming = self.scheduler.get_upcoming(limit=5)
                sections.append(f"\n### Calendar")
                sections.append(f"- **Today's events**: {len(today_events)}")
                sections.append(f"- **Upcoming**: {len(upcoming)}")
                # Read-only peek — don't mutate event state from debrief
                due_now = [e for e in self.scheduler.events
                           if e.get("status") == "pending"
                           and datetime.fromisoformat(e["due"]) <= datetime.now()]
                if due_now:
                    sections.append(f"- **Due now**: {len(due_now)}")
                    for t in due_now:
                        sections.append(f"  - {t.get('title', 'Untitled')} (due {t.get('due', 'N/A')[:16]})")
                for e in upcoming[:3]:
                    sections.append(f"  - {e.get('due', '')[:16]} — {e.get('title', 'Untitled')}")

            # 3. Active goals
            if self.active_goals:
                sections.append(f"\n### Active Goals")
                for g in self.active_goals[:5]:
                    sections.append(f"- {g}")

            # 4. Memory stats
            try:
                mem_stats = self.memory.get_stats() if hasattr(self.memory, 'get_stats') else {}
                if mem_stats:
                    sections.append(f"\n### Memory")
                    for k, v in mem_stats.items():
                        sections.append(f"- {k}: {v}")
            except Exception:
                pass

            # 5. System health
            try:
                ollama_ok = self.check_ollama_status()
                sections.append(f"\n### System Health")
                sections.append(f"- **Ollama**: {'Online' if ollama_ok else 'Offline'}")
                sections.append(f"- **Model**: {self.current_model}")
                sections.append(f"- **Time**: {now.strftime('%H:%M:%S')}")
            except Exception:
                pass

            # 6. Generate AI suggestions
            debrief_text = "\n".join(sections)
            try:
                suggest_result = self._ollama_request_sync({
                    "model": self.current_model,
                    "messages": [{"role": "user", "content": (
                        f"Given this daily status:\n{debrief_text}\n\n"
                        "Suggest 3 specific next actions Ben should take. Be brief and actionable."
                    )}],
                    "stream": False,
                    "options": {"num_predict": 200},
                }, timeout=60)
                suggestions = suggest_result.get("message", {}).get("content", "")
                if suggestions:
                    sections.append(f"\n### Suggested Next Actions")
                    sections.append(suggestions[:400])
            except Exception:
                pass

            header = f"## Daily Debrief — {today} {now.strftime('%H:%M')}\n"
            return header + "\n".join(sections), None
        except Exception as e:
            return None, f"Debrief error: {e}"

    # ---- Check Past Failures (Transparency) ----
    async def _check_past_failures(self, params: Dict):
        """Check the transparency log for past failures and lessons learned."""
        tool_filter = params.get("tool", None)
        limit = min(params.get("limit", 10), 20)
        failures = self.get_past_failures(tool_filter)
        if not failures:
            return "No past failures recorded." + (f" (filtered by tool: {tool_filter})" if tool_filter else ""), None

        lines = [f"## Transparency Log — Past Failures ({len(failures)} entries)"]
        for f in failures[-limit:]:
            ts = f["timestamp"][:16].replace("T", " ")
            lines.append(f"- **{ts}** | `{f['tool']}`: {f['what_happened']}")
            if f.get("error"):
                lines.append(f"  Error: {f['error'][:200]}")
            if f.get("lesson"):
                lines.append(f"  Lesson: {f['lesson']}")
        return "\n".join(lines), None

    # ---- Reset System Tool (#28) ----
    async def _reset_system(self, params: Dict):
        """Reset tasks, memory, calendar, and/or transparency log for a fresh start."""
        results = []
        reset_tasks = params.get("reset_tasks", True)
        reset_memory = params.get("reset_memory", True)
        reset_calendar = params.get("reset_calendar", True)
        reset_transparency = params.get("reset_transparency", False)

        if reset_tasks and self.task_mgr:
            try:
                all_tasks = self.task_mgr.get_all_tasks()
                for task in all_tasks:
                    self.task_mgr.update_status(task.id, "completed")
                results.append(f"Tasks: {len(all_tasks)} tasks marked completed")
            except Exception as e:
                results.append(f"Tasks reset error: {e}")

        if reset_memory:
            try:
                # Clear conscious memory (conversation history)
                self.conversation_history.clear()
                # Clear chat persistence files for today
                chat_dir = Path.home() / "LLTimmy" / "memory" / "raw_chats"
                if chat_dir.exists():
                    today_file = chat_dir / f"{date.today().isoformat()}.json"
                    if today_file.exists():
                        today_file.write_text("[]")
                results.append("Memory: conversation history and today's chat cleared")
            except Exception as e:
                results.append(f"Memory reset error: {e}")

        if reset_calendar and self.scheduler:
            try:
                with self.scheduler._lock:
                    self.scheduler.events.clear()
                    self.scheduler._save()
                results.append("Calendar: all events cleared")
            except Exception as e:
                results.append(f"Calendar reset error: {e}")

        if reset_transparency:
            self._transparency_log.clear()
            self._current_task_errors.clear()
            self._save_transparency_log()
            results.append("Transparency log: cleared")

        summary = "\n".join(f"- {r}" for r in results)
        return f"System reset complete:\n{summary}\n\nI'm feeling brand new and ready to help!", None

    def clear_history(self):
        self.conversation_history.clear()

    # ---- Conversation Branching ----
    def branch_conversation(self, branch_name: str = None) -> Dict:
        """Create a branch (snapshot) of the current conversation state.
        Returns a branch object that can be restored later."""
        import copy
        branch_id = branch_name or f"branch_{datetime.now().strftime('%H%M%S')}"
        branch = {
            "id": branch_id,
            "created_at": datetime.now().isoformat(),
            "history": copy.deepcopy(self.conversation_history),
            "goals": copy.deepcopy(self.active_goals),
            "model": self.current_model,
        }
        # Store branch
        branches_file = Path.home() / "LLTimmy" / "memory" / "branches.json"
        branches = {}
        if branches_file.exists():
            try:
                branches = json.loads(branches_file.read_text())
            except Exception:
                branches = {}
        branches[branch_id] = branch
        tmp = branches_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(branches, indent=2, ensure_ascii=False))
        tmp.replace(branches_file)
        logger.info(f"Conversation branched: {branch_id} ({len(self.conversation_history)} messages)")
        return branch

    def list_branches(self) -> List[Dict]:
        """List all saved conversation branches."""
        branches_file = Path.home() / "LLTimmy" / "memory" / "branches.json"
        if not branches_file.exists():
            return []
        try:
            branches = json.loads(branches_file.read_text())
            return [
                {"id": b["id"], "created_at": b["created_at"], "messages": len(b["history"]), "model": b.get("model", "?")}
                for b in branches.values()
            ]
        except Exception:
            return []

    def restore_branch(self, branch_id: str) -> bool:
        """Restore a conversation branch, replacing current history."""
        import copy
        branches_file = Path.home() / "LLTimmy" / "memory" / "branches.json"
        if not branches_file.exists():
            return False
        try:
            branches = json.loads(branches_file.read_text())
            if branch_id not in branches:
                return False
            branch = branches[branch_id]
            self.conversation_history = copy.deepcopy(branch["history"])
            self.active_goals = copy.deepcopy(branch.get("goals", []))
            if branch.get("model"):
                self.current_model = branch["model"]
            logger.info(f"Restored branch: {branch_id} ({len(self.conversation_history)} messages)")
            return True
        except Exception as e:
            logger.error(f"Branch restore error: {e}")
            return False

    def delete_branch(self, branch_id: str) -> bool:
        """Delete a conversation branch."""
        branches_file = Path.home() / "LLTimmy" / "memory" / "branches.json"
        if not branches_file.exists():
            return False
        try:
            branches = json.loads(branches_file.read_text())
            if branch_id in branches:
                del branches[branch_id]
                tmp = branches_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(branches, indent=2, ensure_ascii=False))
                tmp.replace(branches_file)
                return True
            return False
        except Exception:
            return False
