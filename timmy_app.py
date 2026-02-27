"""
LLTimmy Native macOS Desktop App
CustomTkinter-based premium dark UI with amber/gold accent.
Replaces Gradio browser UI with native desktop experience.

Architecture:
- Doctor runs as background daemon (always on)
- Timmy agent runs in this process
- No browser, no localhost, no Gradio
- Launchable via Spotlight/Dock
"""
import json
import os
import sys
import asyncio
import threading
import time
import logging
import subprocess
import signal
from pathlib import Path
from datetime import datetime, date
from typing import List, Dict, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
PID_FILE = Path("/tmp/timmy.pid")
DOCTOR_PID_FILE = Path("/tmp/doctor.pid")
MEMORY_BASE = BASE_DIR / "memory"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("timmy_app")

# Write our PID
PID_FILE.write_text(str(os.getpid()))

# Load config
try:
    with open(CONFIG_PATH) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"FATAL: Cannot load config.json: {e}")
    sys.exit(1)

# Import backend modules
from agent_core import AgentCore
from memory_manager import MemoryManager
from task_manager import TaskManager
from self_evolution import SelfEvolution
from tools import ToolsSystem

try:
    from scheduler import Scheduler
    scheduler = Scheduler()
except ImportError:
    scheduler = None

memory = MemoryManager(ollama_host=config.get("ollama_host", "http://localhost:11434"))
tools = ToolsSystem(config)
task_mgr = TaskManager()
evolution = SelfEvolution()
agent = AgentCore(config=config, memory_manager=memory, tools_system=tools,
                   scheduler=scheduler, task_mgr=task_mgr)

# ---------------------------------------------------------------------------
# Color Palette - Premium Stealth
# ---------------------------------------------------------------------------
C_BG = "#0b0b0b"           # Deep charcoal matte
C_SURFACE = "#141414"       # Card/panel surface
C_SURFACE_2 = "#1a1a1a"    # Slightly lighter surface
C_BORDER = "#1f1f1f"        # 1px subtle border (no alpha - tk limitation)
C_BORDER_SOLID = "#2a2a2a" # Solid border for inputs
C_ACCENT = "#ffb700"        # Strict amber/gold
C_ACCENT_DIM = "#3d2e00"   # Dimmed accent (amber mixed with dark bg, no alpha)
C_TEXT = "#f5f5f7"          # Primary text
C_TEXT_DIM = "#86868b"      # Secondary text
C_TEXT_MUTED = "#48484a"    # Muted text
C_GREEN = "#30d158"         # Online/success
C_RED = "#ff453a"           # Error/failure (muted)
C_INPUT_BG = "#1e1e1e"     # Input background

# ---------------------------------------------------------------------------
# CustomTkinter Theme Setup
# ---------------------------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ---------------------------------------------------------------------------
# Async event loop for agent
# ---------------------------------------------------------------------------
_loop = asyncio.new_event_loop()

def _run_loop():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()

_loop_thread = threading.Thread(target=_run_loop, daemon=True)
_loop_thread.start()


