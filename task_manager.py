"""
Task Manager for LLTimmy
Persistent goals, task queue with priorities, checkpoints, sub-task decomposition.
Survives restarts. Goals shown in agent system prompt.
"""
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_BASE = Path.home() / "LLTimmy" / "memory"


class Task:
    """Single task with status, priority, sub-tasks, and checkpoint support."""

    def __init__(
        self,
        title: str,
        description: str = "",
        priority: int = 5,
        parent_id: str = None,
        task_id: str = None,
        urgency: str = "normal",
        schedule: str = "now",
        scheduled_time: str = None,
    ):
        self.id = task_id or self._gen_id(title)
        self.title = title
        self.description = description
        self.priority = priority  # 1 (highest) to 10 (lowest)
        self.urgency = urgency  # "critical", "high", "normal", "low"
        self.schedule = schedule  # "now", "idle", "scheduled"
        self.scheduled_time = scheduled_time  # ISO datetime for "scheduled" mode
        self.status = "pending"  # pending, in_progress, completed, failed, paused
        self.parent_id = parent_id
        self.subtasks: List[str] = []
        self.checkpoints: List[Dict] = []
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.completed_at = None
        self.retry_count = 0
        self.max_retries = 3
        self.progress: int = 0  # 0-100 percent
        self.notes: List[str] = []

    @staticmethod
    def _gen_id(title: str) -> str:
        import hashlib
        ts = datetime.now().isoformat()
        return hashlib.md5(f"{title}:{ts}".encode()).hexdigest()[:12]

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "urgency": self.urgency,
            "schedule": self.schedule,
            "scheduled_time": self.scheduled_time,
            "status": self.status,
            "parent_id": self.parent_id,
            "subtasks": self.subtasks,
            "checkpoints": self.checkpoints,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "progress": self.progress,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Task":
        t = cls(
            title=data["title"],
            description=data.get("description", ""),
            priority=data.get("priority", 5),
            parent_id=data.get("parent_id"),
            task_id=data.get("id"),
            urgency=data.get("urgency", "normal"),
            schedule=data.get("schedule", "now"),
            scheduled_time=data.get("scheduled_time"),
        )
        t.status = data.get("status", "pending")
        t.subtasks = data.get("subtasks", [])
        t.checkpoints = data.get("checkpoints", [])
        t.created_at = data.get("created_at", t.created_at)
        t.updated_at = data.get("updated_at", t.updated_at)
        t.completed_at = data.get("completed_at")
        t.retry_count = data.get("retry_count", 0)
        t.max_retries = data.get("max_retries", 3)
        t.progress = data.get("progress", 0)
        t.notes = data.get("notes", [])
        return t


