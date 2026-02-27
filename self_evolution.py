"""
Self-Evolution Engine for LLTimmy
Runs at midnight: reviews past 24h, suggests improvements, can auto-apply small changes.
Also provides on-demand self-analysis, capability gap detection,
and proactive idle-time research for self-improvement.
"""
import json
import logging
import re
import time
import threading
import requests
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path.home() / "LLTimmy"
MEMORY_BASE = BASE_DIR / "memory"
EVOLUTION_DIR = MEMORY_BASE / "evolution"
CONFIG_PATH = BASE_DIR / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


class SelfEvolution:
    """Analyzes agent performance, proposes improvements, and proactively
    researches new capabilities during idle time."""

    def __init__(self):
        EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
        self.reviews_file = EVOLUTION_DIR / "daily_reviews.json"
        self.capabilities_file = EVOLUTION_DIR / "capabilities.json"
        self.improvements_file = EVOLUTION_DIR / "improvements.json"
        self.ideas_file = EVOLUTION_DIR / "ideas.json"
        self._load()
        self._idle_thread = None
        self._idle_running = False

    def _load(self):
        self.reviews: List[Dict] = self._read_json(self.reviews_file, [])
        self.capabilities: Dict = self._read_json(self.capabilities_file, {
            "confirmed": [],
            "gaps": [],
            "requested": [],
        })
        self.improvements: List[Dict] = self._read_json(self.improvements_file, [])
        self.ideas: List[Dict] = self._read_json(self.ideas_file, [])

    @staticmethod
    def _read_json(path: Path, default):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return default

    def _save_reviews(self):
        self.reviews_file.write_text(
            json.dumps(self.reviews[-365:], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_capabilities(self):
        self.capabilities_file.write_text(
            json.dumps(self.capabilities, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_improvements(self):
        self.improvements_file.write_text(
            json.dumps(self.improvements[-100:], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_ideas(self):
        self.ideas_file.write_text(
            json.dumps(self.ideas[-200:], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---- Daily Review ----
    def create_daily_review(self) -> Dict:
        """Analyze yesterday's interactions and create a performance review."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        chat_file = MEMORY_BASE / "raw_chats" / f"{yesterday}.json"

        review = {
            "date": yesterday,
            "created_at": datetime.now().isoformat(),
            "stats": {},
            "patterns": [],
            "issues": [],
            "suggestions": [],
        }

        if not chat_file.exists():
            review["stats"]["message_count"] = 0
            review["notes"] = "No chat data for this day."
            self.reviews.append(review)
            self._save_reviews()
            return review

        try:
            messages = json.loads(chat_file.read_text(encoding="utf-8"))
        except Exception:
            messages = []

        user_msgs = [m for m in messages if m.get("role") == "user"]
        asst_msgs = [m for m in messages if m.get("role") == "assistant"]

        review["stats"] = {
            "total_messages": len(messages),
            "user_messages": len(user_msgs),
            "assistant_messages": len(asst_msgs),
        }

        tool_calls = 0
        errors = 0
        cant_phrases = 0

        for msg in asst_msgs:
            content = msg.get("content", "")
            tool_calls += len(re.findall(r"Action:\s*\w+", content))
            errors += len(re.findall(r"(?i)(error|failed|exception)", content))
            cant_phrases += len(re.findall(
                r"(?i)(i can'?t|i'?m unable|i don'?t have the ability|as a text.based)",
                content
            ))

        review["stats"]["tool_calls"] = tool_calls
        review["stats"]["errors"] = errors
        review["stats"]["cant_violations"] = cant_phrases

        if cant_phrases > 0:
            review["issues"].append(
                f"Said 'I can't' or similar {cant_phrases} times. "
                "Must research solutions instead."
            )
            review["suggestions"].append(
                "Strengthen never-say-can't rule. When lacking capability, "
                "immediately web_search for how to add it."
            )

        if errors > 3:
            review["issues"].append(
                f"High error count ({errors}). Review error handling."
            )
            review["suggestions"].append(
                "Add better error recovery. Consider pre-checking tool availability."
            )

        error_rate = errors / max(tool_calls, 1)
        if error_rate > 0.3:
            review["suggestions"].append(
                f"Tool error rate is {error_rate:.0%}. "
                "Consider adding validation before tool calls."
            )

        # Detect capability gaps from user requests
        for msg in user_msgs:
            content = msg.get("content", "").lower()
            if any(w in content for w in ["image", "photo", "picture", "screenshot"]):
                if "vision" not in str(self.capabilities.get("confirmed", [])):
                    self._add_gap("vision", "User requested image analysis")
            if any(w in content for w in ["audio", "voice", "speak", "listen"]):
                self._add_gap("audio", "User requested audio capability")
            if any(w in content for w in ["schedule", "cron", "timer", "remind"]):
                self._add_gap("scheduling", "User wanted scheduled tasks")
            if any(w in content for w in ["notification", "notify", "alert"]):
                self._add_gap("notifications", "User wanted notifications")

        # Auto-generate improvement proposals from patterns
        if cant_phrases > 2:
            self.propose_improvement(
                "Reduce 'I can\'t' responses",
                "Add pre-check for available tools and auto-research missing capabilities.",
                "agent_core.py",
            )
        if error_rate > 0.5:
            self.propose_improvement(
                "Improve tool error handling",
                f"Tool error rate is {error_rate:.0%}. Add input validation and better fallbacks.",
                "tools.py",
            )

        self.reviews.append(review)
        self._save_reviews()
        return review

    # ---- Capability tracking ----
    def confirm_capability(self, name: str, details: str = ""):
        cap = {"name": name, "details": details, "confirmed_at": datetime.now().isoformat()}
        self.capabilities["confirmed"] = [
            c for c in self.capabilities["confirmed"] if c["name"] != name
        ]
        self.capabilities["confirmed"].append(cap)
        self.capabilities["gaps"] = [
            g for g in self.capabilities["gaps"] if g["name"] != name
        ]
        self._save_capabilities()

    def _add_gap(self, name: str, reason: str):
        existing = [g for g in self.capabilities["gaps"] if g["name"] == name]
        if not existing:
            self.capabilities["gaps"].append({
                "name": name,
                "reason": reason,
                "detected_at": datetime.now().isoformat(),
            })
            self._save_capabilities()

    def add_requested_capability(self, name: str, user_request: str):
        self.capabilities["requested"].append({
            "name": name,
            "request": user_request[:200],
            "requested_at": datetime.now().isoformat(),
        })
        self.capabilities["requested"] = self.capabilities["requested"][-50:]
        self._save_capabilities()

    # ---- Improvement proposals ----
    def propose_improvement(self, title: str, description: str, file_target: str = None, code: str = None):
        # Avoid duplicate proposals
        for imp in self.improvements:
            if imp["title"] == title and imp["status"] == "proposed":
                return imp

        improvement = {
            "id": max((i.get("id", 0) for i in self.improvements), default=0) + 1,
            "title": title,
            "description": description,
            "file_target": file_target,
            "code": code,
            "status": "proposed",
            "proposed_at": datetime.now().isoformat(),
        }
        self.improvements.append(improvement)
        self._save_improvements()
        return improvement

    def get_pending_improvements(self) -> List[Dict]:
        return [i for i in self.improvements if i["status"] == "proposed"]

    def approve_improvement(self, improvement_id: int) -> Optional[Dict]:
        for imp in self.improvements:
            if imp["id"] == improvement_id:
                imp["status"] = "approved"
                imp["approved_at"] = datetime.now().isoformat()
                self._save_improvements()
                return imp
        return None

    # ---- Proactive Ideas (NEW: Timmy generates ideas for self-improvement) ----
    def add_idea(self, title: str, description: str, source: str = "idle_research", category: str = "general"):
        """Record an idea for self-improvement or new feature."""
        idea = {
            "id": max((i.get("id", 0) for i in self.ideas), default=0) + 1,
            "title": title,
            "description": description,
            "source": source,
            "category": category,
            "status": "new",  # new, presented, approved, rejected
            "created_at": datetime.now().isoformat(),
        }
        self.ideas.append(idea)
        self._save_ideas()
        return idea

    def get_new_ideas(self) -> List[Dict]:
        return [i for i in self.ideas if i["status"] == "new"]

    def mark_idea_presented(self, idea_id: int):
        for idea in self.ideas:
            if idea["id"] == idea_id:
                idea["status"] = "presented"
                self._save_ideas()
                return

    # ---- Idle-Time Research (NEW: proactive self-evolution) ----
    def start_idle_research(self, agent_core=None):
        """Start background idle research thread.
        When Timmy is idle, search for improvements, new tools, better approaches."""
        if self._idle_running:
            return

        config = _load_config()
        evo_config = config.get("self_evolution", {})
        if not evo_config.get("idle_research", False):
            return

        self._idle_running = True
        self._idle_thread = threading.Thread(
            target=self._idle_research_loop,
            args=(agent_core, evo_config),
            daemon=True,
        )
        self._idle_thread.start()
        logger.info("Idle research thread started")

    def stop_idle_research(self):
        self._idle_running = False

    def _idle_research_loop(self, agent_core, evo_config):
        """Background loop: when agent is idle, research improvements."""
        interval = evo_config.get("idle_interval_minutes", 30) * 60
        max_searches = evo_config.get("max_idle_searches", 3)

        research_topics = [
            "local AI agent improvements {year}",
            "Ollama model optimization tips {year}",
            "Python AI agent best practices {year}",
            "macOS automation AppleScript tips",
            "ChromaDB vector search optimization",
            "Gradio UI advanced features {year}",
            "AI agent memory systems research {year}",
            "local LLM agent self-improvement techniques",
        ]
        topic_index = 0

        while self._idle_running:
            try:
                time.sleep(interval)

                # Only research when idle
                if agent_core and agent_core.is_working:
                    continue

                # Check gaps for targeted research
                gaps = self.capabilities.get("gaps", [])
                if gaps:
                    gap = gaps[0]
                    topic = f"how to add {gap['name']} capability to Python AI agent"
                else:
                    topic = research_topics[topic_index % len(research_topics)]
                    topic = topic.format(year=datetime.now().year)
                    topic_index += 1

                # Do a lightweight web search
                results = self._idle_web_search(topic, max_searches)

                if results:
                    # Generate an idea from research
                    snippets = " | ".join(r.get("snippet", "")[:100] for r in results[:3])
                    self.add_idea(
                        title=f"Research: {topic[:60]}",
                        description=f"Found: {snippets[:300]}",
                        source="idle_research",
                        category="research",
                    )
                    logger.info(f"Idle research: found info on '{topic}'")

            except Exception as e:
                logger.warning(f"Idle research error: {e}")

    def _idle_web_search(self, query: str, max_results: int = 3) -> List[Dict]:
        """Lightweight web search for idle research."""
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in raw
            ]
        except Exception:
            return []

    # ---- Audit log analysis ----
    def analyze_tool_usage(self) -> Dict:
        audit_path = BASE_DIR / "tim_audit.log"
        if not audit_path.exists():
            return {"error": "No audit log found"}

        tool_counts: Dict[str, int] = {}
        error_counts: Dict[str, int] = {}

        try:
            # Read only last 512KB to avoid OOM on large audit logs
            with open(audit_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 524288))
                tail = f.read().decode("utf-8", errors="ignore")
            for line in tail.splitlines():
                try:
                    entry = json.loads(line)
                    tool = entry.get("tool", "unknown")
                    tool_counts[tool] = tool_counts.get(tool, 0) + 1
                    result = entry.get("result", "")
                    if "error" in result.lower() or "failed" in result.lower():
                        error_counts[tool] = error_counts.get(tool, 0) + 1
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

        return {
            "tool_usage": dict(sorted(tool_counts.items(), key=lambda x: -x[1])),
            "tool_errors": error_counts,
            "total_calls": sum(tool_counts.values()),
            "total_errors": sum(error_counts.values()),
        }

    # ---- Summary ----
    def get_evolution_summary(self) -> str:
        lines = ["## Self-Evolution Status\n"]

        # Recent review
        if self.reviews:
            last = self.reviews[-1]
            lines.append(f"**Last Review:** {last.get('date', 'unknown')}")
            stats = last.get("stats", {})
            lines.append(f"- Messages: {stats.get('total_messages', 0)}")
            lines.append(f"- Tool calls: {stats.get('tool_calls', 0)}")
            lines.append(f"- Errors: {stats.get('errors', 0)}")
            if last.get("issues"):
                lines.append("- Issues: " + "; ".join(last["issues"][:3]))
            lines.append("")

        # Capabilities
        confirmed = len(self.capabilities.get("confirmed", []))
        gaps = len(self.capabilities.get("gaps", []))
        lines.append(f"**Capabilities:** {confirmed} confirmed, {gaps} gaps")
        for gap in self.capabilities.get("gaps", [])[:5]:
            lines.append(f"  - GAP: {gap['name']} ({gap['reason']})")
        lines.append("")

        # Pending improvements
        pending = self.get_pending_improvements()
        if pending:
            lines.append(f"**Pending Improvements:** {len(pending)}")
            for imp in pending[:5]:
                lines.append(f"  - #{imp['id']}: {imp['title']}")
            lines.append("")

        # New ideas from idle research
        new_ideas = self.get_new_ideas()
        if new_ideas:
            lines.append(f"**New Ideas:** {len(new_ideas)}")
            for idea in new_ideas[:5]:
                lines.append(f"  - #{idea['id']}: {idea['title']}")

        # Idle research status
        if self._idle_running:
            lines.append("\n*Idle research: Active*")
        else:
            lines.append("\n*Idle research: Inactive*")

        return "\n".join(lines)