# ============================================================================
# Main Application
# ============================================================================
class LLTimmyApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window setup
        self.title("LLTimmy")
        self.geometry("1400x900")
        self.minsize(1000, 700)
        self.configure(fg_color=C_BG)

        # State
        self._agent_working = False
        self._chat_history: List[Dict] = []
        self._current_tab = "Tasks"
        self._trace_log: List[Dict] = []
        self._sidebar_expanded_menu = False
        self._last_stream_update = 0  # Throttle streaming UI updates

        # Load today's chat
        self._load_chat_history()

        # Build UI
        self._build_ui()

        # Background threads
        self._start_background_threads()

        # Startup message
        if not self._chat_history:
            self._append_message("assistant", "I am Timmy. Ben's AI agent. I've been monitoring your workspace and I'm ready to assist.")

        # Protocol for clean exit
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Chat history persistence
    # ------------------------------------------------------------------
    def _load_chat_history(self):
        msgs = memory.load_current_day()
        self._chat_history = [
            {"role": m["role"], "content": m["content"],
             "ts": m.get("timestamp", datetime.now().isoformat())}
            for m in msgs
        ]

    def _append_message(self, role: str, content: str):
        self._chat_history.append({
            "role": role, "content": content,
            "ts": datetime.now().isoformat()
        })
        memory.save_message(role, content)
        self._render_chat()

    # ------------------------------------------------------------------
    # Build the entire UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        # Main container
        self._main_frame = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        self._main_frame.pack(fill="both", expand=True)
        self._main_frame.grid_columnconfigure(1, weight=1)
        self._main_frame.grid_rowconfigure(0, weight=1)

        # Sidebar
        self._build_sidebar()
        # Chat area
        self._build_chat_area()

    # ------------------------------------------------------------------
    # SIDEBAR
    # ------------------------------------------------------------------
    def _build_sidebar(self):
        self._sidebar = ctk.CTkFrame(
            self._main_frame, width=300, fg_color=C_SURFACE,
            corner_radius=0, border_width=1, border_color=C_BORDER_SOLID
        )
        self._sidebar.grid(row=0, column=0, sticky="nsew")
        self._sidebar.grid_propagate(False)
        self._sidebar.grid_rowconfigure(3, weight=1)  # Tabs expand

        # Header: LLTimmy + status dot + plus button
        header = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=70)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(20, 8))
        header.grid_columnconfigure(1, weight=1)

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(title_frame, text="LLTimmy", font=("SF Pro Display", 22, "bold"),
                      text_color=C_TEXT).pack(side="left")
        # Gold status dot (using label with unicode circle)
        ctk.CTkLabel(title_frame, text="\u25cf", font=("SF Pro", 10),
                      text_color=C_ACCENT, width=12).pack(side="left", padx=(6, 0))

        plus_btn = ctk.CTkButton(header, text="+", width=30, height=30,
                                  fg_color=C_SURFACE_2, hover_color="#333",
                                  text_color=C_TEXT_DIM, font=("SF Pro", 16),
                                  corner_radius=6, command=self._new_session)
        plus_btn.grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(self._sidebar, text="LARGE AGENT V7",
                      font=("SF Mono", 9), text_color=C_TEXT_MUTED,
                      anchor="center").grid(row=0, column=0, sticky="ew", pady=(42, 0))

        # Agent cards
        cards_frame = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        cards_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(8, 4))

        self._agent_card = self._make_agent_card(
            cards_frame, "Agent Timmy", "Ready to execute", C_ACCENT, True)
        self._agent_card.pack(fill="x", pady=(0, 4))

        self._doctor_card = self._make_agent_card(
            cards_frame, "Doctor Online", "Inactive", C_TEXT_MUTED, False)
        self._doctor_card.pack(fill="x")

        # Tabs: Tasks, Memory, Calendar + three-dot menu
        tabs_header = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=36)
        tabs_header.grid(row=2, column=0, sticky="ew", padx=12, pady=(12, 0))

        self._tab_buttons = {}
        for i, tab_name in enumerate(["Tasks", "Memory", "Calendar"]):
            btn = ctk.CTkButton(
                tabs_header, text=tab_name, width=75, height=28,
                fg_color=C_ACCENT if tab_name == "Tasks" else "transparent",
                hover_color=C_SURFACE_2,
                text_color=C_BG if tab_name == "Tasks" else C_TEXT_DIM,
                font=("SF Pro", 12, "bold" if tab_name == "Tasks" else "normal"),
                corner_radius=6,
                command=lambda t=tab_name: self._switch_tab(t)
            )
            btn.pack(side="left", padx=(0, 4))
            self._tab_buttons[tab_name] = btn

        # Three-dot menu button
        self._menu_btn = ctk.CTkButton(
            tabs_header, text="\u22ef", width=28, height=28,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_DIM, font=("SF Pro", 16),
            corner_radius=6, command=self._show_extended_menu
        )
        self._menu_btn.pack(side="right")

        # Tab content area
        self._tab_content = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._tab_content.grid(row=3, column=0, sticky="nsew", padx=12, pady=(8, 8))

        # Build each tab's content
        self._tabs = {}
        self._build_tasks_tab()
        self._build_memory_tab()
        self._build_calendar_tab()
        # Extended tabs (shown via menu)
        self._build_trace_tab()
        self._build_evolution_tab()
        self._build_console_tab()
        self._build_settings_tab()

        # Show default tab
        self._show_tab("Tasks")

        # Bottom: Quick add + Clear Archive
        bottom = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=60)
        bottom.grid(row=4, column=0, sticky="sew", padx=12, pady=(0, 12))

        self._quick_add = ctk.CTkEntry(
            bottom, placeholder_text="Quick add task...",
            fg_color=C_INPUT_BG, border_color=C_BORDER_SOLID,
            text_color=C_TEXT, font=("SF Pro", 12), height=32,
            corner_radius=8
        )
        self._quick_add.pack(fill="x", side="top", pady=(0, 6))
        self._quick_add.bind("<Return>", self._quick_add_task)

        clear_btn = ctk.CTkButton(
            bottom, text="CLEAR ARCHIVE", height=24,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_MUTED, font=("SF Mono", 9),
            corner_radius=4, command=self._clear_completed_tasks
        )
        clear_btn.pack(fill="x")

    def _make_agent_card(self, parent, name, subtitle, dot_color, active):
        border_args = {"border_width": 1, "border_color": C_ACCENT} if active else {"border_width": 0}
        card = ctk.CTkFrame(parent, fg_color=C_ACCENT_DIM if active else C_SURFACE_2,
                             corner_radius=10, height=52, **border_args)
        card.pack_propagate(False)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=8)

        # Dot (using label with unicode circle)
        ctk.CTkLabel(inner, text="\u25cf", font=("SF Pro", 10),
                      text_color=dot_color, width=14).pack(side="left", padx=(0, 6))

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(info, text=name, font=("SF Pro", 13, "bold"),
                      text_color=C_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(info, text=subtitle, font=("SF Pro", 10),
                      text_color=C_TEXT_DIM, anchor="w").pack(anchor="w")
        return card

    # ------------------------------------------------------------------
    # Tab building
    # ------------------------------------------------------------------
    def _build_tasks_tab(self):
        frame = ctk.CTkScrollableFrame(self._tab_content, fg_color="transparent",
                                        scrollbar_button_color=C_SURFACE_2)
        self._tabs["Tasks"] = frame
        self._tasks_container = frame

    def _build_memory_tab(self):
        frame = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Memory"] = frame

        # Stats
        self._mem_stats_label = ctk.CTkLabel(
            frame, text="", font=("SF Mono", 10),
            text_color=C_TEXT_DIM, anchor="w", justify="left"
        )
        self._mem_stats_label.pack(fill="x", pady=(0, 8))

        # Search
        search = ctk.CTkEntry(
            frame, placeholder_text="Search memories...",
            fg_color=C_INPUT_BG, border_color=C_BORDER_SOLID,
            text_color=C_TEXT, font=("SF Pro", 12), height=32, corner_radius=8
        )
        search.pack(fill="x", pady=(0, 8))
        search.bind("<Return>", lambda e: self._search_memory(search.get()))
        self._mem_search = search

        self._mem_results_frame = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", height=300,
            scrollbar_button_color=C_SURFACE_2
        )
        self._mem_results_frame.pack(fill="both", expand=True)

    def _build_calendar_tab(self):
        frame = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Calendar"] = frame

        self._cal_container = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", height=250,
            scrollbar_button_color=C_SURFACE_2
        )
        self._cal_container.pack(fill="both", expand=True, pady=(0, 8))

        # Add event controls
        add_frame = ctk.CTkFrame(frame, fg_color="transparent")
        add_frame.pack(fill="x")

        self._cal_title = ctk.CTkEntry(
            add_frame, placeholder_text="Event title...",
            fg_color=C_INPUT_BG, border_color=C_BORDER_SOLID,
            text_color=C_TEXT, font=("SF Pro", 11), height=28, corner_radius=6
        )
        self._cal_title.pack(fill="x", pady=(0, 4))

        row = ctk.CTkFrame(add_frame, fg_color="transparent")
        row.pack(fill="x")
        self._cal_due = ctk.CTkEntry(
            row, placeholder_text="Due: YYYY-MM-DD HH:MM or +1h",
            fg_color=C_INPUT_BG, border_color=C_BORDER_SOLID,
            text_color=C_TEXT, font=("SF Pro", 11), height=28, corner_radius=6
        )
        self._cal_due.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            row, text="Add", width=50, height=28,
            fg_color=C_ACCENT, text_color=C_BG, font=("SF Pro", 11, "bold"),
            corner_radius=6, hover_color="#e6a500",
            command=self._add_calendar_event
        ).pack(side="right")

    def _build_trace_tab(self):
        frame = ctk.CTkScrollableFrame(self._tab_content, fg_color="transparent",
                                        scrollbar_button_color=C_SURFACE_2)
        self._tabs["Trace"] = frame
        self._trace_container = frame

    def _build_evolution_tab(self):
        frame = ctk.CTkScrollableFrame(self._tab_content, fg_color="transparent",
                                        scrollbar_button_color=C_SURFACE_2)
        self._tabs["Evolution"] = frame
        self._evo_container = frame

    def _build_console_tab(self):
        frame = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Console"] = frame

        self._console_text = ctk.CTkTextbox(
            frame, fg_color=C_SURFACE_2, text_color=C_GREEN,
            font=("SF Mono", 10), corner_radius=8, border_width=1,
            border_color=C_BORDER_SOLID, wrap="word"
        )
        self._console_text.pack(fill="both", expand=True)

    def _build_settings_tab(self):
        frame = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Settings"] = frame

        ctk.CTkLabel(frame, text="Active Model", font=("SF Pro", 11),
                      text_color=C_TEXT_DIM).pack(anchor="w", pady=(0, 4))

        models = self._get_model_choices()
        self._model_var = ctk.StringVar(value=agent.current_model)
        self._model_menu = ctk.CTkOptionMenu(
            frame, values=models, variable=self._model_var,
            fg_color=C_INPUT_BG, button_color=C_SURFACE_2,
            button_hover_color="#333", text_color=C_TEXT,
            font=("SF Mono", 11), dropdown_fg_color=C_SURFACE,
            dropdown_text_color=C_TEXT, dropdown_hover_color=C_SURFACE_2,
            corner_radius=6, command=self._on_model_change
        )
        self._model_menu.pack(fill="x", pady=(0, 12))

        ctk.CTkButton(
            frame, text="Refresh Models", height=28,
            fg_color=C_SURFACE_2, hover_color="#333",
            text_color=C_TEXT_DIM, font=("SF Pro", 11),
            corner_radius=6, command=self._refresh_models
        ).pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(frame, text=f"Base: {BASE_DIR}\nMemory: {MEMORY_BASE}",
                      font=("SF Mono", 9), text_color=C_TEXT_MUTED,
                      justify="left").pack(anchor="w")

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------
    def _switch_tab(self, tab_name):
        self._current_tab = tab_name
        # Update button styles
        for name, btn in self._tab_buttons.items():
            if name == tab_name:
                btn.configure(fg_color=C_ACCENT, text_color=C_BG,
                              font=("SF Pro", 12, "bold"))
            else:
                btn.configure(fg_color="transparent", text_color=C_TEXT_DIM,
                              font=("SF Pro", 12))
        self._show_tab(tab_name)

    def _show_tab(self, tab_name):
        for name, frame in self._tabs.items():
            frame.pack_forget()
        if tab_name in self._tabs:
            self._tabs[tab_name].pack(fill="both", expand=True)
        self._refresh_tab(tab_name)

    def _show_extended_menu(self):
        """Show extended tabs: Trace, Evolution, Console, Settings."""
        menu = ctk.CTkToplevel(self)
        menu.title("")
        menu.geometry("160x180")
        menu.overrideredirect(True)
        menu.configure(fg_color=C_SURFACE)
        menu.attributes("-topmost", True)

        # Position near button
        x = self._menu_btn.winfo_rootx()
        y = self._menu_btn.winfo_rooty() + 30
        menu.geometry(f"+{x}+{y}")

        for tab_name in ["Trace", "Evolution", "Console", "Settings"]:
            btn = ctk.CTkButton(
                menu, text=tab_name, height=32,
                fg_color="transparent", hover_color=C_SURFACE_2,
                text_color=C_TEXT, font=("SF Pro", 12),
                anchor="w", corner_radius=4,
                command=lambda t=tab_name, m=menu: (m.destroy(), self._show_tab(t))
            )
            btn.pack(fill="x", padx=4, pady=1)

        menu.bind("<FocusOut>", lambda e: menu.destroy())
        menu.focus_set()

    # ------------------------------------------------------------------
    # Tab refresh
    # ------------------------------------------------------------------
    def _refresh_tab(self, tab_name):
        if tab_name == "Tasks":
            self._render_tasks()
        elif tab_name == "Memory":
            self._render_memory_stats()
        elif tab_name == "Calendar":
            self._render_calendar()
        elif tab_name == "Trace":
            self._render_trace()
        elif tab_name == "Evolution":
            self._render_evolution()
        elif tab_name == "Console":
            self._render_console()

    # ------------------------------------------------------------------
    # TASKS rendering
    # ------------------------------------------------------------------
    def _render_tasks(self):
        for w in self._tasks_container.winfo_children():
            w.destroy()

        all_tasks = task_mgr.get_all_tasks()
        if not all_tasks:
            ctk.CTkLabel(self._tasks_container, text="No tasks yet.",
                          font=("SF Pro", 12), text_color=C_TEXT_MUTED).pack(pady=20)
            return

        urgency_colors = {"critical": C_RED, "high": "#ff9500", "normal": C_TEXT_DIM, "low": "#636366"}
        status_icons = {"pending": "\u25cb", "in_progress": "\u25d4", "completed": "\u25cf", "failed": "\u2716", "paused": "\u25a0"}

        for task in sorted(all_tasks, key=lambda t: ({"critical": 0, "high": 1, "normal": 2, "low": 3}.get(t.urgency, 2), t.priority)):
            row = ctk.CTkFrame(self._tasks_container, fg_color=C_SURFACE_2,
                                corner_radius=8, height=40)
            row.pack(fill="x", pady=(0, 4))
            row.pack_propagate(False)

            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=10, pady=6)

            # Urgency sliver on left
            if task.urgency in ("critical", "high"):
                sliver = ctk.CTkFrame(row, width=3, fg_color=urgency_colors.get(task.urgency, C_TEXT_DIM),
                                       corner_radius=2)
                sliver.place(x=0, y=4, relheight=0.8)

            # Status icon (clickable)
            icon = status_icons.get(task.status, "\u25cb")
            icon_color = C_GREEN if task.status == "completed" else (C_RED if task.status == "failed" else C_ACCENT)
            icon_btn = ctk.CTkButton(
                inner, text=icon, width=20, height=20,
                fg_color="transparent", hover_color=C_SURFACE,
                text_color=icon_color, font=("SF Pro", 14),
                corner_radius=10, command=lambda tid=task.id: self._toggle_task(tid)
            )
            icon_btn.pack(side="left", padx=(0, 6))

            # Title
            title_color = C_TEXT_MUTED if task.status == "completed" else C_TEXT
            ctk.CTkLabel(inner, text=task.title[:40], font=("SF Pro", 12),
                          text_color=title_color, anchor="w").pack(side="left", fill="x", expand=True)

            # Progress if in_progress
            if task.status == "in_progress" and task.progress > 0:
                ctk.CTkLabel(inner, text=f"{task.progress}%",
                              font=("SF Mono", 9), text_color=C_ACCENT).pack(side="right", padx=(4, 0))

    def _toggle_task(self, task_id):
        task = task_mgr.get_task(task_id)
        if not task:
            return
        cycle = {"pending": "in_progress", "in_progress": "completed", "completed": "pending", "failed": "pending", "paused": "in_progress"}
        new_status = cycle.get(task.status, "pending")
        task_mgr.update_status(task_id, new_status)
        self._render_tasks()

    def _quick_add_task(self, event=None):
        text = self._quick_add.get().strip()
        if not text:
            return
        task_mgr.add_task(text)
        self._quick_add.delete(0, "end")
        self._render_tasks()

    def _clear_completed_tasks(self):
        completed = [t for t in task_mgr.get_all_tasks() if t.status == "completed"]
        for t in completed:
            task_mgr.remove_task(t.id)
        self._render_tasks()

    # ------------------------------------------------------------------
    # MEMORY rendering
    # ------------------------------------------------------------------
    def _render_memory_stats(self):
        stats = memory.get_memory_stats()
        text = (f"Today: {stats['today_messages']} msgs\n"
                f"Subconscious: {stats['subconscious_entries']} entries\n"
                f"Graph: {stats['graph_entities']} entities")
        self._mem_stats_label.configure(text=text)

    def _search_memory(self, query):
        if not query.strip():
            return
        results = memory.search_memory(query.strip())
        for w in self._mem_results_frame.winfo_children():
            w.destroy()
        if not results:
            ctk.CTkLabel(self._mem_results_frame, text="No results found.",
                          font=("SF Pro", 11), text_color=C_TEXT_MUTED).pack(pady=10)
            return
        for r in results[:10]:
            content = r.get("content", "")[:120]
            ts = r.get("metadata", {}).get("timestamp", "")[:16]
            card = ctk.CTkFrame(self._mem_results_frame, fg_color=C_SURFACE_2, corner_radius=6)
            card.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(card, text=content, font=("SF Pro", 10),
                          text_color=C_TEXT, wraplength=230, justify="left").pack(
                              fill="x", padx=8, pady=(6, 2))
            ctk.CTkLabel(card, text=ts, font=("SF Mono", 8),
                          text_color=C_TEXT_MUTED).pack(anchor="w", padx=8, pady=(0, 6))

    # ------------------------------------------------------------------
    # CALENDAR rendering
    # ------------------------------------------------------------------
    def _render_calendar(self):
        for w in self._cal_container.winfo_children():
            w.destroy()

        if not scheduler:
            ctk.CTkLabel(self._cal_container, text="Calendar unavailable",
                          font=("SF Pro", 11), text_color=C_TEXT_MUTED).pack(pady=10)
            return

        events = scheduler.get_upcoming(15)
        if not events:
            ctk.CTkLabel(self._cal_container, text="No upcoming events.",
                          font=("SF Pro", 11), text_color=C_TEXT_MUTED).pack(pady=10)
            return

        for ev in events:
            card = ctk.CTkFrame(self._cal_container, fg_color=C_SURFACE_2, corner_radius=6)
            card.pack(fill="x", pady=(0, 4))
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=8, pady=6)
            ctk.CTkLabel(inner, text=ev["title"][:40], font=("SF Pro", 11),
                          text_color=C_TEXT, anchor="w").pack(anchor="w")
            due_str = ev.get("due", "")[:16]
            ctk.CTkLabel(inner, text=due_str, font=("SF Mono", 9),
                          text_color=C_ACCENT).pack(anchor="w")

    def _add_calendar_event(self):
        title = self._cal_title.get().strip()
        due = self._cal_due.get().strip()
        if not title:
            return
        try:
            if scheduler:
                scheduler.add_event(title, due or None)
                self._cal_title.delete(0, "end")
                self._cal_due.delete(0, "end")
                self._render_calendar()
        except Exception as e:
            logger.warning(f"Calendar add error: {e}")

    # ------------------------------------------------------------------
    # TRACE rendering
    # ------------------------------------------------------------------
    def _render_trace(self):
        for w in self._trace_container.winfo_children():
            w.destroy()

        log_path = BASE_DIR / "tim_audit.log"
        if not log_path.exists():
            ctk.CTkLabel(self._trace_container, text="No trace data.",
                          font=("SF Pro", 11), text_color=C_TEXT_MUTED).pack(pady=10)
            return

        try:
            lines = log_path.read_text(encoding="utf-8").strip().split("\n")[-20:]
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    tool = entry.get("tool", "?")
                    ts = entry.get("ts", "")[:19]
                    result = entry.get("result", "")[:80]

                    card = ctk.CTkFrame(self._trace_container, fg_color=C_SURFACE_2, corner_radius=6)
                    card.pack(fill="x", pady=(0, 3))
                    ctk.CTkLabel(card, text=f"{tool}", font=("SF Pro", 10, "bold"),
                                  text_color=C_ACCENT).pack(anchor="w", padx=8, pady=(4, 0))
                    ctk.CTkLabel(card, text=result, font=("SF Mono", 9),
                                  text_color=C_TEXT_DIM, wraplength=240, justify="left").pack(
                                      anchor="w", padx=8, pady=(0, 2))
                    ctk.CTkLabel(card, text=ts, font=("SF Mono", 8),
                                  text_color=C_TEXT_MUTED).pack(anchor="w", padx=8, pady=(0, 4))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # EVOLUTION rendering
    # ------------------------------------------------------------------
    def _render_evolution(self):
        for w in self._evo_container.winfo_children():
            w.destroy()
        summary = evolution.get_evolution_summary()
        ctk.CTkLabel(self._evo_container, text=summary,
                      font=("SF Mono", 10), text_color=C_TEXT_DIM,
                      wraplength=250, justify="left").pack(fill="x", padx=4, pady=4)

    # ------------------------------------------------------------------
    # CONSOLE rendering
    # ------------------------------------------------------------------
    def _render_console(self):
        log_path = BASE_DIR / "tim_audit.log"
        self._console_text.configure(state="normal")
        self._console_text.delete("1.0", "end")
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8").strip().split("\n")[-30:]
                self._console_text.insert("end", "\n".join(lines))
            except Exception:
                pass
        self._console_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # CHAT AREA
    # ------------------------------------------------------------------
    def _build_chat_area(self):
        chat_frame = ctk.CTkFrame(self._main_frame, fg_color=C_BG, corner_radius=0)
        chat_frame.grid(row=0, column=1, sticky="nsew")
        chat_frame.grid_rowconfigure(1, weight=1)
        chat_frame.grid_columnconfigure(0, weight=1)

        # Header bar
        header = ctk.CTkFrame(chat_frame, fg_color=C_SURFACE, height=50,
                                corner_radius=0, border_width=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        left_header = ctk.CTkFrame(header, fg_color="transparent")
        left_header.grid(row=0, column=0, sticky="w", padx=16, pady=10)

        ctk.CTkLabel(left_header, text="\U0001f512 Secure Session",
                      font=("SF Pro", 14, "bold"), text_color=C_TEXT).pack(side="left")

        badge = ctk.CTkLabel(left_header, text="ENCRYPTED END-TO-END",
                              font=("SF Mono", 8), text_color=C_ACCENT,
                              fg_color=C_SURFACE_2, corner_radius=4)
        badge.pack(side="left", padx=(10, 0))
        badge.configure(width=140, height=20)

        right_header = ctk.CTkFrame(header, fg_color="transparent")
        right_header.grid(row=0, column=1, sticky="e", padx=16)

        self._model_label = ctk.CTkLabel(
            right_header, text=agent.current_model,
            font=("SF Mono", 10), text_color=C_ACCENT
        )
        self._model_label.pack(side="right", padx=(0, 8))

        # User name
        ctk.CTkLabel(right_header, text="Ben Richards",
                      font=("SF Pro", 12), text_color=C_TEXT_DIM).pack(side="right", padx=(0, 12))

        # Chat display
        self._chat_display = ctk.CTkTextbox(
            chat_frame, fg_color=C_BG, text_color=C_TEXT,
            font=("SF Pro", 13), corner_radius=0, border_width=0,
            wrap="word", state="disabled", spacing3=8
        )
        self._chat_display.grid(row=1, column=0, sticky="nsew", padx=20, pady=(10, 0))

        # Configure tags for chat styling
        self._chat_display._textbox.tag_configure("user_name", foreground=C_TEXT, font=("SF Pro", 11, "bold"))
        self._chat_display._textbox.tag_configure("bot_name", foreground=C_ACCENT, font=("SF Pro", 11, "bold"))
        self._chat_display._textbox.tag_configure("timestamp", foreground=C_TEXT_MUTED, font=("SF Mono", 9))
        self._chat_display._textbox.tag_configure("user_msg", foreground=C_TEXT, font=("SF Pro", 13))
        self._chat_display._textbox.tag_configure("bot_msg", foreground="#e0e0e0", font=("SF Pro", 13))
        self._chat_display._textbox.tag_configure("error_msg", foreground=C_RED, font=("SF Pro", 12))
        self._chat_display._textbox.tag_configure("spacing", font=("SF Pro", 6))

        # Suggestion chips
        sug_frame = ctk.CTkFrame(chat_frame, fg_color="transparent", height=40)
        sug_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 4))

        ctk.CTkLabel(sug_frame, text="\u2728 SUGGESTIONS",
                      font=("SF Mono", 9), text_color=C_TEXT_MUTED).pack(side="left", padx=(0, 10))

        for text in ["Summarize tasks", "Check my schedule", "Analyze performance"]:
            chip = ctk.CTkButton(
                sug_frame, text=text, height=28,
                fg_color=C_SURFACE_2, hover_color="#333",
                text_color=C_TEXT_DIM, font=("SF Pro", 11),
                corner_radius=14, border_width=1, border_color=C_BORDER_SOLID,
                command=lambda t=text: self._send_suggestion(t)
            )
            chip.pack(side="left", padx=(0, 6))

        # Input area
        input_container = ctk.CTkFrame(chat_frame, fg_color=C_SURFACE,
                                         corner_radius=16, border_width=1,
                                         border_color=C_BORDER_SOLID, height=60)
        input_container.grid(row=3, column=0, sticky="ew", padx=20, pady=(8, 8))
        input_container.grid_columnconfigure(1, weight=1)

        # Tool icons (left side)
        tools_frame = ctk.CTkFrame(input_container, fg_color="transparent")
        tools_frame.grid(row=0, column=0, padx=(12, 4), pady=10)

        for icon_text, tooltip in [("\U0001f4ce", "Attach"), ("\U0001f50d", "Search"),
                                    ("\U0001f3a4", "Voice")]:
            btn = ctk.CTkButton(
                tools_frame, text=icon_text, width=28, height=28,
                fg_color="transparent", hover_color=C_SURFACE_2,
                text_color=C_TEXT_MUTED, font=("SF Pro", 14),
                corner_radius=6
            )
            btn.pack(side="left", padx=1)
            if icon_text == "\U0001f3a4":
                btn.configure(command=self._toggle_voice)
                self._voice_btn = btn

        # Text input
        self._msg_input = ctk.CTkEntry(
            input_container,
            placeholder_text="Message Timmy or trigger an agent action...",
            fg_color="transparent", border_width=0,
            text_color=C_TEXT, font=("SF Pro", 13), height=36
        )
        self._msg_input.grid(row=0, column=1, sticky="ew", pady=10)
        self._msg_input.bind("<Return>", self._on_send)
        self._msg_input.bind("<Shift-Return>", lambda e: None)  # Allow shift+enter

        # Right side: mode, jury, send
        right_tools = ctk.CTkFrame(input_container, fg_color="transparent")
        right_tools.grid(row=0, column=2, padx=(4, 12), pady=10)

        # Jury button
        jury_btn = ctk.CTkButton(
            right_tools, text="\u2696", width=28, height=28,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_ACCENT, font=("SF Pro", 16), corner_radius=6,
            command=self._jury_send
        )
        jury_btn.pack(side="left", padx=2)

        # Send button
        send_btn = ctk.CTkButton(
            right_tools, text="\u27a4", width=36, height=36,
            fg_color=C_ACCENT, hover_color="#e6a500",
            text_color=C_BG, font=("SF Pro", 16, "bold"),
            corner_radius=18, command=lambda: self._on_send(None)
        )
        send_btn.pack(side="left", padx=(4, 0))

        # Footer
        footer = ctk.CTkFrame(chat_frame, fg_color="transparent", height=24)
        footer.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 8))

        self._status_dot = ctk.CTkLabel(footer, text="\u25cf", font=("SF Pro", 10),
                                          text_color=C_GREEN)
        self._status_dot.pack(side="left")
        ctk.CTkLabel(footer, text="SYSTEM ONLINE", font=("SF Mono", 9),
                      text_color=C_TEXT_MUTED).pack(side="left", padx=(4, 16))
        ctk.CTkLabel(footer, text="PRIVACY SECURED BY LLTIMMY", font=("SF Mono", 9),
                      text_color=C_TEXT_MUTED).pack(side="left")

        # Render initial chat
        self._render_chat()

    # ------------------------------------------------------------------
    # Chat rendering
    # ------------------------------------------------------------------
    def _render_chat(self):
        self._chat_display.configure(state="normal")
        self._chat_display.delete("1.0", "end")

        for msg in self._chat_history:
            role = msg["role"]
            content = msg["content"]

            if role == "user":
                self._chat_display.insert("end", "Ben  ", "user_name")
            else:
                self._chat_display.insert("end", "\U0001f916 Timmy  ", "bot_name")

            raw_ts = msg.get("ts", "")
            try:
                ts = datetime.fromisoformat(raw_ts).strftime("%I:%M %p") if raw_ts else datetime.now().strftime("%I:%M %p")
            except ValueError:
                ts = datetime.now().strftime("%I:%M %p")
            self._chat_display.insert("end", ts + "\n", "timestamp")

            tag = "user_msg" if role == "user" else "bot_msg"
            self._chat_display.insert("end", content + "\n\n", tag)

        self._chat_display.configure(state="disabled")
        self._chat_display.see("end")

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------
    def _on_send(self, event):
        text = self._msg_input.get().strip()
        if not text or self._agent_working:
            return

        self._msg_input.delete(0, "end")
        self._append_message("user", text)
        self._set_working(True)

        # Run agent in background
        threading.Thread(target=self._run_agent, args=(text,), daemon=True).start()

    def _run_agent(self, user_message: str):
        """Run the agent's ReAct loop and stream response."""
        full_response = ""
        try:
            async def _do():
                nonlocal full_response
                async for chunk in agent.run(user_message):
                    full_response += chunk
                    # Throttle UI updates to max 10/sec to prevent flicker
                    now = time.time()
                    if now - self._last_stream_update > 0.1:
                        self._last_stream_update = now
                        self.after(0, self._update_streaming_response, full_response)

            future = asyncio.run_coroutine_threadsafe(_do(), _loop)
            future.result(timeout=300)  # 5 minute timeout

        except Exception as e:
            full_response = f"Error: {e}"
            logger.error(f"Agent error: {e}")

        # Final update
        self.after(0, self._finalize_response, full_response)

    def _update_streaming_response(self, partial):
        """Update the last bot message in the chat display during streaming.
        Uses incremental update instead of full re-render for performance."""
        if self._chat_history and self._chat_history[-1]["role"] == "assistant":
            self._chat_history[-1]["content"] = partial
        else:
            self._chat_history.append({
                "role": "assistant", "content": partial,
                "ts": datetime.now().isoformat()
            })
        # Incremental update: only rewrite the streaming message area
        self._chat_display.configure(state="normal")
        # Find the last bot marker and replace everything after it
        try:
            last_bot = self._chat_display._textbox.search(
                "\U0001f916 Timmy", "end", backwards=True, stopindex="1.0")
            if last_bot:
                # Delete from the line after the bot name+timestamp to end
                line_start = self._chat_display._textbox.index(f"{last_bot} linestart")
                # Go to next line (after the timestamp line)
                next_line = self._chat_display._textbox.index(f"{last_bot} + 1 lines linestart")
                self._chat_display._textbox.delete(next_line, "end")
                self._chat_display._textbox.insert("end", partial + "\n\n", "bot_msg")
            else:
                # Fallback: full render
                self._render_chat()
                return
        except Exception:
            self._render_chat()
            return
        self._chat_display.configure(state="disabled")
        self._chat_display.see("end")

    def _finalize_response(self, full_text):
        """Finalize the response after agent completes."""
        if self._chat_history and self._chat_history[-1]["role"] == "assistant":
            self._chat_history[-1]["content"] = full_text
        else:
            self._chat_history.append({"role": "assistant", "content": full_text})
        # NOTE: Do NOT call memory.save_message here — agent_core.py already persists
        # the assistant response in its run() method. Double-saving corrupts memory.
        self._render_chat()
        self._set_working(False)
        # Refresh sidebar data
        self._refresh_tab(self._current_tab)

    def _set_working(self, working: bool):
        self._agent_working = working
        if working:
            self._status_dot.configure(text_color=C_ACCENT)
        else:
            self._status_dot.configure(text_color=C_GREEN)

    def _send_suggestion(self, text):
        self._msg_input.delete(0, "end")
        self._msg_input.insert(0, text)
        self._on_send(None)

    # ------------------------------------------------------------------
    # Jury mode
    # ------------------------------------------------------------------
    def _jury_send(self):
        text = self._msg_input.get().strip()
        if not text or self._agent_working:
            return
        self._msg_input.delete(0, "end")
        self._append_message("user", f"[JURY MODE] {text}")
        self._set_working(True)
        threading.Thread(target=self._run_jury, args=(text,), daemon=True).start()

    def _run_jury(self, query):
        full = ""
        try:
            async def _do():
                nonlocal full
                async for chunk in agent.run(
                    f"Use panel_discussion tool to get opinions from multiple models about: {query}"
                ):
                    full += chunk
            future = asyncio.run_coroutine_threadsafe(_do(), _loop)
            future.result(timeout=300)
        except Exception as e:
            full = f"Jury error: {e}"
        self.after(0, self._finalize_response, full)

    # ------------------------------------------------------------------
    # Voice (placeholder - needs system-level audio)
    # ------------------------------------------------------------------
    def _toggle_voice(self):
        # macOS speech recognition via AppleScript
        try:
            subprocess.Popen(["osascript", "-e",
                'tell application "System Events" to key code 63 using {command down}'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------
    def _get_model_choices(self):
        try:
            import requests
            host = config.get("ollama_host", "http://localhost:11434")
            resp = requests.get(f"{host}/api/tags", timeout=5)
            models = [m["name"] for m in resp.json().get("models", [])]
            return models if models else [config.get("primary_model", "qwen3:30b").replace("ollama/", "")]
        except Exception:
            return [config.get("primary_model", "qwen3:30b").replace("ollama/", "")]

    def _on_model_change(self, model_name):
        agent.set_model(model_name)
        self._model_label.configure(text=model_name)

    def _refresh_models(self):
        models = self._get_model_choices()
        self._model_menu.configure(values=models)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def _new_session(self):
        """Start a fresh session (clear chat, keep memory)."""
        self._chat_history.clear()
        memory.clear_day()
        self._append_message("assistant", "New session started. I'm ready to assist.")

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------
    def _start_background_threads(self):
        # Doctor health check
        def _doctor_check():
            while True:
                try:
                    if DOCTOR_PID_FILE.exists():
                        pid = int(DOCTOR_PID_FILE.read_text().strip())
                        try:
                            os.kill(pid, 0)
                        except (ProcessLookupError, PermissionError):
                            self._start_doctor()
                    else:
                        self._start_doctor()
                except Exception:
                    pass
                time.sleep(30)

        threading.Thread(target=_doctor_check, daemon=True).start()

        # Calendar event checker
        if scheduler:
            def _cal_check():
                while True:
                    try:
                        triggered = scheduler.check_due()
                        for ev in triggered:
                            title = ev.get("title", "Event")
                            # Send macOS notification (escape quotes to prevent AppleScript injection)
                            safe_title = title.replace('\\', '\\\\').replace('"', '\\"')
                            subprocess.run(
                                ["osascript", "-e",
                                 f'display notification "{safe_title}" with title "LLTimmy Calendar" sound name "Funk"'],
                                capture_output=True, timeout=5
                            )
                    except Exception:
                        pass
                    time.sleep(60)
            threading.Thread(target=_cal_check, daemon=True).start()

        # Idle evolution
        evolution.start_idle_research(agent)

        # Periodic sidebar refresh
        def _ui_refresh():
            while True:
                try:
                    self.after(0, lambda: self._refresh_tab(self._current_tab))
                except Exception:
                    pass
                time.sleep(30)
        threading.Thread(target=_ui_refresh, daemon=True).start()

        # Ollama warmup
        def _warmup():
            try:
                import requests
                host = config.get("ollama_host", "http://localhost:11434")
                requests.post(
                    f"{host}/api/chat",
                    json={"model": agent.current_model,
                          "messages": [{"role": "user", "content": "hi"}],
                          "stream": False},
                    timeout=60
                )
                logger.info("Ollama warm-up complete")
            except Exception as e:
                logger.warning(f"Warmup failed: {e}")
        threading.Thread(target=_warmup, daemon=True).start()

    def _start_doctor(self):
        """Ensure Doctor daemon is running."""
        try:
            venv_py = str(BASE_DIR / ".venv" / "bin" / "python3")
            subprocess.Popen(
                [venv_py, str(BASE_DIR / "doctor.py")],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info("Doctor daemon started")
        except Exception as e:
            logger.warning(f"Failed to start Doctor: {e}")

    # ------------------------------------------------------------------
    # Clean exit
    # ------------------------------------------------------------------
    def _on_close(self):
        """Clean shutdown - stop Timmy, Doctor keeps running."""
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        evolution.stop_idle_research()
        self.destroy()


# ===========================================================================
# Entry point
# ===========================================================================
if __name__ == "__main__":
    app = LLTimmyApp()
    app.mainloop()