class TaskManager:
    """Persistent task queue with goal tracking, sub-tasks, and checkpoints."""

    def __init__(self):
        self.tasks_file = MEMORY_BASE / "tasks.json"
        self.goals_file = MEMORY_BASE / "active_goals.json"
        MEMORY_BASE.mkdir(parents=True, exist_ok=True)
        self.tasks: Dict[str, Task] = {}
        self._load()

    # ---- Persistence ----
    def _load(self):
        if self.tasks_file.exists():
            try:
                data = json.loads(self.tasks_file.read_text(encoding="utf-8"))
                for td in data:
                    task = Task.from_dict(td)
                    self.tasks[task.id] = task
            except Exception as e:
                logger.warning(f"Task load error: {e}")

    def _save(self):
        data = [t.to_dict() for t in self.tasks.values()]
        self._atomic_write(self.tasks_file, data)
        self._sync_goals()

    def _sync_goals(self):
        """Write active goals to active_goals.json for the agent system prompt."""
        active = self.get_active_goals()
        self._atomic_write(self.goals_file, active)

    @staticmethod
    def _atomic_write(path: Path, data):
        """Write JSON atomically via temp file + rename to prevent corruption."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # Atomic on POSIX

    # ---- CRUD ----
    def add_task(
        self,
        title: str,
        description: str = "",
        priority: int = 5,
        parent_id: str = None,
        urgency: str = "normal",
        schedule: str = "now",
        scheduled_time: str = None,
    ) -> Task:
        task = Task(title, description, priority, parent_id,
                     urgency=urgency, schedule=schedule, scheduled_time=scheduled_time)
        self.tasks[task.id] = task

        # If this is a subtask, register with parent
        if parent_id and parent_id in self.tasks:
            self.tasks[parent_id].subtasks.append(task.id)
            self.tasks[parent_id].updated_at = datetime.now().isoformat()

        self._save()
        logger.info(f"Task added: {task.title} [{task.id}] urgency={urgency} schedule={schedule}")
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def update_status(self, task_id: str, status: str) -> Optional[Task]:
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.status = status
        task.updated_at = datetime.now().isoformat()
        if status == "completed":
            task.completed_at = datetime.now().isoformat()
        self._save()
        logger.info(f"Task [{task_id}] -> {status}")
        return task

    def update_title(self, task_id: str, new_title: str) -> Optional[Task]:
        """Rename a task."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.title = new_title
        task.updated_at = datetime.now().isoformat()
        self._save()
        logger.info(f"Task [{task_id}] renamed to: {new_title}")
        return task

    def add_note(self, task_id: str, note: str) -> Optional[Task]:
        """Add a note to a task."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.notes.append(note)
        task.updated_at = datetime.now().isoformat()
        self._save()
        return task

    def find_by_title(self, title: str) -> Optional[Task]:
        """Find a task by title (case-insensitive)."""
        title_lower = title.lower().strip()
        for task in self.tasks.values():
            if task.title.lower() == title_lower:
                return task
        return None

    def remove_task(self, task_id: str) -> bool:
        task = self.tasks.pop(task_id, None)
        if not task:
            return False
        # Remove from parent's subtask list
        if task.parent_id and task.parent_id in self.tasks:
            parent = self.tasks[task.parent_id]
            parent.subtasks = [s for s in parent.subtasks if s != task_id]
        # Remove subtasks recursively
        for sub_id in list(task.subtasks):
            self.remove_task(sub_id)
        self._save()
        return True

    # ---- Checkpoints ----
    def add_checkpoint(self, task_id: str, note: str, data: Dict = None):
        task = self.tasks.get(task_id)
        if not task:
            return
        task.checkpoints.append({
            "timestamp": datetime.now().isoformat(),
            "note": note,
            "data": data or {},
        })
        task.updated_at = datetime.now().isoformat()
        self._save()

    # ---- Queries ----
    def get_active_goals(self) -> List[str]:
        """Return titles of all non-completed top-level tasks (goals)."""
        return [
            t.title
            for t in self.tasks.values()
            if t.parent_id is None and t.status not in ("completed", "failed")
        ]

    def get_pending_tasks(self) -> List[Task]:
        """Return all pending tasks sorted by priority."""
        return sorted(
            [t for t in self.tasks.values() if t.status == "pending"],
            key=lambda t: t.priority,
        )

    def get_in_progress(self) -> List[Task]:
        return [t for t in self.tasks.values() if t.status == "in_progress"]

    def get_all_tasks(self) -> List[Task]:
        return list(self.tasks.values())

    def get_next_task(self) -> Optional[Task]:
        """Get the highest-priority task that should run now.
        Order: critical urgency > high > normal > low, then by priority number."""
        urgency_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        ready = [
            t for t in self.tasks.values()
            if t.status == "pending" and t.schedule == "now"
        ]
        if not ready:
            # Also consider "idle" scheduled tasks
            ready = [
                t for t in self.tasks.values()
                if t.status == "pending" and t.schedule == "idle"
            ]
        if not ready:
            # Check scheduled tasks whose time has arrived
            now = datetime.now().isoformat()
            ready = [
                t for t in self.tasks.values()
                if t.status == "pending" and t.schedule == "scheduled"
                and t.scheduled_time and t.scheduled_time <= now
            ]
        if not ready:
            return None
        ready.sort(key=lambda t: (urgency_order.get(t.urgency, 2), t.priority))
        return ready[0]

    def update_progress(self, task_id: str, progress: int) -> Optional[Task]:
        """Update task progress (0-100%)."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.progress = max(0, min(100, progress))
        task.updated_at = datetime.now().isoformat()
        if task.progress == 100 and task.status != "completed":
            task.status = "completed"
            task.completed_at = datetime.now().isoformat()
        self._save()
        return task

    def get_task_tree(self, task_id: str = None) -> List[Dict]:
        """Return task hierarchy as nested dicts."""
        roots = [
            t for t in self.tasks.values()
            if (task_id is None and t.parent_id is None)
            or (task_id is not None and t.id == task_id)
        ]
        result = []
        for task in sorted(roots, key=lambda t: t.priority):
            node = task.to_dict()
            node["children"] = [
                self.get_task_tree(sub_id)[0]
                for sub_id in task.subtasks
                if sub_id in self.tasks
            ]
            result.append(node)
        return result

    # ---- Retry logic ----
    def mark_failed_or_retry(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if not task:
            return "Task not found"
        task.retry_count += 1
        if task.retry_count >= task.max_retries:
            task.status = "failed"
            task.updated_at = datetime.now().isoformat()
            self._save()
            return f"Task '{task.title}' FAILED after {task.retry_count} retries."
        task.status = "pending"
        task.updated_at = datetime.now().isoformat()
        self._save()
        return f"Task '{task.title}' retry {task.retry_count}/{task.max_retries}."

    # ---- Summary for display ----
    def get_summary_text(self) -> str:
        """Human-readable summary of all tasks."""
        lines = []
        trees = self.get_task_tree()
        if not trees:
            return "No active tasks."

        status_icons = {
            "pending": "⏳",
            "in_progress": "🔄",
            "completed": "✅",
            "failed": "❌",
            "paused": "⏸️",
        }

        urgency_tags = {"critical": "[!]", "high": "[H]", "low": "[L]"}

        def render(nodes, depth=0):
            for node in nodes:
                icon = status_icons.get(node["status"], "•")
                indent = "  " * depth
                prio = f"P{node['priority']}" if node["priority"] != 5 else ""
                urg = urgency_tags.get(node.get("urgency", "normal"), "")
                prog = f" {node['progress']}%" if node.get("progress", 0) > 0 and node["status"] == "in_progress" else ""
                lines.append(f"{indent}{icon} {urg}{node['title']} {prio}{prog}".rstrip())
                if node.get("children"):
                    render(node["children"], depth + 1)

        render(trees)
        return "\n".join(lines)
