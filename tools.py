"""
Tools System for LLTimmy
All tools executed via the ReAct loop. Every call logged to tim_audit.log.
"""
import os
import json
import subprocess
import zipfile
import logging
import requests
import shutil
import base64
import re
from datetime import datetime, date
from typing import Dict, Tuple, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

CURRENT_YEAR = datetime.now().year

# ---------------------------------------------------------------------------
# Smart Risk Engine
# ---------------------------------------------------------------------------
class RiskEngine:
    BANNED_PATHS = ["/System", "/Library", "~/Library", "/usr"]

    HIGH_RISK = [
        (r"\brm\s+-rf\b", "Recursive force delete"),
        (r"\bsudo\s+rm\b", "Root-level delete"),
        (r"\bdd\s+if=", "Raw disk write"),
        (r"\bmkfs\b", "Filesystem format"),
        (r"\bfdisk\b", "Disk partition"),
        (r"\bshutdown\b", "System shutdown"),
        (r"\breboot\b", "System reboot"),
        (r"\bsudo\s+chmod\b", "Root permission change"),
        (r"\bsudo\s+chown\b", "Root ownership change"),
        (r"\brm\s+-r\s+/", "Delete from root"),
        (r"\bnewfs\b", "New filesystem"),
        (r"\bdiskutil\s+erase", "Disk erase"),
    ]

    MEDIUM_RISK = [
        (r"\bpip\s+install\b", "Package install"),
        (r"\bnpm\s+install\b", "NPM install"),
        (r"\bbrew\s+install\b", "Homebrew install"),
        (r"\bgit\s+push\b", "Git push"),
        (r"\bcurl\b.*\|\s*sh", "Pipe curl to shell"),
        (r"\bwget\b.*\|\s*sh", "Pipe wget to shell"),
    ]

    # NOTE: curl/wget removed from SAFE_PREFIXES to prevent curl|bash bypass (Critic #14)
    SAFE_PREFIXES = [
        "ls", "cat", "echo", "printf", "mkdir", "touch", "cp", "mv",
        "head", "tail", "wc", "grep", "find", "which", "pwd", "cd",
        "date", "whoami", "hostname", "uname", "env", "python",
        "python3", "node", "open", "pbcopy", "pbpaste", "tee",
        "ollama", "sort", "uniq", "tr", "cut",
        "dirname", "basename", "realpath", "readlink", "file",
        "diff", "less", "more", "strings", "xxd", "stat", "du",
        "df", "top", "ps", "kill", "lsof", "nslookup", "dig",
        "ping", "ssh", "scp", "git", "brew", "npm", "npx",
        "pip", "pip3", "cargo", "go", "ruby", "swift", "clang",
        "gcc", "make", "cmake", "java", "javac",
    ]

    # Commands that look scary in patterns but are safe in context
    SAFE_PIPE_COMMANDS = [
        "grep", "sort", "uniq", "wc", "head", "tail", "awk", "sed",
        "tr", "cut", "tee", "less", "more", "xargs", "jq",
    ]

    def classify_risk(self, command: str) -> Tuple[str, str]:
        cmd_stripped = command.strip()
        first_word = cmd_stripped.split()[0] if cmd_stripped.split() else ""

        # HIGH risk patterns checked FIRST (Critic #14: prevents safe-prefix bypass)
        for pattern, desc in self.HIGH_RISK:
            if re.search(pattern, command):
                return "high", f"Dangerous: {desc}"

        # MEDIUM risk patterns checked before safe prefix
        for pattern, desc in self.MEDIUM_RISK:
            if re.search(pattern, command):
                return "medium", f"Caution: {desc}"

        # Pipe-to-shell check BEFORE safe prefix (BUG-16: prevents cat|sh bypass)
        if "|" in command:
            if re.search(r'\|\s*(sh|bash|zsh|python|perl|ruby|node)\b', command):
                return "high", "Piped to shell/interpreter — dangerous."

        # Safe prefix check
        if first_word in self.SAFE_PREFIXES:
            if first_word == "cp" and re.search(r"\s+/System|\s+/Library", cmd_stripped):
                return "medium", "Copy to system directory."
            if first_word == "mv" and re.search(r"\s+/System|\s+/Library", cmd_stripped):
                return "high", "Move to system directory."
            return "low", "Safe command."

        # Piped commands: check if entire pipeline is safe
        if "|" in command:
            parts = [p.strip().split()[0] for p in command.split("|") if p.strip()]
            if all(p in self.SAFE_PREFIXES or p in self.SAFE_PIPE_COMMANDS for p in parts):
                return "low", "Safe piped command."

        # File read/write via safe patterns
        if re.match(r"^(echo|cat|printf|tee)\b", cmd_stripped):
            return "low", "File I/O via safe command."

        return "low", "Command appears safe."

    def check_banned_paths(self, command: str) -> Tuple[bool, str]:
        expanded_home = os.path.expanduser("~")
        for banned in self.BANNED_PATHS:
            expanded = banned.replace("~", expanded_home)
            if re.search(rf"(^|\s)(rm|mv|cp|chmod|chown|ln)\s.*{re.escape(expanded)}", command):
                return False, f"Banned path target: {banned}"
            if re.search(rf">\s*{re.escape(expanded)}", command):
                return False, f"Write to banned path: {banned}"
        return True, ""


