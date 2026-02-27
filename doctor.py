"""
Doctor Watchdog for LLTimmy
Pure-code supervisor with on-demand LLM boost for complex updates.

Blueprint:
- Watchdog Loop: check Timmy PID every 5s, auto-restart <3s
- Monitor /updates/ folder for JSON update files from Timmy
- Complexity scoring: line_count + keyword detection
- Apply Routine: pure code for simple, LLM review for complex
- Cross-upgrade: Timmy proposes Doctor changes, Doctor applies Timmy changes
- No self-edits: Doctor cannot modify doctor.py; Timmy cannot modify main.py/agent_core.py
- Config: doctor_llm_enabled, doctor_model, complexity_threshold

UI: Gradio on http://127.0.0.1:7861 (emergency chat + monitoring)
"""
import os
import sys
import json
import time
import signal
import subprocess
import logging
import shutil
import hashlib
import requests
from datetime import datetime
from pathlib import Path
from threading import Thread, Lock

import psutil
import gradio as gr

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "doctor_actions.log"
PROJECT_LOG = BASE_DIR / "PROJECT_LOG.md"
UPDATES_DIR = BASE_DIR / "updates"
PID_FILE = Path("/tmp/timmy.pid")
DOCTOR_PID_FILE = Path("/tmp/doctor.pid")
STATUS_FILE = Path("/tmp/timmy_status.json")

