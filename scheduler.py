"""
Scheduler / Calendar for LLTimmy
Persistent calendar with scheduled messages, reminders, cron-like tasks,
and Timmy's own events. Used by main.py for calendar tab and notifications.
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_BASE = Path.home() / "LLTimmy" / "memory"
CALENDAR_FILE = MEMORY_BASE / "calendar.json"


class Scheduler:
    """Persistent calendar with events, reminders, and scheduled messages."""

    def __init__(self):
        MEMORY_BASE.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.events: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        if CALENDAR_FILE.exists():
            try:
                return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save(self):
        tmp = CALENDAR_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.events, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(CALENDAR_FILE)

    def add_event(
        self,
        title: str,
        due: str = None,
        event_type: str = "reminder",
        message: str = None,
        source: str = "user",
        recurring: str = None,
    ) -> Dict:
        """Add a calendar event.

        Args:
            title: Event title / description
            due: ISO datetime string or "YYYY-MM-DD HH:MM". Defaults to +1h from now.
            event_type: "reminder", "scheduled_message", "task", "note"
            message: Optional message to send when event triggers
            source: "user" or "timmy" (Timmy can add events too)
            recurring: None, "daily", "weekly", "monthly"
        """
        if due:
            # Normalize slashes to dashes for flexible input (2026/02/26 → 2026-02-26)
            normalized = due.strip().replace("/", "-")
            # Parse flexible date formats (both dash and slash separated accepted)
            for fmt in (
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%m-%d-%Y %H:%M:%S",
                "%m-%d-%Y %H:%M",
                "%m-%d-%Y",
                "%d-%m-%Y %H:%M",
                "%d-%m-%Y",
            ):
                try:
                    dt = datetime.strptime(normalized, fmt)
                    due_iso = dt.isoformat()
                    break
                except ValueError:
                    continue
            else:
                # Try relative: "+1h", "+30m", "+2d"
                due_iso = self._parse_relative(due)
                if not due_iso:
                    raise ValueError(f"Cannot parse date: {due}. Use YYYY-MM-DD HH:MM or YYYY/MM/DD HH:MM format.")
        else:
            due_iso = (datetime.now() + timedelta(hours=1)).isoformat()

        with self._lock:
            existing_max = max((e.get("id", 0) for e in self.events), default=0)
            event = {
                "id": existing_max + 1,
                "title": title,
                "due": due_iso,
                "type": event_type,
                "message": message,
                "source": source,
                "recurring": recurring,
                "status": "pending",  # pending, triggered, dismissed
                "created_at": datetime.now().isoformat(),
            }
            self.events.append(event)
            self._save()
        logger.info(f"Calendar event added: {title} due {due_iso}")
        return event

    def _parse_relative(self, rel: str) -> Optional[str]:
        """Parse relative time strings like '+1h', '+30m', '+2d'."""
        rel = rel.strip().lower()
        if not rel.startswith("+"):
            return None
        try:
            num = int(rel[1:-1])
            unit = rel[-1]
            if unit == "m":
                dt = datetime.now() + timedelta(minutes=num)
            elif unit == "h":
                dt = datetime.now() + timedelta(hours=num)
            elif unit == "d":
                dt = datetime.now() + timedelta(days=num)
            elif unit == "w":
                dt = datetime.now() + timedelta(weeks=num)
            else:
                return None
            return dt.isoformat()
        except (ValueError, IndexError):
            return None

    def remove_event(self, event_id: int) -> bool:
        with self._lock:
            before = len(self.events)
            self.events = [e for e in self.events if e.get("id") != event_id]
            if len(self.events) < before:
                self._save()
                return True
        return False

    def get_upcoming(self, limit: int = 10) -> List[Dict]:
        """Get upcoming events sorted by due date."""
        now = datetime.now().isoformat()
        pending = [
            e for e in self.events
            if e.get("status") == "pending"
        ]
        pending.sort(key=lambda e: e.get("due", ""))
        return pending[:limit]

    def check_due(self) -> List[Dict]:
        """Check for events that are now due. Returns triggered events."""
        with self._lock:
            now = datetime.now()
            triggered = []

            for event in self.events:
                if event.get("status") != "pending":
                    continue
                try:
                    due_dt = datetime.fromisoformat(event["due"])
                except (ValueError, KeyError):
                    continue

                if due_dt <= now:
                    event["status"] = "triggered"
                    event["triggered_at"] = now.isoformat()
                    triggered.append(event)

                    # Handle recurring events
                    if event.get("recurring"):
                        self._create_next_recurring(event)

            if triggered:
                self._save()

        return triggered

    def _create_next_recurring(self, event: Dict):
        """Create the next occurrence of a recurring event."""
        try:
            due_dt = datetime.fromisoformat(event["due"])
            recurring = event.get("recurring")

            if recurring == "daily":
                next_due = due_dt + timedelta(days=1)
            elif recurring == "weekly":
                next_due = due_dt + timedelta(weeks=1)
            elif recurring == "monthly":
                # Approximate: add 30 days
                next_due = due_dt + timedelta(days=30)
            else:
                return

            self.add_event(
                title=event["title"],
                due=next_due.isoformat(),
                event_type=event.get("type", "reminder"),
                message=event.get("message"),
                source=event.get("source", "recurring"),
                recurring=recurring,
            )
        except Exception as e:
            logger.warning(f"Recurring event creation failed: {e}")

    def get_events_for_date(self, date_str: str) -> List[Dict]:
        """Get all events for a specific date."""
        return [
            e for e in self.events
            if e.get("due", "").startswith(date_str)
        ]

    def dismiss_event(self, event_id: int):
        """Mark an event as dismissed."""
        for event in self.events:
            if event.get("id") == event_id:
                event["status"] = "dismissed"
                self._save()
                return

    def get_summary(self) -> str:
        """Human-readable summary."""
        pending = [e for e in self.events if e.get("status") == "pending"]
        triggered = [e for e in self.events if e.get("status") == "triggered"]
        return f"Calendar: {len(pending)} pending, {len(triggered)} triggered"