# ---------------------------------------------------------------------------
# Source Evaluator
# ---------------------------------------------------------------------------
class SourceEvaluator:
    TRUSTED = {
        "docs.python.org", "developer.apple.com", "developer.mozilla.org",
        "stackoverflow.com", "github.com", "arxiv.org", "wikipedia.org",
        "docs.google.com", "learn.microsoft.com",
    }

    @classmethod
    def evaluate(cls, results: List[Dict]) -> List[Dict]:
        for r in results:
            score = 50
            url = r.get("url", "")
            parts = url.split("/")
            domain = parts[2] if len(parts) > 2 else ""
            if domain in cls.TRUSTED:
                score += 30
            if any(k in url.lower() for k in ("official", "docs", "documentation")):
                score += 10
            if domain.endswith((".edu", ".gov")):
                score += 20
            r["confidence"] = min(score, 100)
        results.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        if len(results) >= 3:
            snippets = [r.get("snippet", "").lower() for r in results[:3]]
            negations = sum(
                1 for s in snippets
                if any(w in s for w in ["not true", "myth", "incorrect", "debunked"])
            )
            if negations >= 2:
                for r in results:
                    r["bullshit_flag"] = True
        return results


# ---------------------------------------------------------------------------
# Tools System
# ---------------------------------------------------------------------------
class ToolsSystem:
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.risk_engine = RiskEngine()
        self.projects_dir = Path.home() / "LLTimmy" / "projects" / "sandbox"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log = Path.home() / "LLTimmy" / "tim_audit.log"
        # Resolve venv Python path dynamically (not hardcoded)
        _venv_path = Path(__file__).parent / ".venv" / "bin" / "python3"
        import sys
        self._venv_python = str(_venv_path) if _venv_path.exists() else sys.executable

    # Sensitive keys to redact from audit log (Critic #8)
    _SENSITIVE_KEYS = {"token", "password", "secret", "api_key", "authorization"}

    def log_tool_call(self, tool: str, params: Dict, result: str):
        # Redact sensitive params before logging
        safe_params = {}
        for k, v in params.items():
            if k.lower() in self._SENSITIVE_KEYS:
                safe_params[k] = "[REDACTED]"
            else:
                safe_params[k] = v
        entry = {
            "ts": datetime.now().isoformat(),
            "tool": tool,
            "params": safe_params,
            "result": result[:500],
        }
        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ---- terminal_command ------------------------------------------------
    async def terminal_command(self, command: str) -> Tuple[str, str]:
        safe, reason = self.risk_engine.check_banned_paths(command)
        if not safe:
            return "", f"BLOCKED: {reason}"

        level, explanation = self.risk_engine.classify_risk(command)

        if level == "high":
            return "", (
                f"HIGH RISK: {explanation}\n"
                f"Command: `{command}`\n"
                "Requires 3x YES confirmation."
            )
        if level == "medium":
            return "", (
                f"MEDIUM RISK: {explanation}\n"
                f"Command: `{command}`\n"
                "Please confirm (YES)."
            )

        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=120,
            )
            output = (proc.stdout + proc.stderr).strip()
            self.log_tool_call("terminal_command", {"command": command}, output)
            return output or "(completed, no output)", ""
        except subprocess.TimeoutExpired:
            return "", "Command timed out (120s)."
        except Exception as e:
            return "", f"ERROR: {e}"

    # ---- write_file (FIXED: check for directory, better error handling) ---
    async def write_file(self, path: str, content: str) -> Tuple[str, str]:
        """Write content to a file using Python (not shell). Always safe."""
        try:
            p = Path(os.path.expanduser(path))

            # FIXED: Check if the target is an existing directory
            if p.is_dir():
                return "", f"Write error: '{p}' is a directory, not a file. Provide a full file path including filename."

            # Ensure parent directory exists
            p.parent.mkdir(parents=True, exist_ok=True)

            # Check that parent is actually a directory
            if not p.parent.is_dir():
                return "", f"Write error: parent path '{p.parent}' is not a valid directory."

            p.write_text(content, encoding="utf-8")
            # Auto-verify
            if not p.exists():
                return "", f"Write error: file '{p}' was not created (verification failed)."
            size = p.stat().st_size
            self.log_tool_call("write_file", {"path": str(p)}, f"{size} bytes")
            return f"File written: {p} ({size} bytes)", ""
        except IsADirectoryError:
            return "", f"Write error: '{path}' is a directory. Provide a file path with filename."
        except PermissionError:
            return "", f"Write error: permission denied for '{path}'."
        except Exception as e:
            return "", f"Write error: {e}"

    # ---- read_file -------------------------------------------------------
    async def read_file(self, path: str) -> Tuple[str, str]:
        try:
            p = Path(os.path.expanduser(path))
            if not p.exists():
                return "", f"File not found: {p}"
            if p.is_dir():
                # List directory contents instead of failing
                entries = sorted(p.iterdir())
                listing = "\n".join(
                    f"{'[DIR] ' if e.is_dir() else ''}{e.name}"
                    for e in entries[:50]
                )
                return f"'{p}' is a directory. Contents:\n{listing}", ""
            content = p.read_text(encoding="utf-8")
            return content[:5000], ""
        except Exception as e:
            return "", f"Read error: {e}"

    # ---- web_search (fixed -- real results, current date) -----------------
    async def web_search(self, query: str, num_results: int = 5) -> Tuple[str, str]:
        results = []

        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=num_results))
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                    "source": "duckduckgo",
                }
                for r in raw
            ]
        except Exception as e:
            logger.warning(f"DDG library failed: {e}")

        if not results:
            try:
                results = self._fallback_ddg_search(query, num_results)
            except Exception as e:
                logger.warning(f"DDG fallback failed: {e}")

        if not results:
            try:
                results = self._fallback_google_search(query, num_results)
            except Exception as e:
                logger.warning(f"Google fallback failed: {e}")

        if not results:
            return "", f"All search methods failed for: {query}"

        results = SourceEvaluator.evaluate(results)
        self.log_tool_call("web_search", {"query": query}, f"{len(results)} results")
        return json.dumps(results, indent=2, ensure_ascii=False), ""

    def _fallback_ddg_search(self, query: str, n: int) -> List[Dict]:
        from bs4 import BeautifulSoup
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=15,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for r in soup.select(".result"):
            title_el = r.select_one(".result__title")
            snippet_el = r.select_one(".result__snippet")
            if title_el:
                url = ""
                a_tag = title_el.select_one("a")
                if a_tag and a_tag.get("href"):
                    url = a_tag["href"]
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": url,
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                    "source": "duckduckgo-fallback",
                })
        return results[:n]

    def _fallback_google_search(self, query: str, n: int) -> List[Dict]:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": query, "num": n},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=15,
        )
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for g in soup.select("div.g"):
            title_el = g.select_one("h3")
            link_el = g.select_one("a")
            snippet_el = g.select_one("div.VwiC3b")
            if title_el and link_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": link_el.get("href", ""),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                    "source": "google-fallback",
                })
        return results[:n]

    # ---- check_service_status (NEW: verify services before claiming) -----
    async def check_service_status(self, service: str, port: int = None) -> Tuple[str, str]:
        """Check if a service is actually running. Returns real status, never guess."""
        results = {}

        if service.lower() in ("doctor", "doctor_ui", "doctor ui"):
            port = port or 7861
            pid_file = Path("/tmp/doctor.pid")
            # Check PID
            pid_alive = False
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    pid_alive = True
                except (ProcessLookupError, ValueError, PermissionError, OSError):
                    pass
            # Check HTTP
            http_alive = False
            try:
                resp = requests.get(f"http://127.0.0.1:{port}/", timeout=3)
                http_alive = resp.status_code == 200
            except Exception:
                pass
            results = {
                "service": "Doctor",
                "pid_running": pid_alive,
                "http_responding": http_alive,
                "port": port,
                "status": "ONLINE" if (pid_alive and http_alive) else "OFFLINE",
            }

        elif service.lower() in ("timmy", "timmy_ui", "timmy ui"):
            port = port or 7860
            pid_file = Path("/tmp/timmy.pid")
            pid_alive = False
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    pid_alive = True
                except (ProcessLookupError, ValueError, PermissionError, OSError):
                    pass
            http_alive = False
            try:
                resp = requests.get(f"http://127.0.0.1:{port}/", timeout=3)
                http_alive = resp.status_code == 200
            except Exception:
                pass
            results = {
                "service": "Timmy",
                "pid_running": pid_alive,
                "http_responding": http_alive,
                "port": port,
                "status": "ONLINE" if (pid_alive and http_alive) else "OFFLINE",
            }

        elif service.lower() in ("ollama",):
            port = port or 11434
            try:
                resp = requests.get(f"http://localhost:{port}/api/tags", timeout=3)
                models = [m["name"] for m in resp.json().get("models", [])]
                results = {
                    "service": "Ollama",
                    "status": "ONLINE",
                    "port": port,
                    "models_available": models,
                }
            except Exception:
                results = {"service": "Ollama", "status": "OFFLINE", "port": port}

        else:
            # Generic port check
            if port:
                try:
                    resp = requests.get(f"http://127.0.0.1:{port}/", timeout=3)
                    results = {
                        "service": service,
                        "status": "ONLINE" if resp.status_code < 500 else "ERROR",
                        "port": port,
                        "http_status": resp.status_code,
                    }
                except Exception:
                    results = {"service": service, "status": "OFFLINE", "port": port}
            else:
                return "", f"Unknown service '{service}'. Provide a port number or use: doctor, timmy, ollama"

        self.log_tool_call("check_service_status", {"service": service, "port": port}, json.dumps(results))
        return json.dumps(results, indent=2), ""

    # ---- list_ollama_models (NEW: check local models before pulling) ------
    async def list_ollama_models(self) -> Tuple[str, str]:
        """List all models currently available in Ollama."""
        try:
            host = self.config.get("ollama_host", "http://localhost:11434")
            resp = requests.get(f"{host}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            model_list = []
            for m in models:
                name = m.get("name", "unknown")
                size_gb = m.get("size", 0) / (1024 ** 3)
                model_list.append({"name": name, "size_gb": round(size_gb, 1)})
            self.log_tool_call("list_ollama_models", {}, f"{len(model_list)} models")
            return json.dumps(model_list, indent=2), ""
        except Exception as e:
            return "", f"Cannot list Ollama models: {e}"

    # ---- manage_ollama_model (NEW: pull/remove models) -------------------
    async def manage_ollama_model(self, action: str, model_name: str) -> Tuple[str, str]:
        """Pull or remove an Ollama model."""
        host = self.config.get("ollama_host", "http://localhost:11434")
        if action == "pull":
            try:
                resp = requests.post(
                    f"{host}/api/pull",
                    json={"name": model_name, "stream": False},
                    timeout=600,
                )
                resp.raise_for_status()
                return f"Model '{model_name}' pulled successfully.", ""
            except Exception as e:
                return "", f"Failed to pull model '{model_name}': {e}"
        elif action == "remove":
            try:
                resp = requests.delete(
                    f"{host}/api/delete",
                    json={"name": model_name},
                    timeout=30,
                )
                resp.raise_for_status()
                return f"Model '{model_name}' removed.", ""
            except Exception as e:
                return "", f"Failed to remove model '{model_name}': {e}"
        else:
            return "", f"Unknown action '{action}'. Use 'pull' or 'remove'."

    # ---- playwright_browser ----------------------------------------------
    async def playwright_browser(self, url: str) -> Tuple[str, str]:
        # Try Playwright first, then fallback to requests+BS4
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await page.title()
            text = await page.inner_text("body")
            text = text[:5000]
            await browser.close()
            await pw.stop()
            result = f"Title: {title}\nURL: {url}\n\n{text}"
            self.log_tool_call("playwright_browser", {"url": url}, result[:300])
            return result, ""
        except Exception as pw_err:
            logger.warning(f"Playwright failed ({pw_err}), trying requests fallback")
            # Fallback: requests + BeautifulSoup
            try:
                from bs4 import BeautifulSoup
                resp = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                    timeout=15,
                )
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                # Remove script/style elements
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                title = soup.title.string if soup.title else "No title"
                text = soup.get_text(separator="\n", strip=True)[:5000]
                result = f"Title: {title}\nURL: {url}\n\n{text}"
                self.log_tool_call("playwright_browser", {"url": url}, result[:300])
                return result, ""
            except Exception as req_err:
                return "", f"Browser error: Playwright({pw_err}), Requests({req_err})"

    # ---- download_url ----------------------------------------------------
    async def download_url(self, url: str, dest_dir: str = None) -> Tuple[str, str]:
        try:
            dest = Path(dest_dir) if dest_dir else Path.home() / "Downloads" / "LLTimmy_Projects"
            dest.mkdir(parents=True, exist_ok=True)
            filename = Path(url).name or f"download_{datetime.now():%Y%m%d%H%M%S}"
            dest_path = dest / filename
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            self.log_tool_call("download_url", {"url": url}, str(dest_path))
            return f"Downloaded -> {dest_path} ({dest_path.stat().st_size} bytes)", ""
        except Exception as e:
            return "", f"Download error: {e}"

    # ---- extract_zip (FIXED: Zip Slip protection — Critic #13) ----------
    async def extract_zip(self, zip_path: str, dest_dir: str = None) -> Tuple[str, str]:
        try:
            dest = Path(dest_dir) if dest_dir else self.projects_dir
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Check for path traversal attacks before extracting
                for member in zf.infolist():
                    member_path = (dest / member.filename).resolve()
                    if not str(member_path).startswith(str(dest.resolve())):
                        return "", f"BLOCKED: Zip path traversal detected in '{member.filename}'. Extraction aborted."
                zf.extractall(dest)
            return f"Extracted -> {dest}", ""
        except Exception as e:
            return "", f"Extract error: {e}"

    # ---- run_blender -----------------------------------------------------
    async def run_blender(self, command: str, gui: bool = False) -> Tuple[str, str]:
        blender = shutil.which("blender")
        if not blender:
            mac_path = "/Applications/Blender.app/Contents/MacOS/Blender"
            if os.path.exists(mac_path):
                blender = mac_path
            else:
                return "", "Blender not found. Install from blender.org or `brew install --cask blender`."

        try:
            if gui:
                subprocess.Popen(["open", "-a", "Blender"])
                return "Blender opened (GUI mode).", ""
            proc = subprocess.run(
                f'"{blender}" {command}',
                shell=True, capture_output=True, text=True, timeout=120,
            )
            output = (proc.stdout + proc.stderr).strip()
            return output or "(completed)", ""
        except Exception as e:
            return "", f"Blender error: {e}"

    # ---- run_applescript -------------------------------------------------
    async def run_applescript(self, script: str) -> Tuple[str, str]:
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=30,
            )
            output = (proc.stdout + proc.stderr).strip()
            if proc.returncode != 0 and not output:
                return "", f"AppleScript returned exit code {proc.returncode}"
            if proc.returncode != 0:
                return "", f"AppleScript error: {output}"
            return output or "(completed)", ""
        except subprocess.TimeoutExpired:
            return "", "AppleScript timed out (30s)."
        except Exception as e:
            return "", f"AppleScript error: {e}"

    # ---- run_comfyui_workflow (FULL: queue, poll, retrieve images) --------
    async def run_comfyui_workflow(self, workflow_id: str = None,
                                    workflow_file: str = None,
                                    workflow_json: dict = None,
                                    poll_timeout: int = 120) -> Tuple[str, str]:
        """Submit a ComfyUI workflow and retrieve output image paths."""
        import time as _time
        host = "http://localhost:8188"

        # 1. Check ComfyUI is alive
        try:
            requests.get(f"{host}/system_stats", timeout=5)
        except requests.ConnectionError:
            return "", "ComfyUI not running on localhost:8188. Start it first."
        except Exception as e:
            return "", f"ComfyUI error: {e}"

        # 2. Load workflow
        payload = None
        if workflow_json and isinstance(workflow_json, dict):
            payload = workflow_json
        elif workflow_file:
            p = Path(os.path.expanduser(workflow_file))
            if not p.exists():
                return "", f"Workflow file not found: {p}"
            try:
                payload = json.loads(p.read_text())
            except json.JSONDecodeError as e:
                return "", f"Invalid JSON in workflow file: {e}"
        elif workflow_id:
            # Search common workflow directories
            search_paths = [
                Path.home() / "ComfyUI" / "workflows" / f"{workflow_id}.json",
                Path.home() / "ComfyUI" / "user" / "default" / "workflows" / f"{workflow_id}.json",
                Path.home() / "Downloads" / f"{workflow_id}.json",
                Path.home() / "Desktop" / f"{workflow_id}.json",
            ]
            for sp in search_paths:
                if sp.exists():
                    try:
                        payload = json.loads(sp.read_text())
                    except json.JSONDecodeError:
                        continue
                    break
            if payload is None:
                return "", f"Workflow '{workflow_id}' not found. Searched: {', '.join(str(s.parent) for s in search_paths)}"
        else:
            return "", "Provide workflow_file, workflow_json, or workflow_id."

        # 3. Queue the prompt
        try:
            resp = requests.post(f"{host}/prompt", json={"prompt": payload}, timeout=15)
            if resp.status_code != 200:
                return "", f"ComfyUI rejected workflow (HTTP {resp.status_code}): {resp.text[:300]}"
            prompt_id = resp.json().get("prompt_id")
            if not prompt_id:
                return "", f"No prompt_id in ComfyUI response: {resp.text[:300]}"
        except Exception as e:
            return "", f"ComfyUI queue error: {e}"

        # 4. Poll for completion
        deadline = _time.time() + poll_timeout
        while _time.time() < deadline:
            try:
                hist = requests.get(f"{host}/history/{prompt_id}", timeout=10).json()
                if prompt_id in hist:
                    outputs = hist[prompt_id].get("outputs", {})
                    image_paths = []
                    for node_id, node_out in outputs.items():
                        for img in node_out.get("images", []):
                            fname = img.get("filename", "")
                            subfolder = img.get("subfolder", "")
                            out_dir = Path.home() / "ComfyUI" / "output"
                            if subfolder:
                                out_dir = out_dir / subfolder
                            full_path = out_dir / fname
                            if full_path.exists():
                                image_paths.append(str(full_path))
                    self.log_tool_call("run_comfyui_workflow",
                                       {"prompt_id": prompt_id}, str(image_paths))
                    if image_paths:
                        return f"Workflow complete. Images:\n" + "\n".join(image_paths), ""
                    return f"Workflow complete (prompt_id={prompt_id}), no image outputs found.", ""
            except Exception:
                pass
            _time.sleep(2)

        return "", f"ComfyUI timed out after {poll_timeout}s. prompt_id={prompt_id}"

    # ---- open_application (IMPROVED: proper macOS paths + verification) --
    async def open_application(self, app_name: str, foreground: bool = True) -> Tuple[str, str]:
        """Open a macOS application. Tries multiple path strategies."""
        try:
            # Strategy 1: Direct open -a (most reliable)
            cmd = ["open", "-a", app_name]
            if not foreground:
                cmd.append("-g")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            if proc.returncode == 0:
                self.log_tool_call("open_application", {"app": app_name}, "opened")
                return f"Opened {app_name}", ""

            # Strategy 2: Try common path variants
            name_variants = [
                app_name,
                app_name.replace(" ", ""),
                app_name.replace(" ", "-"),
                app_name.title(),
                app_name.title().replace(" ", ""),
            ]
            search_dirs = ["/Applications", os.path.expanduser("~/Applications")]

            for search_dir in search_dirs:
                for variant in name_variants:
                    app_path = f"{search_dir}/{variant}.app"
                    if os.path.exists(app_path):
                        proc = subprocess.run(
                            ["open", app_path] + (["-g"] if not foreground else []),
                            capture_output=True, text=True, timeout=15,
                        )
                        if proc.returncode == 0:
                            self.log_tool_call("open_application", {"app": app_name, "path": app_path}, "opened")
                            return f"Opened {app_name} ({app_path})", ""

            # Strategy 3: Use mdfind to locate the app
            try:
                find_proc = subprocess.run(
                    ["mdfind", "kMDItemKind == 'Application' && kMDItemDisplayName == '{}'".format(
                        app_name.replace("'", "\\'"))],
                    capture_output=True, text=True, timeout=10,
                )
                if find_proc.stdout.strip():
                    app_path = find_proc.stdout.strip().split("\n")[0]
                    proc = subprocess.run(
                        ["open", app_path] + (["-g"] if not foreground else []),
                        capture_output=True, text=True, timeout=15,
                    )
                    if proc.returncode == 0:
                        self.log_tool_call("open_application", {"app": app_name, "path": app_path}, "opened via mdfind")
                        return f"Opened {app_name} ({app_path})", ""
            except Exception:
                pass

            # Strategy 4: Check if it's a direct executable path
            if os.path.exists(app_name):
                proc = subprocess.run(
                    ["open", app_name] + (["-g"] if not foreground else []),
                    capture_output=True, text=True, timeout=15,
                )
                if proc.returncode == 0:
                    return f"Opened {app_name}", ""

            # All strategies failed
            available = []
            for search_dir in search_dirs:
                if os.path.exists(search_dir):
                    apps = [f.replace(".app", "") for f in os.listdir(search_dir) if f.endswith(".app")]
                    matches = [a for a in apps if app_name.lower() in a.lower()]
                    available.extend(matches[:5])

            hint = f" Similar apps found: {', '.join(available)}" if available else ""
            return "", f"Failed to open '{app_name}'.{hint}"
        except Exception as e:
            return "", f"Open error: {e}"

    # ---- github_operations -----------------------------------------------
    async def github_operations(self, action: str, repo_name: str = None, token: str = None) -> Tuple[str, str]:
        if not repo_name:
            return "", "repo_name is required."
        try:
            if action == "create":
                headers = {}
                if token:
                    headers["Authorization"] = f"token {token}"
                resp = requests.post(
                    "https://api.github.com/user/repos",
                    json={"name": repo_name, "private": True},
                    headers=headers, timeout=15,
                )
                resp.raise_for_status()
                return f"Created private repo: {repo_name}", ""
            elif action == "clone":
                dest = self.projects_dir / repo_name
                if dest.exists():
                    return "", f"Already exists: {dest}"
                user = os.environ.get("GITHUB_USERNAME", "bengur")
                subprocess.run(
                    ["git", "clone", f"https://github.com/{user}/{repo_name}.git", str(dest)],
                    check=True, capture_output=True, text=True,
                )
                return f"Cloned -> {dest}", ""
            elif action == "push":
                repo_dir = self.projects_dir / repo_name
                if not (repo_dir / ".git").exists():
                    return "", f"{repo_name} is not a git repo."
                subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
                subprocess.run(
                    ["git", "-C", str(repo_dir), "commit", "-m", "Update from LLTimmy"],
                    check=True, capture_output=True, text=True,
                )
                subprocess.run(
                    ["git", "-C", str(repo_dir), "push"],
                    check=True, capture_output=True, text=True,
                )
                return f"Pushed {repo_name}", ""
            else:
                return "", f"Unknown action: {action}"
        except subprocess.CalledProcessError as e:
            return "", f"Git error: {e.stderr or e}"
        except Exception as e:
            return "", f"GitHub error: {e}"

    # ---- da_vinci_resolve_script -----------------------------------------
    async def da_vinci_resolve_script(self, script: str) -> Tuple[str, str]:
        try:
            import importlib
            dvr = importlib.import_module("DaVinciResolveScript")
            resolve = dvr.scriptapp("Resolve")
            if resolve is None:
                return "", "DaVinci Resolve is not running."
            # Safety: reject scripts with sandbox-escape tokens
            # Substring checks for dunder and class introspection
            BANNED_SUBSTR = ("__", "subprocess", " os.", "builtins", "subclasses", "mro")
            # Word-boundary checks to reduce false positives (e.g. "imported")
            BANNED_WORDS = (r'\bimport\b', r'\bexec\b', r'\beval\b', r'\bopen\b',
                            r'\bgetattr\b', r'\bsetattr\b', r'\bdelattr\b',
                            r'\bglobals\b', r'\blocals\b', r'\bcompile\b',
                            r'\bvars\b', r'\btype\b', r'\bbases\b')
            if any(tok in script for tok in BANNED_SUBSTR):
                return "", "Script contains unsafe tokens — rejected."
            if any(re.search(pat, script) for pat in BANNED_WORDS):
                return "", "Script contains unsafe tokens — rejected."
            # Restricted evaluation — builtins disabled, only `resolve` in scope
            result = eval(script, {"__builtins__": {}, "resolve": resolve})  # noqa: S307  # nosec
            return str(result), ""
        except ImportError:
            return "", "DaVinci Resolve scripting module not found."
        except Exception as e:
            return "", f"Resolve error: {e}"

    # ---- create_tool (FIXED: sanitize name, safe syntax check — Critic #17)
    async def create_tool(self, name: str, code: str) -> Tuple[str, str]:
        """Write a new tool to sandbox for testing before integration."""
        try:
            # Sanitize tool name to prevent path injection
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '', name)
            if not safe_name:
                return "", "Invalid tool name. Use only alphanumeric characters and underscores."
            tool_path = self.projects_dir / f"tool_{safe_name}.py"
            tool_path.write_text(code, encoding="utf-8")
            # Use py_compile for safe syntax checking (no string embedding)
            proc = subprocess.run(
                [self._venv_python, "-m", "py_compile", str(tool_path)],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return "", f"Syntax error in new tool:\n{proc.stderr}"
            self.log_tool_call("create_tool", {"name": name}, "created")
            return f"Tool '{name}' created at {tool_path}. Syntax OK. Propose to Doctor for integration.", ""
        except Exception as e:
            return "", f"Create tool error: {e}"

    # ---- send_notification (FIXED: escape AppleScript strings — Critic #5) --
    async def send_notification(self, title: str, message: str, sound: bool = True) -> Tuple[str, str]:
        """Send a macOS notification via osascript."""
        try:
            # Escape quotes and backslashes for AppleScript safety
            safe_title = title.replace('\\', '\\\\').replace('"', '\\"')
            safe_msg = message.replace('\\', '\\\\').replace('"', '\\"')
            sound_str = 'sound name "Funk"' if sound else ""
            script = f'display notification "{safe_msg}" with title "{safe_title}" {sound_str}'
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return "", f"Notification error: {proc.stderr}"
            return f"Notification sent: {title}", ""
        except Exception as e:
            return "", f"Notification error: {e}"

    # ---- read_clipboard ---------------------------------------------------
    async def read_clipboard(self) -> Tuple[str, str]:
        """Read the current macOS clipboard contents."""
        try:
            proc = subprocess.run(
                ["pbpaste"], capture_output=True, timeout=5,
            )
            content = proc.stdout.decode("utf-8", errors="replace")
            self.log_tool_call("read_clipboard", {}, f"({len(content)} chars read)")
            return content[:8000] or "(clipboard is empty)", ""
        except Exception as e:
            return "", f"Clipboard read error: {e}"

    # ---- write_clipboard --------------------------------------------------
    async def write_clipboard(self, content: str) -> Tuple[str, str]:
        """Write content to the macOS clipboard."""
        try:
            proc = subprocess.run(
                ["pbcopy"], input=content.encode("utf-8"),
                capture_output=True, timeout=5,
            )
            if proc.returncode != 0:
                return "", f"Clipboard write error: {proc.stderr.decode()}"
            self.log_tool_call("write_clipboard", {"length": len(content)}, "ok")
            return f"Copied to clipboard ({len(content)} chars)", ""
        except Exception as e:
            return "", f"Clipboard write error: {e}"

    # ---- scaffold_project -------------------------------------------------
    _SCAFFOLD_TEMPLATES = {
        "blender_addon": {
            "__init__.py": (
                'bl_info = {{\n'
                '    "name": "{name}",\n'
                '    "blender": (4, 0, 0),\n'
                '    "category": "Object",\n'
                '    "version": (1, 0, 0),\n'
                '    "author": "Ben",\n'
                '    "description": "Generated by LLTimmy",\n'
                '}}\n\n'
                'import bpy\n\n'
                'class {class_name}Operator(bpy.types.Operator):\n'
                '    bl_idname = "object.{snake_name}"\n'
                '    bl_label = "{name}"\n\n'
                '    def execute(self, context):\n'
                '        return {{"FINISHED"}}\n\n'
                'def register():\n'
                '    bpy.utils.register_class({class_name}Operator)\n\n'
                'def unregister():\n'
                '    bpy.utils.unregister_class({class_name}Operator)\n'
            ),
        },
        "blender_script": {
            "script.py": (
                'import bpy\n\n'
                '# {name}\n'
                'scene = bpy.context.scene\n\n'
                '# Your script logic here\n'
            ),
        },
        "comfyui_node": {
            "{snake_name}.py": (
                'class {class_name}:\n'
                '    @classmethod\n'
                '    def INPUT_TYPES(cls):\n'
                '        return {{"required": {{"image": ("IMAGE",)}}}}\n\n'
                '    RETURN_TYPES = ("IMAGE",)\n'
                '    FUNCTION = "process"\n'
                '    CATEGORY = "LLTimmy"\n\n'
                '    def process(self, image):\n'
                '        return (image,)\n\n'
                'NODE_CLASS_MAPPINGS = {{"{class_name}": {class_name}}}\n'
                'NODE_DISPLAY_NAME_MAPPINGS = {{"{class_name}": "{name}"}}\n'
            ),
        },
        "website": {
            "index.html": (
                '<!DOCTYPE html>\n'
                '<html lang="en">\n'
                '<head>\n'
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                '  <title>{name}</title>\n'
                '  <link rel="stylesheet" href="style.css">\n'
                '</head>\n'
                '<body>\n'
                '  <h1>{name}</h1>\n'
                '  <script src="main.js"></script>\n'
                '</body>\n'
                '</html>'
            ),
            "style.css": "* {{ box-sizing: border-box; margin: 0; padding: 0; }}\nbody {{ font-family: system-ui, sans-serif; }}",
            "main.js": "// {name}\nconsole.log('{name} loaded');",
        },
        "python_package": {
            "{snake_name}/__init__.py": '"""{name} package."""\n__version__ = "0.1.0"\n',
            "{snake_name}/core.py": "# Core logic for {name}\n",
            "tests/__init__.py": "",
            "tests/test_core.py": "from {snake_name} import core\n\ndef test_placeholder():\n    pass\n",
            "pyproject.toml": '[project]\nname = "{snake_name}"\nversion = "0.1.0"\n',
        },
        "react_app": {
            "index.html": (
                '<!DOCTYPE html>\n<html>\n<head><meta charset="UTF-8">'
                '<title>{name}</title></head>\n'
                '<body><div id="root"></div>'
                '<script type="module" src="/src/main.jsx"></script>'
                '</body>\n</html>'
            ),
            "src/main.jsx": (
                "import React from 'react';\n"
                "import ReactDOM from 'react-dom/client';\n"
                "import App from './App';\n"
                "ReactDOM.createRoot(document.getElementById('root')).render(<App />);\n"
            ),
            "src/App.jsx": "export default function App() {{\n  return <h1>{name}</h1>;\n}}\n",
            "package.json": (
                '{{\n  "name": "{snake_name}",\n'
                '  "scripts": {{"dev": "vite", "build": "vite build"}},\n'
                '  "dependencies": {{"react": "^18", "react-dom": "^18"}},\n'
                '  "devDependencies": {{"vite": "^5", "@vitejs/plugin-react": "^4"}}\n}}\n'
            ),
            "vite.config.js": "import {{ defineConfig }} from 'vite';\nimport react from '@vitejs/plugin-react';\nexport default defineConfig({{ plugins: [react()] }});\n",
        },
        "nextjs_app": {
            "app/page.tsx": (
                "export default function Home() {{\n"
                "  return <main><h1>{name}</h1></main>;\n"
                "}}\n"
            ),
            "app/layout.tsx": (
                "export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{\n"
                "  return <html lang='en'><body>{{children}}</body></html>;\n"
                "}}\n"
            ),
            "package.json": (
                '{{\n  "name": "{snake_name}",\n'
                '  "scripts": {{"dev": "next dev", "build": "next build"}},\n'
                '  "dependencies": {{"next": "^14", "react": "^18", "react-dom": "^18"}}\n}}\n'
            ),
            "tsconfig.json": '{{\n  "compilerOptions": {{"target": "es5", "lib": ["dom"], "jsx": "preserve", "strict": true}}\n}}\n',
        },
    }

    async def scaffold_project(self, project_type: str, name: str,
                                dest_dir: str = None) -> Tuple[str, str]:
        """Create a complete project scaffold from a template."""
        pt = project_type.lower().replace("-", "_").replace(" ", "_")
        if pt not in self._SCAFFOLD_TEMPLATES:
            available = ", ".join(self._SCAFFOLD_TEMPLATES.keys())
            return "", f"Unknown project type '{project_type}'. Available: {available}"

        snake_name = re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_') or "project"
        class_name = "".join(w.capitalize() for w in snake_name.split('_'))

        template = self._SCAFFOLD_TEMPLATES[pt]
        dest = Path(os.path.expanduser(dest_dir)).resolve() if dest_dir else (self.projects_dir / snake_name)
        # Safety: block system paths
        dest_str = str(dest)
        for banned in self.risk_engine.BANNED_PATHS:
            if dest_str.startswith(os.path.expanduser(banned)):
                return "", f"BLOCKED: Cannot scaffold into {banned}"
        dest.mkdir(parents=True, exist_ok=True)

        created_files = []
        for rel_path_tpl, content_tpl in template.items():
            rel_path = rel_path_tpl.format(snake_name=snake_name, class_name=class_name, name=name)
            content = content_tpl.format(name=name, snake_name=snake_name, class_name=class_name)
            file_path = dest / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            created_files.append(str(file_path.relative_to(dest)))

        self.log_tool_call("scaffold_project", {"type": pt, "name": name}, str(dest))
        manifest = "\n".join(f"  {f}" for f in sorted(created_files))
        return f"Project '{name}' scaffolded at {dest}:\n{manifest}", ""

    # ---- streaming terminal -----------------------------------------------
    async def terminal_command_stream(self, command: str,
                                       stream_callback=None,
                                       timeout: int = 300) -> Tuple[str, str]:
        """Run a long command with live output streaming."""
        import asyncio

        safe, reason = self.risk_engine.check_banned_paths(command)
        if not safe:
            return "", f"BLOCKED: {reason}"
        level, explanation = self.risk_engine.classify_risk(command)
        if level == "high":
            return "", f"HIGH RISK: {explanation}. Refusing to execute."
        if level == "medium":
            # Allow package installs through streaming (npm/pip/brew) — they're safe and long-running
            if re.search(r'\b(pip|npm|brew)\s+install\b', command):
                pass  # Allow through — streaming install is the ideal use case
            else:
                return "", f"MEDIUM RISK: {explanation}. Use terminal_command if you need to confirm."

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        lines = []
        try:
            async def _read_all():
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    lines.append(line)
                    if stream_callback:
                        try:
                            stream_callback(line)
                        except Exception:
                            pass
                await proc.wait()
            await asyncio.wait_for(_read_all(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return "\n".join(lines[-50:]), f"Timed out after {timeout}s"

        output = "\n".join(lines)
        self.log_tool_call("terminal_command_stream", {"command": command}, output[-500:])
        exit_code = proc.returncode
        if exit_code != 0:
            return output[-10000:] or "(no output)", f"Exit code {exit_code}"
        return output[-10000:] or "(completed, no output)", ""

    # ---- capture_screenshot (NEW: UI diagnostic tool) --------------------
    async def capture_screenshot(self, target: str = "desktop", save_path: str = None) -> Tuple[str, str]:
        """Capture a screenshot for diagnostic purposes.

        Args:
            target: "desktop" for full screen, "timmy" for Timmy UI (port 7860),
                    or a URL/window name.
            save_path: Optional path to save screenshot. Default: ~/LLTimmy/screenshots/
        """
        try:
            screenshots_dir = Path.home() / "LLTimmy" / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = save_path or str(screenshots_dir / f"screenshot_{timestamp}.png")

            if target == "desktop":
                # macOS screencapture for full desktop
                proc = subprocess.run(
                    ["screencapture", "-x", filename],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode != 0:
                    return "", f"Screenshot error: {proc.stderr}"
                return f"Desktop screenshot saved to {filename}", ""

            elif target in ("timmy", "doctor"):
                port = 7860 if target == "timmy" else 7861
                url = f"http://127.0.0.1:{port}"
                # Try Playwright (FIXED: pass URL/path via env vars, not string embedding — Critic #6)
                try:
                    pw_script = (
                        "import asyncio, sys, os\n"
                        "from playwright.async_api import async_playwright\n"
                        "async def shot():\n"
                        "    url = os.environ['PW_URL']\n"
                        "    dest = os.environ['PW_DEST']\n"
                        "    async with async_playwright() as p:\n"
                        "        browser = await p.chromium.launch(headless=True)\n"
                        "        page = await browser.new_page(viewport={'width': 1280, 'height': 900})\n"
                        "        await page.goto(url, wait_until='networkidle', timeout=15000)\n"
                        "        await page.wait_for_timeout(2000)\n"
                        "        await page.screenshot(path=dest, full_page=True)\n"
                        "        await browser.close()\n"
                        "asyncio.run(shot())\n"
                    )
                    env = {**os.environ, "PW_URL": url, "PW_DEST": filename}
                    proc = subprocess.run(
                        [self._venv_python, "-c", pw_script],
                        capture_output=True, text=True, timeout=30, env=env,
                    )
                    if proc.returncode == 0:
                        return f"UI screenshot of {target} saved to {filename}", ""
                    logger.warning(f"Playwright screenshot failed: {proc.stderr[:200]}")
                except Exception as e:
                    logger.warning(f"Playwright unavailable: {e}")

                # Fallback: capture desktop
                proc = subprocess.run(
                    ["screencapture", "-x", filename],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    return f"Desktop screenshot saved to {filename} (Playwright unavailable for direct UI capture)", ""
                return "", f"Screenshot failed: {proc.stderr}"
            else:
                # Treat target as a URL (FIXED: pass via env vars — Critic #6)
                try:
                    pw_script = (
                        "import asyncio, os\n"
                        "from playwright.async_api import async_playwright\n"
                        "async def shot():\n"
                        "    url = os.environ['PW_URL']\n"
                        "    dest = os.environ['PW_DEST']\n"
                        "    async with async_playwright() as p:\n"
                        "        browser = await p.chromium.launch(headless=True)\n"
                        "        page = await browser.new_page(viewport={'width': 1280, 'height': 900})\n"
                        "        await page.goto(url, wait_until='networkidle', timeout=15000)\n"
                        "        await page.wait_for_timeout(2000)\n"
                        "        await page.screenshot(path=dest, full_page=True)\n"
                        "        await browser.close()\n"
                        "asyncio.run(shot())\n"
                    )
                    env = {**os.environ, "PW_URL": target, "PW_DEST": filename}
                    proc = subprocess.run(
                        [self._venv_python, "-c", pw_script],
                        capture_output=True, text=True, timeout=30, env=env,
                    )
                    if proc.returncode == 0:
                        return f"Screenshot of {target} saved to {filename}", ""
                    return "", f"Screenshot error: {proc.stderr[:300]}"
                except Exception as e:
                    return "", f"Screenshot error: {e}"

        except Exception as e:
            return "", f"Screenshot error: {e}"