# Protected files -- Doctor cannot edit these (self-protection)
DOCTOR_PROTECTED = {"doctor.py", "run_doctor.sh"}
# Timmy cannot edit these (protected by Doctor)
TIMMY_PROTECTED = {"main.py", "agent_core.py"}
# Banned paths for any update
BANNED_PATHS = ["/System", "/Library", "/usr", "/bin", "/sbin"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("doctor")

_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
logger.addHandler(_console)


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Complexity Scoring
# ---------------------------------------------------------------------------
def score_complexity(changes: list) -> int:
    """Score update complexity: line_count + keyword presence."""
    score = 0
    risky_keywords = ["sudo", "rm", "new_lib", "import", "subprocess", "eval", "exec",
                      "os.system", "shutil.rmtree", "pip install", "brew"]

    for change in changes:
        content = change.get("content", "")
        lines = content.count("\n") + 1
        score += lines

        for kw in risky_keywords:
            if kw in content.lower():
                score += 5

    return score


def validate_file_checksum(path: Path) -> str:
    """Compute SHA256 checksum for file validation."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_banned_path(filepath: str) -> bool:
    """Check if a file path is in banned locations (resolves symlinks + ..)."""
    try:
        resolved = os.path.realpath(os.path.abspath(os.path.expanduser(filepath)))
    except Exception:
        return True  # Fail-safe: ban unresolvable paths
    for banned in BANNED_PATHS:
        banned_resolved = os.path.realpath(os.path.abspath(os.path.expanduser(banned)))
        if resolved == banned_resolved or resolved.startswith(banned_resolved + os.sep):
            return True
    return False


# ---------------------------------------------------------------------------
# LLM Boost Layer (On-Demand Only)
# ---------------------------------------------------------------------------
def call_llm_for_review(prompt: str, config: dict) -> dict:
    """Call LLM only for complex updates. Returns approval JSON.
    Falls back to pure-code reject if LLM is unavailable."""

    if not config.get("doctor_llm_enabled", True):
        return {"approved": False, "modified_changes": [], "reasons": "LLM review disabled in config."}

    model_spec = config.get("doctor_model", "ollama/qwen3:30b")

    try:
        if model_spec.startswith("ollama/"):
            model_name = model_spec.removeprefix("ollama/")
            host = config.get("ollama_host", "http://localhost:11434")

            review_prompt = (
                f"Review this update for LLTimmy. Check for security issues, bugs, and correctness.\n"
                f"Output ONLY valid JSON: {{\"approved\": bool, \"modified_changes\": list, \"reasons\": str}}\n\n"
                f"Update:\n{prompt}"
            )

            resp = requests.post(
                f"{host}/api/generate",
                json={"model": model_name, "prompt": review_prompt, "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            response_text = resp.json().get("response", "")

            # Parse JSON from response (Critic #25: brace-depth extraction for nested JSON)
            json_match = None
            try:
                json_match = json.loads(response_text)
            except json.JSONDecodeError:
                # Brace-depth counting to handle nested objects
                start = response_text.find('{')
                if start != -1:
                    depth = 0
                    in_str = False
                    esc = False
                    for i in range(start, len(response_text)):
                        ch = response_text[i]
                        if esc:
                            esc = False
                            continue
                        if ch == '\\' and in_str:
                            esc = True
                            continue
                        if ch == '"' and not esc:
                            in_str = not in_str
                            continue
                        if in_str:
                            continue
                        if ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                try:
                                    json_match = json.loads(response_text[start:i + 1])
                                except json.JSONDecodeError:
                                    pass
                                break

            if json_match and isinstance(json_match, dict) and "approved" in json_match:
                return json_match

            return {"approved": False, "modified_changes": [], "reasons": "LLM returned invalid JSON, auto-rejected."}

        elif model_spec.startswith("lmstudio/"):
            # LM Studio via OpenAI-compatible API on localhost:1234
            from openai import OpenAI
            client = OpenAI(base_url="http://localhost:1234/v1", api_key="not-needed")

            review_prompt = (
                f"Review this update for LLTimmy. Check for security issues, bugs, and correctness.\n"
                f"Output ONLY valid JSON: {{\"approved\": bool, \"modified_changes\": list, \"reasons\": str}}\n\n"
                f"Update:\n{prompt}"
            )

            completion = client.chat.completions.create(
                model=model_spec.removeprefix("lmstudio/"),
                messages=[{"role": "user", "content": review_prompt}],
                temperature=0.1,
            )
            response_text = completion.choices[0].message.content

            try:
                result = json.loads(response_text)
                if isinstance(result, dict) and "approved" in result:
                    return result
            except json.JSONDecodeError:
                pass

            return {"approved": False, "modified_changes": [], "reasons": "LM Studio returned invalid JSON."}

        else:
            return {"approved": False, "modified_changes": [], "reasons": f"Unknown model spec: {model_spec}"}

    except Exception as e:
        logger.warning(f"LLM review failed ({e}), falling back to pure-code reject")
        return {"approved": False, "modified_changes": [], "reasons": f"LLM unavailable ({e}), auto-rejected."}


# ---------------------------------------------------------------------------
# Doctor Core
# ---------------------------------------------------------------------------
class Doctor:
    def __init__(self):
        self.monitoring = True
        self.restart_count = 0
        self.max_restarts = 10
        self.log_entries: list[str] = []
        self._lock = Lock()

    def log(self, message: str, level: str = "INFO"):
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        if level == "ERROR":
            logger.error(message)
        else:
            logger.info(message)
        with self._lock:
            self.log_entries.append(entry)
            if len(self.log_entries) > 500:
                self.log_entries = self.log_entries[-500:]

    def _write_project_log(self, action: str, details: str = ""):
        """Append to PROJECT_LOG.md for audit trail."""
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"\n## [{ts}] {action}\n{details}\n"
            with open(PROJECT_LOG, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.warning(f"Project log write failed: {e}")

    # ---- Timmy process management ----
    def get_timmy_pid(self) -> int | None:
        if PID_FILE.exists():
            try:
                return int(PID_FILE.read_text().strip())
            except (ValueError, OSError):
                return None
        return None

    def is_timmy_running(self) -> bool:
        pid = self.get_timmy_pid()
        if pid is None:
            return False
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def start_timmy(self) -> str:
        self.log("Starting Timmy ...")
        try:
            venv_python = str(BASE_DIR / ".venv" / "bin" / "python3")
            if not Path(venv_python).exists():
                venv_python = sys.executable
            stderr_fd = open(BASE_DIR / "timmy_stderr.log", "a")
            proc = subprocess.Popen(
                [venv_python, str(BASE_DIR / "src" / "app.py")],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=stderr_fd,
            )
            stderr_fd.close()  # Child inherits the fd; parent must close its copy
            msg = f"Timmy started (PID {proc.pid})"
            self.log(msg)
            self._write_project_log("Timmy Started", f"PID: {proc.pid}")
            return msg
        except Exception as e:
            msg = f"Failed to start Timmy: {e}"
            self.log(msg, "ERROR")
            return msg

    def stop_timmy(self) -> str:
        pid = self.get_timmy_pid()
        if pid is None:
            return "Timmy is not running."
        try:
            os.kill(pid, signal.SIGTERM)
            self.log(f"SIGTERM -> Timmy (PID {pid})")
            time.sleep(2)
            if self.is_timmy_running():
                os.kill(pid, signal.SIGKILL)
                self.log(f"SIGKILL -> Timmy (PID {pid})")
        except ProcessLookupError:
            pass
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
        return f"Timmy stopped (was PID {pid})."

    def restart_timmy(self) -> str:
        if self.restart_count >= self.max_restarts:
            msg = "Max restarts reached. Manual intervention needed."
            self.log(msg, "ERROR")
            return msg
        self.log("Restarting Timmy ...")
        self.stop_timmy()
        time.sleep(1)
        result = self.start_timmy()
        self.restart_count += 1
        return result

    # ---- Update Application (Blueprint: Apply Routine) ----
    def apply_update(self, json_data: dict) -> str:
        """Apply a single update. Pure code for simple, LLM review for complex."""
        config = _load_config()
        threshold = config.get("complexity_threshold", 10)

        changes = json_data.get("changes", [])
        update_type = json_data.get("type", "update")
        source = json_data.get("source", "unknown")

        if not changes:
            return "No changes in update."

        # Validate all file targets
        for change in changes:
            target = change.get("file", "")
            if not target:
                return f"Change missing file target."
            if is_banned_path(target):
                self.log(f"BLOCKED: update targets banned path {target}", "ERROR")
                return f"BLOCKED: cannot modify {target} (banned path)."
            # Cross-edit rule: Doctor cannot edit itself (Critic #23)
            if Path(target).name in DOCTOR_PROTECTED:
                self.log(f"BLOCKED: Doctor cannot edit {target} (self-protection)", "ERROR")
                return f"BLOCKED: Doctor cannot modify its own files ({target})."
            # Cross-edit rule: Timmy-protected files cannot be overwritten via updates (Critic #23)
            if Path(target).name in TIMMY_PROTECTED:
                self.log(f"BLOCKED: {target} is Timmy-protected, cannot be modified via updates", "ERROR")
                return f"BLOCKED: {target} is protected and cannot be modified via updates."

        complexity = score_complexity(changes)
        self.log(f"Update from {source}: {len(changes)} changes, complexity={complexity}")

        if complexity <= threshold:
            # Pure code apply
            return self._pure_code_apply(changes, source)
        else:
            # LLM review needed
            if config.get("doctor_llm_enabled", True):
                self.log(f"Complex update (score={complexity}), requesting LLM review")
                review = call_llm_for_review(json.dumps(json_data, indent=2), config)

                if review.get("approved"):
                    self.log(f"LLM approved update: {review.get('reasons', 'OK')}")
                    final_changes = review.get("modified_changes") or changes
                    return self._pure_code_apply(final_changes, source, llm_reviewed=True)
                else:
                    reason = review.get("reasons", "No reason given")
                    self.log(f"LLM rejected update: {reason}", "ERROR")
                    return f"Update REJECTED by LLM review: {reason}"
            else:
                self.log(f"Complex update (score={complexity}), LLM disabled, auto-rejecting", "ERROR")
                return f"Update REJECTED: complexity={complexity} exceeds threshold={threshold} and LLM review is disabled."

    def _pure_code_apply(self, changes: list, source: str, llm_reviewed: bool = False) -> str:
        """Apply changes via pure file copy/replace. Always validate after."""
        applied = []
        for change in changes:
            target_path = BASE_DIR / change.get("file", "")
            content = change.get("content", "")
            action = change.get("action", "write")

            try:
                # Pre-apply checksum
                pre_checksum = validate_file_checksum(target_path)

                if action == "write":
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_text(content, encoding="utf-8")
                elif action == "append":
                    with open(target_path, "a", encoding="utf-8") as f:
                        f.write(content)
                elif action == "delete":
                    if target_path.exists():
                        target_path.unlink()
                elif action == "copy":
                    src = BASE_DIR / change.get("source", "")
                    if src.exists():
                        shutil.copy2(src, target_path)
                else:
                    self.log(f"Unknown action '{action}' for {target_path}", "ERROR")
                    continue

                # Post-apply validation
                post_checksum = validate_file_checksum(target_path)
                applied.append(f"{target_path.name} ({action})")
                self.log(f"Applied: {target_path.name} [{pre_checksum[:8]}..→{post_checksum[:8]}..]")

            except Exception as e:
                self.log(f"Apply failed for {target_path}: {e}", "ERROR")
                return f"Apply failed: {e}"

        # Post-apply: log and optionally restart
        review_tag = " [LLM-reviewed]" if llm_reviewed else ""
        self._write_project_log(
            f"Update Applied{review_tag}",
            f"Source: {source}\nFiles: {', '.join(applied)}"
        )

        # Send status to Timmy chat if online
        self._notify_timmy(f"Doctor applied update: {', '.join(applied)}")

        # Restart Timmy if core files changed
        core_files = {"main.py", "agent_core.py", "tools.py", "memory_manager.py"}
        if any(Path(a.split(" ")[0]).name in core_files for a in applied):
            self.log("Core file changed, restarting Timmy")
            self.restart_timmy()

        return f"Applied: {', '.join(applied)}"

    def _notify_timmy(self, message: str):
        """Send a status message to Timmy's chat file (if online)."""
        try:
            from datetime import date as _date
            chat_file = BASE_DIR / "memory" / "raw_chats" / f"{_date.today().isoformat()}.json"
            if chat_file.exists():
                messages = json.loads(chat_file.read_text(encoding="utf-8"))
            else:
                messages = []
            messages.append({
                "role": "assistant",
                "content": f"[Doctor] {message}",
                "timestamp": datetime.now().isoformat(),
                "metadata": {"source": "doctor"},
            })
            chat_file.write_text(json.dumps(messages, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Failed to notify Timmy: {e}")

    # ---- Process update files from /updates/ folder ----
    def check_and_apply_updates(self) -> str:
        """Check /updates/ for new JSON update files from Timmy."""
        if not UPDATES_DIR.exists():
            UPDATES_DIR.mkdir(parents=True, exist_ok=True)
            return "No updates."

        update_files = sorted(UPDATES_DIR.glob("*.json"))
        if not update_files:
            # Also handle .py files (legacy format)
            py_updates = sorted(UPDATES_DIR.glob("*.py"))
            if py_updates:
                return self._apply_legacy_updates(py_updates)
            return "No updates."

        results = []
        for update_file in update_files:
            try:
                data = json.loads(update_file.read_text(encoding="utf-8"))
                result = self.apply_update(data)
                results.append(f"{update_file.name}: {result}")
                # Move file based on result: failed updates go to failed/ for retry
                if "BLOCKED" in result or "REJECTED" in result or "failed" in result.lower() or "ERROR" in result:
                    failed_dir = UPDATES_DIR / "failed"
                    failed_dir.mkdir(exist_ok=True)
                    update_file.rename(failed_dir / update_file.name)
                else:
                    processed_dir = UPDATES_DIR / "processed"
                    processed_dir.mkdir(exist_ok=True)
                    update_file.rename(processed_dir / update_file.name)
            except json.JSONDecodeError:
                self.log(f"Invalid JSON in {update_file.name}", "ERROR")
                results.append(f"{update_file.name}: INVALID JSON")
            except Exception as e:
                self.log(f"Update error ({update_file.name}): {e}", "ERROR")
                results.append(f"{update_file.name}: ERROR: {e}")

        return "\n".join(results) if results else "No updates applied."

    def _apply_legacy_updates(self, py_files: list) -> str:
        """Handle legacy .py file updates (direct file replacement)."""
        applied = []
        for update_file in py_files:
            target = BASE_DIR / update_file.name
            # Cross-edit check (Critic #24: also block TIMMY_PROTECTED)
            if update_file.name in DOCTOR_PROTECTED or update_file.name in TIMMY_PROTECTED:
                self.log(f"BLOCKED: cannot edit protected file {update_file.name}", "ERROR")
                continue
            try:
                shutil.copy2(update_file, target)
                update_file.unlink()
                applied.append(update_file.name)
                self.log(f"Applied legacy update: {update_file.name}")
            except Exception as e:
                self.log(f"Legacy update failed ({update_file.name}): {e}", "ERROR")

        if applied:
            self.restart_timmy()
            return f"Applied legacy: {', '.join(applied)}. Timmy restarted."
        return "No legacy updates applied."

    # ---- Evolution ----
    def get_evolution_status(self) -> str:
        try:
            from self_evolution import SelfEvolution
            evo = SelfEvolution()
            pending = evo.get_pending_improvements()
            if not pending:
                return "No pending improvements."
            lines = ["**Pending Improvements:**"]
            for imp in pending:
                lines.append(f"- #{imp['id']}: {imp['title']}")
            return "\n".join(lines)
        except Exception as e:
            return f"Evolution status error: {e}"

    def approve_improvement(self, imp_id: int) -> str:
        try:
            from self_evolution import SelfEvolution
            evo = SelfEvolution()
            result = evo.approve_improvement(imp_id)
            if result:
                return f"Approved improvement #{imp_id}: {result['title']}"
            return f"Improvement #{imp_id} not found."
        except Exception as e:
            return f"Error: {e}"

    # ---- Model switch ----
    def switch_model(self, model_name: str) -> str:
        payload = {
            "command": "switch_model",
            "model": model_name,
            "timestamp": datetime.now().isoformat(),
        }
        STATUS_FILE.write_text(json.dumps(payload))
        self.log(f"Model switch signal: {model_name}")
        return f"Sent model-switch signal for {model_name}"

    # ---- Status ----
    def get_status_text(self) -> str:
        running = self.is_timmy_running()
        pid = self.get_timmy_pid()
        config = _load_config()
        return (
            f"**Timmy:** {'Running' if running else 'Stopped'}  "
            f"(PID {pid or '---'})\n\n"
            f"**Restarts:** {self.restart_count} / {self.max_restarts}\n\n"
            f"**Doctor Model:** {config.get('doctor_model', 'N/A')}\n"
            f"**LLM Review:** {'Enabled' if config.get('doctor_llm_enabled') else 'Disabled'}\n"
            f"**Complexity Threshold:** {config.get('complexity_threshold', 10)}"
        )

    # ---- Doctor health (for display and Timmy mutual monitoring) ----
    def check_doctor_health(self) -> dict:
        """Return Doctor's health status including Timmy monitoring info.
        Note: Doctor monitors Timmy, not itself. Doctor cannot edit doctor.py."""
        timmy_running = self.is_timmy_running()
        timmy_pid = self.get_timmy_pid()
        return {
            "doctor_status": "online",
            "doctor_pid": os.getpid(),
            "timmy_status": "running" if timmy_running else "stopped",
            "timmy_pid": timmy_pid,
            "restart_count": self.restart_count,
            "monitoring_active": self.monitoring,
            "note": "Doctor monitors Timmy (5s checks, auto-restart). Doctor never edits its own files.",
            "timestamp": datetime.now().isoformat(),
        }

    # ---- Background monitor loop (Watchdog) ----
    def monitor_loop(self):
        """Main watchdog loop: check Timmy every 5s, process updates."""
        self.log("Monitor loop started (5s interval)")
        _last_restart_time = 0  # Critic #26: cooldown to prevent rapid restarts
        while self.monitoring:
            try:
                if not self.is_timmy_running():
                    now = time.time()
                    if now - _last_restart_time > 15:  # Wait 15s between restarts
                        self.log("Timmy not running -- auto-restarting", "ERROR")
                        time.sleep(1)
                        self.restart_timmy()
                        _last_restart_time = now
                        time.sleep(8)  # Give Timmy time to write PID file

                # Check for pending updates
                self.check_and_apply_updates()

                # Check for model switch signals
                if STATUS_FILE.exists():
                    try:
                        signal_data = json.loads(STATUS_FILE.read_text())
                        if signal_data.get("command") == "switch_model":
                            model = signal_data.get("model", "")
                            self.log(f"Processing model switch to {model}")
                            STATUS_FILE.unlink()
                    except Exception:
                        pass

            except Exception as e:
                self.log(f"Monitor loop error: {e}", "ERROR")

            time.sleep(5)

    # ---- Idle evolution trigger ----
    def idle_evolution_check(self):
        """Background thread: if Timmy is idle >5min, trigger self-evolution research."""
        idle_start = time.time()
        while self.monitoring:
            try:
                if self.is_timmy_running():
                    # Check if Timmy's agent is idle (no recent activity)
                    audit_log = BASE_DIR / "tim_audit.log"
                    if audit_log.exists():
                        try:
                            # Critic #27: Read only last 4KB instead of entire file
                            with open(audit_log, "rb") as f:
                                f.seek(0, 2)
                                size = f.tell()
                                f.seek(max(0, size - 4096))
                                tail = f.read().decode("utf-8", errors="ignore")
                            lines = [l for l in tail.strip().split("\n") if l.strip()]
                            if lines:
                                last_entry = json.loads(lines[-1])
                                last_ts = datetime.fromisoformat(last_entry.get("ts", "2000-01-01"))
                                idle_seconds = (datetime.now() - last_ts).total_seconds()
                                if idle_seconds > 300:  # 5 minutes idle
                                    self.log(f"Timmy idle for {idle_seconds:.0f}s -- suggesting evolution research")
                                    # Signal Timmy to start idle research by touching a flag file
                                    idle_flag = BASE_DIR / "memory" / ".idle_research_trigger"
                                    idle_flag.write_text(datetime.now().isoformat())
                        except Exception:
                            pass
            except Exception as e:
                self.log(f"Idle evolution check error: {e}", "WARNING")
            time.sleep(60)

    # ---- Resource monitoring ----
    def resource_monitor(self):
        """Background thread: monitor system resources, alert on high load with no progress."""
        while self.monitoring:
            try:
                import subprocess as sp
                # Get CPU usage
                cpu_proc = sp.run(
                    ["ps", "-A", "-o", "%cpu", "-r"],
                    capture_output=True, text=True, timeout=5,
                )
                if cpu_proc.stdout:
                    lines = cpu_proc.stdout.strip().split("\n")[1:6]  # Top 5
                    cpu_values = []
                    for line in lines:
                        try:
                            cpu_values.append(float(line.strip()))
                        except ValueError:
                            pass
                    total_top5 = sum(cpu_values)
                    if total_top5 > 300:  # >300% across top 5 processes
                        self.log(f"High CPU load detected: top-5 total {total_top5:.0f}%", "WARNING")

                # Get memory pressure
                mem_proc = sp.run(
                    ["memory_pressure"],
                    capture_output=True, text=True, timeout=5,
                )
                if "critical" in mem_proc.stdout.lower():
                    self.log("CRITICAL memory pressure detected!", "ERROR")
                elif "warn" in mem_proc.stdout.lower():
                    self.log("Memory pressure warning", "WARNING")

            except Exception:
                pass
            time.sleep(30)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
doctor = Doctor()
DOCTOR_PID_FILE.write_text(str(os.getpid()))

# Start idle evolution and resource monitoring threads
Thread(target=doctor.idle_evolution_check, daemon=True).start()
Thread(target=doctor.resource_monitor, daemon=True).start()

# ---------------------------------------------------------------------------
# Doctor Gradio UI
# ---------------------------------------------------------------------------
DOCTOR_CSS = """
body, .gradio-container { background-color: #111111 !important; }
.gradio-container { max-width: 900px !important; }
.status-card {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 12px;
    margin: 8px 0;
}
"""


def handle_command(message: str, history: list) -> tuple[list, str, str]:
    history = list(history or [])
    history.append({"role": "user", "content": message})

    cmd = message.strip().lower()

    if cmd in ("status", "check"):
        reply = doctor.get_status_text()
    elif cmd in ("restart", "restart timmy"):
        reply = doctor.restart_timmy()
    elif cmd in ("stop", "stop timmy"):
        reply = doctor.stop_timmy()
    elif cmd in ("start", "start timmy"):
        reply = doctor.start_timmy()
    elif cmd.startswith("switch "):
        reply = doctor.switch_model(message[7:].strip())
    elif cmd in ("logs", "log"):
        with doctor._lock:
            reply = "\n".join(doctor.log_entries[-30:]) or "No log entries."
    elif cmd in ("updates", "check updates"):
        reply = doctor.check_and_apply_updates()
    elif cmd in ("evolution", "evo", "improvements"):
        reply = doctor.get_evolution_status()
    elif cmd.startswith("approve "):
        try:
            imp_id = int(cmd.split()[-1])
            reply = doctor.approve_improvement(imp_id)
        except ValueError:
            reply = "Usage: `approve <number>`"
    elif cmd in ("health", "self"):
        health = doctor.check_doctor_health()
        reply = json.dumps(health, indent=2)
    elif cmd in ("config",):
        config = _load_config()
        reply = f"```json\n{json.dumps(config, indent=2)}\n```"
    elif cmd in ("help", "?"):
        reply = (
            "**Doctor Commands**\n"
            "- `status` -- check Timmy\n"
            "- `restart` -- restart Timmy\n"
            "- `stop` -- stop Timmy\n"
            "- `start` -- start Timmy\n"
            "- `switch <model>` -- switch Timmy's model\n"
            "- `logs` -- show recent log entries\n"
            "- `updates` -- check & apply updates\n"
            "- `evolution` -- show pending improvements\n"
            "- `approve <id>` -- approve an improvement\n"
            "- `health` -- Check Doctor's health & Timmy monitoring status\n"
            "- `config` -- show current config\n"
            "- `help` -- this message"
        )
    else:
        # For non-command messages, use LLM if enabled (emergency chat)
        config = _load_config()
        if config.get("doctor_llm_enabled"):
            try:
                model = config.get("doctor_model", "ollama/qwen3:30b").removeprefix("ollama/")
                host = config.get("ollama_host", "http://localhost:11434")
                resp = requests.post(
                    f"{host}/api/generate",
                    json={
                        "model": model,
                        "prompt": f"You are the Doctor, Timmy's supervisor. Ben says: {message}\nRespond helpfully and concisely.",
                        "stream": False,
                    },
                    timeout=60,
                )
                reply = resp.json().get("response", "No response from LLM.")
            except Exception as e:
                reply = f"LLM unavailable: {e}\nType `help` for commands."
        else:
            reply = f"Unknown command: `{message}`\nType `help` for options."

    history.append({"role": "assistant", "content": reply})
    return history, "", doctor.get_status_text()


DOCTOR_THEME = gr.themes.Soft(primary_hue="stone", neutral_hue="stone")

with gr.Blocks(title="LLTimmy Doctor") as doctor_app:

    gr.Markdown("# LLTimmy Doctor")
    gr.Markdown("*Pure-code supervisor with on-demand LLM boost*")
    status_md = gr.Markdown(doctor.get_status_text())

    chatbot = gr.Chatbot(height=400, show_label=False)
    msg_input = gr.Textbox(
        placeholder="Doctor command (type 'help') ...",
        show_label=False,
    )

    msg_input.submit(
        handle_command,
        inputs=[msg_input, chatbot],
        outputs=[chatbot, msg_input, status_md],
    )

    status_timer = gr.Timer(5)
    status_timer.tick(
        fn=lambda: doctor.get_status_text(), outputs=[status_md]
    )

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    monitor = Thread(target=doctor.monitor_loop, daemon=True)
    monitor.start()
    doctor.log("Doctor starting on port 7861")

    config = _load_config()

    doctor_app.launch(
        server_name="127.0.0.1",
        server_port=config.get("gradio_doctor_port", 7861),
        share=False,
        show_error=True,
        css=DOCTOR_CSS,
        theme=DOCTOR_THEME,
    )
