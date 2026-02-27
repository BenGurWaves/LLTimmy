"""
LLTimmy — Quiet Luxury Native Desktop App
CustomTkinter · macOS · Deep Matte Charcoal + Amber/Gold

Architecture:
  Doctor runs as background daemon (always on).
  Timmy agent runs in this process.
  No browser, no localhost, no Gradio — pure native desktop.

Design language: Quiet luxury — calm sophistication, restrained elegance,
premium minimalism. Every pixel has purpose and breathing room.
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
# Path setup — src/ lives one level inside the project root
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

CONFIG_PATH = BASE_DIR / "config.json"
PID_FILE = Path("/tmp/timmy.pid")
DOCTOR_PID_FILE = Path("/tmp/doctor.pid")
MEMORY_BASE = BASE_DIR / "memory"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("lltimmy")

# PID file written after successful init — see __main__ block below

# Load config
try:
    with open(CONFIG_PATH) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"FATAL: Cannot load config.json: {e}")
    sys.exit(1)

# Import backend modules (from project root via sys.path)
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
agent = AgentCore(
    config=config, memory_manager=memory, tools_system=tools,
    scheduler=scheduler, task_mgr=task_mgr,
)

# Resolve venv Python path once for reuse
_venv_path = BASE_DIR / ".venv" / "bin" / "python3"
VENV_PYTHON = str(_venv_path) if _venv_path.exists() else sys.executable


# ═══════════════════════════════════════════════════════════════════════════
# Quiet Luxury Color Palette
# ═══════════════════════════════════════════════════════════════════════════
C_BG         = "#0b0b0b"    # Deep matte charcoal — the canvas
C_SURFACE    = "#131313"    # Panel / sidebar surface
C_SURFACE_2  = "#1a1a1a"    # Elevated surface (cards, hovers)
C_SURFACE_3  = "#222222"    # Hover / active states
C_BORDER     = "#1c1c1c"    # Barely-visible border (~#ffffff08 equivalent on dark)
C_BORDER_VIS = "#282828"    # Slightly more visible border for inputs
C_ACCENT     = "#ffb700"    # Warm amber/gold — used sparingly
C_ACCENT_DIM = "#2a2000"    # Dim amber for subtle bg hints
C_ACCENT_HOV = "#e6a500"    # Accent hover
C_TEXT       = "#f0f0f2"    # Primary text — not pure white
C_TEXT_SEC   = "#86868b"    # Secondary text
C_TEXT_MUTED = "#48484a"    # Muted / disabled
C_GREEN      = "#30d158"    # Online / success
C_RED        = "#ff453a"    # Error / failure
C_INPUT_BG   = "#161616"    # Input field background


# ═══════════════════════════════════════════════════════════════════════════
# CustomTkinter theme + async loop
# ═══════════════════════════════════════════════════════════════════════════
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

_loop = asyncio.new_event_loop()


def _run_loop():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


_loop_thread = threading.Thread(target=_run_loop, daemon=True)
_loop_thread.start()


# ═══════════════════════════════════════════════════════════════════════════
# Main Application
# ═══════════════════════════════════════════════════════════════════════════
class LLTimmyApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title("LLTimmy")
        self.geometry("1400x900")
        self.minsize(1000, 700)
        self.configure(fg_color=C_BG)

        # ── State ──────────────────────────────────────────────────────
        self._agent_working = False
        self._chat_history: List[Dict] = []
        self._current_tab = "Tasks"
        self._show_reasoning = True
        self._last_stream_update = 0.0

        # Load today's conversation
        self._load_chat_history()

        # Build everything
        self._build_ui()
        self._start_background_threads()

        # Greeting
        if not self._chat_history:
            self._append_message(
                "assistant",
                "I'm Timmy — Ben's AI agent. Workspace is monitored. Ready when you are.",
            )

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ──────────────────────────────────────────────────────────────────
    # Chat history persistence
    # ──────────────────────────────────────────────────────────────────
    def _load_chat_history(self):
        msgs = memory.load_current_day()
        self._chat_history = [
            {"role": m["role"], "content": m["content"],
             "ts": m.get("timestamp", datetime.now().isoformat())}
            for m in msgs
        ]

    def _append_message(self, role: str, content: str):
        self._chat_history.append({
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(),
        })
        # Only save non-user messages here; agent_core.run() saves user messages
        if role != "user":
            memory.save_message(role, content)
        self._render_chat()

    # ──────────────────────────────────────────────────────────────────
    # UI Construction
    # ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._main_frame = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        self._main_frame.pack(fill="both", expand=True)
        self._main_frame.grid_columnconfigure(1, weight=1)
        self._main_frame.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_chat_area()

    # ══════════════════════════════════════════════════════════════════
    #  SIDEBAR — 280 px, deep surface
    # ══════════════════════════════════════════════════════════════════
    def _build_sidebar(self):
        sb = ctk.CTkFrame(
            self._main_frame, width=280, fg_color=C_SURFACE,
            corner_radius=0, border_width=1, border_color=C_BORDER,
        )
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_rowconfigure(3, weight=1)
        self._sidebar = sb

        # ── Brand header ──────────────────────────────────────────────
        hdr = ctk.CTkFrame(sb, fg_color="transparent", height=60)
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 4))
        hdr.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(hdr, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            brand, text="LLTimmy",
            font=("SF Pro Display", 24, "bold"), text_color=C_TEXT,
        ).pack(side="left")

        # Gold status dot
        ctk.CTkLabel(
            brand, text="\u25cf", font=("SF Pro", 8),
            text_color=C_ACCENT, width=10,
        ).pack(side="left", padx=(8, 0), pady=(2, 0))

        # New-session button
        ctk.CTkButton(
            hdr, text="+", width=32, height=32,
            fg_color=C_SURFACE_2, hover_color=C_SURFACE_3,
            text_color=C_TEXT_SEC, font=("SF Pro", 16),
            corner_radius=16, command=self._new_session,
        ).grid(row=0, column=1, sticky="e")

        # ── Agent status cards ────────────────────────────────────────
        cards = ctk.CTkFrame(sb, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=16, pady=(8, 4))

        self._agent_card = self._make_agent_card(
            cards, "Agent Timmy", "Ready", C_ACCENT, active=True)
        self._agent_card.pack(fill="x", pady=(0, 4))

        self._doctor_card = self._make_agent_card(
            cards, "Doctor", "Watching", C_TEXT_MUTED, active=False)
        self._doctor_card.pack(fill="x")

        # ── Tab row: Tasks · Memory · Calendar  +  ⋯ ─────────────────
        tab_row = ctk.CTkFrame(sb, fg_color="transparent", height=36)
        tab_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(16, 0))

        self._tab_buttons = {}
        for name in ("Tasks", "Memory", "Calendar"):
            active = name == "Tasks"
            btn = ctk.CTkButton(
                tab_row, text=name, width=72, height=30,
                fg_color=C_ACCENT if active else "transparent",
                hover_color=C_SURFACE_2,
                text_color=C_BG if active else C_TEXT_SEC,
                font=("SF Pro", 12, "bold" if active else "normal"),
                corner_radius=15,
                command=lambda t=name: self._switch_tab(t),
            )
            btn.pack(side="left", padx=(0, 4))
            self._tab_buttons[name] = btn

        # Three-dot menu
        ctk.CTkButton(
            tab_row, text="\u22ef", width=30, height=30,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_SEC, font=("SF Pro", 16),
            corner_radius=15, command=self._show_extended_menu,
        ).pack(side="right")

        # ── Tab content area ──────────────────────────────────────────
        self._tab_content = ctk.CTkFrame(sb, fg_color="transparent")
        self._tab_content.grid(row=3, column=0, sticky="nsew", padx=16, pady=(10, 8))

        self._tabs = {}
        self._build_tasks_tab()
        self._build_memory_tab()
        self._build_calendar_tab()
        self._build_trace_tab()
        self._build_evolution_tab()
        self._build_console_tab()
        self._build_settings_tab()
        self._show_tab("Tasks")

        # ── Bottom: quick-add + clear ─────────────────────────────────
        btm = ctk.CTkFrame(sb, fg_color="transparent", height=64)
        btm.grid(row=4, column=0, sticky="sew", padx=16, pady=(0, 16))

        self._quick_add = ctk.CTkEntry(
            btm, placeholder_text="Quick add task\u2026",
            fg_color=C_INPUT_BG, border_color=C_BORDER_VIS,
            text_color=C_TEXT, font=("SF Pro", 12), height=34,
            corner_radius=12,
        )
        self._quick_add.pack(fill="x", pady=(0, 6))
        self._quick_add.bind("<Return>", self._quick_add_task)

        ctk.CTkButton(
            btm, text="CLEAR COMPLETED", height=24,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_MUTED, font=("SF Mono", 9),
            corner_radius=4, command=self._clear_completed_tasks,
        ).pack(fill="x")

    # ── Agent card widget ─────────────────────────────────────────────
    def _make_agent_card(self, parent, name, status_text, dot_color, active):
        kw = {"border_width": 1, "border_color": C_ACCENT} if active else {}
        card = ctk.CTkFrame(
            parent,
            fg_color=C_ACCENT_DIM if active else C_SURFACE_2,
            corner_radius=12, height=48, **kw,
        )
        card.pack_propagate(False)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=8)

        ctk.CTkLabel(
            inner, text="\u25cf", font=("SF Pro", 8),
            text_color=dot_color, width=12,
        ).pack(side="left", padx=(0, 8))

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            info, text=name, font=("SF Pro", 13, "bold"),
            text_color=C_TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            info, text=status_text, font=("SF Pro", 10),
            text_color=C_TEXT_SEC, anchor="w",
        ).pack(anchor="w")
        return card

    # ══════════════════════════════════════════════════════════════════
    #  TAB BUILDERS
    # ══════════════════════════════════════════════════════════════════
    def _build_tasks_tab(self):
        f = ctk.CTkScrollableFrame(
            self._tab_content, fg_color="transparent",
            scrollbar_button_color=C_SURFACE_2,
        )
        self._tabs["Tasks"] = f
        self._tasks_container = f

    def _build_memory_tab(self):
        f = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Memory"] = f

        self._mem_stats_label = ctk.CTkLabel(
            f, text="", font=("SF Mono", 10),
            text_color=C_TEXT_SEC, anchor="w", justify="left",
        )
        self._mem_stats_label.pack(fill="x", pady=(0, 10))

        self._mem_search = ctk.CTkEntry(
            f, placeholder_text="Search memories\u2026",
            fg_color=C_INPUT_BG, border_color=C_BORDER_VIS,
            text_color=C_TEXT, font=("SF Pro", 12), height=34,
            corner_radius=12,
        )
        self._mem_search.pack(fill="x", pady=(0, 10))
        self._mem_search.bind("<Return>", lambda e: self._search_memory(self._mem_search.get()))

        self._mem_results_frame = ctk.CTkScrollableFrame(
            f, fg_color="transparent", height=300,
            scrollbar_button_color=C_SURFACE_2,
        )
        self._mem_results_frame.pack(fill="both", expand=True)

    def _build_calendar_tab(self):
        f = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Calendar"] = f

        self._cal_container = ctk.CTkScrollableFrame(
            f, fg_color="transparent", height=250,
            scrollbar_button_color=C_SURFACE_2,
        )
        self._cal_container.pack(fill="both", expand=True, pady=(0, 10))

        add_f = ctk.CTkFrame(f, fg_color="transparent")
        add_f.pack(fill="x")

        self._cal_title = ctk.CTkEntry(
            add_f, placeholder_text="Event title\u2026",
            fg_color=C_INPUT_BG, border_color=C_BORDER_VIS,
            text_color=C_TEXT, font=("SF Pro", 11), height=30,
            corner_radius=10,
        )
        self._cal_title.pack(fill="x", pady=(0, 4))

        row = ctk.CTkFrame(add_f, fg_color="transparent")
        row.pack(fill="x")
        self._cal_due = ctk.CTkEntry(
            row, placeholder_text="Due: YYYY-MM-DD HH:MM",
            fg_color=C_INPUT_BG, border_color=C_BORDER_VIS,
            text_color=C_TEXT, font=("SF Pro", 11), height=30,
            corner_radius=10,
        )
        self._cal_due.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            row, text="Add", width=50, height=30,
            fg_color=C_ACCENT, text_color=C_BG,
            font=("SF Pro", 11, "bold"), corner_radius=10,
            hover_color=C_ACCENT_HOV, command=self._add_calendar_event,
        ).pack(side="right")

    def _build_trace_tab(self):
        f = ctk.CTkScrollableFrame(
            self._tab_content, fg_color="transparent",
            scrollbar_button_color=C_SURFACE_2,
        )
        self._tabs["Trace"] = f
        self._trace_container = f

    def _build_evolution_tab(self):
        f = ctk.CTkScrollableFrame(
            self._tab_content, fg_color="transparent",
            scrollbar_button_color=C_SURFACE_2,
        )
        self._tabs["Evolution"] = f
        self._evo_container = f

    def _build_console_tab(self):
        f = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Console"] = f
        self._console_text = ctk.CTkTextbox(
            f, fg_color=C_SURFACE_2, text_color=C_GREEN,
            font=("SF Mono", 10), corner_radius=10,
            border_width=1, border_color=C_BORDER, wrap="word",
        )
        self._console_text.pack(fill="both", expand=True)

    def _build_settings_tab(self):
        f = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self._tabs["Settings"] = f

        ctk.CTkLabel(
            f, text="Active Model", font=("SF Pro", 11),
            text_color=C_TEXT_SEC,
        ).pack(anchor="w", pady=(0, 4))

        models = self._get_model_choices()
        self._settings_model_var = ctk.StringVar(master=self, value=agent.current_model)
        self._settings_model_menu = ctk.CTkOptionMenu(
            f, values=models, variable=self._settings_model_var,
            fg_color=C_INPUT_BG, button_color=C_SURFACE_2,
            button_hover_color=C_SURFACE_3, text_color=C_TEXT,
            font=("SF Mono", 11), dropdown_fg_color=C_SURFACE,
            dropdown_text_color=C_TEXT, dropdown_hover_color=C_SURFACE_2,
            corner_radius=10, command=self._on_model_change,
        )
        self._settings_model_menu.pack(fill="x", pady=(0, 12))

        ctk.CTkButton(
            f, text="Refresh Models", height=30,
            fg_color=C_SURFACE_2, hover_color=C_SURFACE_3,
            text_color=C_TEXT_SEC, font=("SF Pro", 11),
            corner_radius=10, command=self._refresh_models,
        ).pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            f, text=f"Base  {BASE_DIR}\nMem   {MEMORY_BASE}",
            font=("SF Mono", 9), text_color=C_TEXT_MUTED, justify="left",
        ).pack(anchor="w")

    # ══════════════════════════════════════════════════════════════════
    #  TAB SWITCHING + REFRESH
    # ══════════════════════════════════════════════════════════════════
    def _switch_tab(self, tab_name):
        self._current_tab = tab_name
        for name, btn in self._tab_buttons.items():
            if name == tab_name:
                btn.configure(
                    fg_color=C_ACCENT, text_color=C_BG,
                    font=("SF Pro", 12, "bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent", text_color=C_TEXT_SEC,
                    font=("SF Pro", 12),
                )
        self._show_tab(tab_name)

    def _show_tab(self, tab_name):
        for frame in self._tabs.values():
            frame.pack_forget()
        if tab_name in self._tabs:
            self._tabs[tab_name].pack(fill="both", expand=True)
        self._refresh_tab(tab_name)

    def _show_extended_menu(self):
        menu = ctk.CTkToplevel(self)
        menu.title("")
        menu.geometry("160x190")
        menu.overrideredirect(True)
        menu.configure(fg_color=C_SURFACE)
        menu.attributes("-topmost", True)

        x = self._sidebar.winfo_rootx() + 180
        y = self._sidebar.winfo_rooty() + 220
        menu.geometry(f"+{x}+{y}")

        for name in ("Trace", "Evolution", "Console", "Settings"):
            ctk.CTkButton(
                menu, text=name, height=34,
                fg_color="transparent", hover_color=C_SURFACE_2,
                text_color=C_TEXT, font=("SF Pro", 12),
                anchor="w", corner_radius=6,
                command=lambda t=name, m=menu: (m.destroy(), self._show_tab(t)),
            ).pack(fill="x", padx=6, pady=2)

        menu.bind("<FocusOut>", lambda e: menu.destroy())
        menu.focus_set()

    def _refresh_tab(self, tab_name):
        dispatch = {
            "Tasks": self._render_tasks,
            "Memory": self._render_memory_stats,
            "Calendar": self._render_calendar,
            "Trace": self._render_trace,
            "Evolution": self._render_evolution,
            "Console": self._render_console,
        }
        fn = dispatch.get(tab_name)
        if fn:
            fn()

    # ══════════════════════════════════════════════════════════════════
    #  TASKS
    # ══════════════════════════════════════════════════════════════════
    def _render_tasks(self):
        for w in self._tasks_container.winfo_children():
            w.destroy()

        all_tasks = task_mgr.get_all_tasks()
        if not all_tasks:
            ctk.CTkLabel(
                self._tasks_container, text="No tasks yet.",
                font=("SF Pro", 12), text_color=C_TEXT_MUTED,
            ).pack(pady=24)
            return

        urg_colors = {
            "critical": C_RED, "high": "#ff9500",
            "normal": C_TEXT_SEC, "low": "#636366",
        }
        status_icons = {
            "pending": "\u25cb", "in_progress": "\u25d4",
            "completed": "\u25cf", "failed": "\u2716", "paused": "\u25a0",
        }

        for task in sorted(
            all_tasks,
            key=lambda t: (
                {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(t.urgency, 2),
                t.priority,
            ),
        ):
            row = ctk.CTkFrame(
                self._tasks_container, fg_color=C_SURFACE_2,
                corner_radius=12, height=44,
            )
            row.pack(fill="x", pady=(0, 6))
            row.pack_propagate(False)

            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=12, pady=6)

            # Urgency accent line
            if task.urgency in ("critical", "high"):
                sliver = ctk.CTkFrame(
                    row, width=3,
                    fg_color=urg_colors.get(task.urgency, C_TEXT_SEC),
                    corner_radius=2,
                )
                sliver.place(x=0, y=6, relheight=0.7)

            icon = status_icons.get(task.status, "\u25cb")
            icon_c = (
                C_GREEN if task.status == "completed"
                else C_RED if task.status == "failed"
                else C_ACCENT
            )
            ctk.CTkButton(
                inner, text=icon, width=22, height=22,
                fg_color="transparent", hover_color=C_SURFACE,
                text_color=icon_c, font=("SF Pro", 14),
                corner_radius=11,
                command=lambda tid=task.id: self._toggle_task(tid),
            ).pack(side="left", padx=(0, 8))

            title_c = C_TEXT_MUTED if task.status == "completed" else C_TEXT
            ctk.CTkLabel(
                inner, text=task.title[:40], font=("SF Pro", 12),
                text_color=title_c, anchor="w",
            ).pack(side="left", fill="x", expand=True)

            if task.status == "in_progress" and task.progress > 0:
                ctk.CTkLabel(
                    inner, text=f"{task.progress}%",
                    font=("SF Mono", 9), text_color=C_ACCENT,
                ).pack(side="right")

    def _toggle_task(self, task_id):
        task = task_mgr.get_task(task_id)
        if not task:
            return
        cycle = {
            "pending": "in_progress", "in_progress": "completed",
            "completed": "pending", "failed": "pending",
            "paused": "in_progress",
        }
        task_mgr.update_status(task_id, cycle.get(task.status, "pending"))
        self._render_tasks()

    def _quick_add_task(self, event=None):
        text = self._quick_add.get().strip()
        if not text:
            return
        task_mgr.add_task(text)
        self._quick_add.delete(0, "end")
        self._render_tasks()

    def _clear_completed_tasks(self):
        for t in [t for t in task_mgr.get_all_tasks() if t.status == "completed"]:
            task_mgr.remove_task(t.id)
        self._render_tasks()

    # ══════════════════════════════════════════════════════════════════
    #  MEMORY
    # ══════════════════════════════════════════════════════════════════
    def _render_memory_stats(self):
        stats = memory.get_memory_stats()
        self._mem_stats_label.configure(
            text=(
                f"Today: {stats['today_messages']} msgs\n"
                f"Subconscious: {stats['subconscious_entries']} entries\n"
                f"Graph: {stats['graph_entities']} entities"
            )
        )

    def _search_memory(self, query):
        if not query.strip():
            return
        results = memory.search_memory(query.strip())
        for w in self._mem_results_frame.winfo_children():
            w.destroy()
        if not results:
            ctk.CTkLabel(
                self._mem_results_frame, text="No results.",
                font=("SF Pro", 11), text_color=C_TEXT_MUTED,
            ).pack(pady=12)
            return
        for r in results[:10]:
            content = r.get("content", "")[:120]
            ts = r.get("metadata", {}).get("timestamp", "")[:16]
            card = ctk.CTkFrame(
                self._mem_results_frame, fg_color=C_SURFACE_2,
                corner_radius=10,
            )
            card.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(
                card, text=content, font=("SF Pro", 10),
                text_color=C_TEXT, wraplength=220, justify="left",
            ).pack(fill="x", padx=10, pady=(8, 2))
            ctk.CTkLabel(
                card, text=ts, font=("SF Mono", 8), text_color=C_TEXT_MUTED,
            ).pack(anchor="w", padx=10, pady=(0, 8))

    # ══════════════════════════════════════════════════════════════════
    #  CALENDAR
    # ══════════════════════════════════════════════════════════════════
    def _render_calendar(self):
        for w in self._cal_container.winfo_children():
            w.destroy()
        if not scheduler:
            ctk.CTkLabel(
                self._cal_container, text="Calendar unavailable.",
                font=("SF Pro", 11), text_color=C_TEXT_MUTED,
            ).pack(pady=12)
            return
        events = scheduler.get_upcoming(15)
        if not events:
            ctk.CTkLabel(
                self._cal_container, text="No upcoming events.",
                font=("SF Pro", 11), text_color=C_TEXT_MUTED,
            ).pack(pady=12)
            return
        for ev in events:
            card = ctk.CTkFrame(
                self._cal_container, fg_color=C_SURFACE_2, corner_radius=10,
            )
            card.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(
                card, text=ev["title"][:40], font=("SF Pro", 11),
                text_color=C_TEXT, anchor="w",
            ).pack(anchor="w", padx=10, pady=(8, 0))
            ctk.CTkLabel(
                card, text=ev.get("due", "")[:16], font=("SF Mono", 9),
                text_color=C_ACCENT,
            ).pack(anchor="w", padx=10, pady=(0, 8))

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
            logger.warning("Calendar add error: %s", e)

    # ══════════════════════════════════════════════════════════════════
    #  TRACE
    # ══════════════════════════════════════════════════════════════════
    def _render_trace(self):
        for w in self._trace_container.winfo_children():
            w.destroy()
        log_path = BASE_DIR / "tim_audit.log"
        if not log_path.exists():
            ctk.CTkLabel(
                self._trace_container, text="No trace data.",
                font=("SF Pro", 11), text_color=C_TEXT_MUTED,
            ).pack(pady=12)
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
                    card = ctk.CTkFrame(
                        self._trace_container, fg_color=C_SURFACE_2,
                        corner_radius=10,
                    )
                    card.pack(fill="x", pady=(0, 4))
                    ctk.CTkLabel(
                        card, text=tool, font=("SF Pro", 10, "bold"),
                        text_color=C_ACCENT,
                    ).pack(anchor="w", padx=10, pady=(6, 0))
                    ctk.CTkLabel(
                        card, text=result, font=("SF Mono", 9),
                        text_color=C_TEXT_SEC, wraplength=230, justify="left",
                    ).pack(anchor="w", padx=10, pady=(0, 2))
                    ctk.CTkLabel(
                        card, text=ts, font=("SF Mono", 8),
                        text_color=C_TEXT_MUTED,
                    ).pack(anchor="w", padx=10, pady=(0, 6))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  EVOLUTION + CONSOLE
    # ══════════════════════════════════════════════════════════════════
    def _render_evolution(self):
        for w in self._evo_container.winfo_children():
            w.destroy()
        summary = evolution.get_evolution_summary()
        ctk.CTkLabel(
            self._evo_container, text=summary,
            font=("SF Mono", 10), text_color=C_TEXT_SEC,
            wraplength=240, justify="left",
        ).pack(fill="x", padx=4, pady=4)

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

    # ══════════════════════════════════════════════════════════════════
    #  CHAT AREA — the main conversation view
    # ══════════════════════════════════════════════════════════════════
    def _build_chat_area(self):
        chat = ctk.CTkFrame(self._main_frame, fg_color=C_BG, corner_radius=0)
        chat.grid(row=0, column=1, sticky="nsew")
        chat.grid_rowconfigure(1, weight=1)
        chat.grid_columnconfigure(0, weight=1)

        # ── Header bar ────────────────────────────────────────────────
        hdr = ctk.CTkFrame(
            chat, fg_color=C_SURFACE, height=56,
            corner_radius=0, border_width=0,
        )
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(hdr, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=20, pady=12)

        ctk.CTkLabel(
            left, text="Secure Session",
            font=("SF Pro Display", 15, "bold"), text_color=C_TEXT,
        ).pack(side="left")

        right = ctk.CTkFrame(hdr, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=20)

        # See Reasoning toggle
        self._reasoning_var = ctk.BooleanVar(master=self, value=True)
        reasoning_cb = ctk.CTkSwitch(
            right, text="Reasoning", variable=self._reasoning_var,
            font=("SF Pro", 10), text_color=C_TEXT_SEC,
            fg_color=C_SURFACE_2, progress_color=C_ACCENT,
            button_color=C_TEXT_SEC, button_hover_color=C_TEXT,
            width=36, height=18,
            command=self._toggle_reasoning,
        )
        reasoning_cb.pack(side="right", padx=(0, 16))

        # Model badge
        self._model_label = ctk.CTkLabel(
            right, text=agent.current_model,
            font=("SF Mono", 10), text_color=C_ACCENT,
            fg_color=C_SURFACE_2, corner_radius=8,
        )
        self._model_label.pack(side="right", padx=(0, 12))
        self._model_label.configure(width=160, height=24)

        # ── Chat display ──────────────────────────────────────────────
        self._chat_display = ctk.CTkTextbox(
            chat, fg_color=C_BG, text_color=C_TEXT,
            font=("SF Pro", 14), corner_radius=0, border_width=0,
            wrap="word", state="disabled", spacing3=6,
        )
        self._chat_display.grid(row=1, column=0, sticky="nsew", padx=24, pady=(12, 0))

        # Text tags for message styling
        tb = self._chat_display._textbox
        tb.tag_configure("user_name",
                         foreground=C_TEXT, font=("SF Pro Display", 12, "bold"),
                         spacing1=12)
        tb.tag_configure("bot_name",
                         foreground=C_ACCENT, font=("SF Pro Display", 12, "bold"),
                         spacing1=12)
        tb.tag_configure("timestamp",
                         foreground=C_TEXT_MUTED, font=("SF Mono", 9))
        tb.tag_configure("user_msg",
                         foreground=C_TEXT, font=("SF Pro", 14),
                         lmargin1=4, lmargin2=4, spacing1=4, spacing3=6)
        tb.tag_configure("bot_msg",
                         foreground="#e8e8ea", font=("SF Pro", 14),
                         lmargin1=4, lmargin2=4, spacing1=4, spacing3=6)
        tb.tag_configure("error_msg",
                         foreground=C_RED, font=("SF Pro", 13))
        tb.tag_configure("dim_msg",
                         foreground=C_TEXT_MUTED, font=("SF Mono", 11),
                         lmargin1=4, lmargin2=4, spacing1=2, spacing3=2)
        tb.tag_configure("sep", font=("SF Pro", 2))

        # ── Suggestion chips ──────────────────────────────────────────
        sug = ctk.CTkFrame(chat, fg_color="transparent", height=40)
        sug.grid(row=2, column=0, sticky="ew", padx=24, pady=(10, 4))

        for text in ("Summarize tasks", "Check my schedule", "Analyze performance"):
            ctk.CTkButton(
                sug, text=text, height=30,
                fg_color=C_SURFACE_2, hover_color=C_SURFACE_3,
                text_color=C_TEXT_SEC, font=("SF Pro", 11),
                corner_radius=15, border_width=1, border_color=C_BORDER,
                command=lambda t=text: self._send_suggestion(t),
            ).pack(side="left", padx=(0, 8))

        # ── Input bar ─────────────────────────────────────────────────
        inp = ctk.CTkFrame(
            chat, fg_color=C_SURFACE,
            corner_radius=24, border_width=1, border_color=C_BORDER,
            height=56,
        )
        inp.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 6))
        inp.grid_columnconfigure(1, weight=1)

        # Left tool icons
        tools_f = ctk.CTkFrame(inp, fg_color="transparent")
        tools_f.grid(row=0, column=0, padx=(16, 4), pady=10)

        for icon, tip in (("\U0001f4ce", "Attach"), ("\U0001f3a4", "Voice")):
            b = ctk.CTkButton(
                tools_f, text=icon, width=28, height=28,
                fg_color="transparent", hover_color=C_SURFACE_2,
                text_color=C_TEXT_MUTED, font=("SF Pro", 14),
                corner_radius=14,
            )
            b.pack(side="left", padx=1)
            if icon == "\U0001f3a4":
                b.configure(command=self._toggle_voice)

        # Text entry
        self._msg_input = ctk.CTkEntry(
            inp,
            placeholder_text="Message Timmy\u2026",
            fg_color="transparent", border_width=0,
            text_color=C_TEXT, font=("SF Pro", 14), height=36,
        )
        self._msg_input.grid(row=0, column=1, sticky="ew", pady=10)
        self._msg_input.bind("<Return>", self._on_send)

        # Right: jury + send
        right_tools = ctk.CTkFrame(inp, fg_color="transparent")
        right_tools.grid(row=0, column=2, padx=(4, 12), pady=10)

        ctk.CTkButton(
            right_tools, text="\u2696", width=30, height=30,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_ACCENT, font=("SF Pro", 16),
            corner_radius=15, command=self._jury_send,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            right_tools, text="\u27a4", width=38, height=38,
            fg_color=C_ACCENT, hover_color=C_ACCENT_HOV,
            text_color=C_BG, font=("SF Pro", 16, "bold"),
            corner_radius=19, command=lambda: self._on_send(None),
        ).pack(side="left", padx=(4, 0))

        # ── Footer ────────────────────────────────────────────────────
        footer = ctk.CTkFrame(chat, fg_color="transparent", height=22)
        footer.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 8))

        self._status_dot = ctk.CTkLabel(
            footer, text="\u25cf", font=("SF Pro", 8),
            text_color=C_GREEN,
        )
        self._status_dot.pack(side="left")
        self._status_label = ctk.CTkLabel(
            footer, text="Online", font=("SF Mono", 9),
            text_color=C_TEXT_MUTED,
        )
        self._status_label.pack(side="left", padx=(4, 0))

        self._render_chat()

    # ══════════════════════════════════════════════════════════════════
    #  CHAT RENDERING
    # ══════════════════════════════════════════════════════════════════
    def _render_chat(self):
        self._chat_display.configure(state="normal")
        self._chat_display.delete("1.0", "end")

        for msg in self._chat_history:
            role = msg["role"]
            content = msg["content"]

            # Optionally filter reasoning
            if role == "assistant" and not self._show_reasoning:
                content = self._filter_reasoning(content)

            if role == "user":
                self._chat_display.insert("end", "Ben  ", "user_name")
            else:
                self._chat_display.insert("end", "Timmy  ", "bot_name")

            raw_ts = msg.get("ts", "")
            try:
                ts = datetime.fromisoformat(raw_ts).strftime("%I:%M %p")
            except (ValueError, TypeError):
                ts = ""
            if ts:
                self._chat_display.insert("end", ts, "timestamp")
            self._chat_display.insert("end", "\n", "sep")

            tag = "user_msg" if role == "user" else "bot_msg"
            self._chat_display.insert("end", content + "\n", tag)
            self._chat_display.insert("end", "\n", "sep")

        self._chat_display.configure(state="disabled")
        self._chat_display.see("end")

    def _filter_reasoning(self, text: str) -> str:
        """Strip ReAct Thought/Action/Observation blocks, keep final answer."""
        lines = text.split("\n")
        out = []
        skip = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("Thought:", "Action:", "Action Input:", "Observation:")):
                skip = True
                continue
            if skip and stripped.startswith(("**[", "Final Answer")):
                skip = False
            if not skip:
                out.append(line)
        result = "\n".join(out).strip()
        return result if result else text

    def _toggle_reasoning(self):
        self._show_reasoning = self._reasoning_var.get()
        self._render_chat()

    # ══════════════════════════════════════════════════════════════════
    #  SENDING MESSAGES + STREAMING (bug-fixed)
    # ══════════════════════════════════════════════════════════════════
    def _on_send(self, event):
        text = self._msg_input.get().strip()
        if not text or self._agent_working:
            return
        self._msg_input.delete(0, "end")
        self._append_message("user", text)
        self._set_working(True)
        threading.Thread(target=self._run_agent, args=(text,), daemon=True).start()

    def _run_agent(self, user_message: str):
        """Run agent ReAct loop with streaming. BUG-FIX: agent.run() yields
        the full accumulated response so far, not deltas — use = not +=."""
        full_response = ""
        try:
            async def _do():
                nonlocal full_response
                async for chunk in agent.run(user_message):
                    # CRITICAL: agent.run() yields accumulated text, not deltas
                    full_response = chunk
                    now = time.time()
                    if now - self._last_stream_update > 0.1:
                        self._last_stream_update = now
                        # Pass snapshot — don't let bg thread touch _chat_history
                        snapshot = full_response
                        self.after(0, self._update_streaming, snapshot)

            future = asyncio.run_coroutine_threadsafe(_do(), _loop)
            future.result(timeout=300)
        except Exception as e:
            full_response = f"Error: {e}"
            logger.error("Agent error: %s", e)

        self.after(0, self._finalize_response, full_response)

    def _update_streaming(self, partial):
        """Incremental streaming update — ONLY updates the text widget.
        Does NOT modify _chat_history (that's done in _finalize_response)
        to avoid race conditions between the bg thread and Tk main loop."""
        # Optionally filter reasoning
        display_text = partial
        if not self._show_reasoning:
            display_text = self._filter_reasoning(partial)

        self._chat_display.configure(state="normal")
        try:
            last_bot = self._chat_display._textbox.search(
                "Timmy", "end", backwards=True, stopindex="1.0")
            if last_bot:
                next_line = self._chat_display._textbox.index(
                    f"{last_bot} + 1 lines linestart")
                self._chat_display._textbox.delete(next_line, "end")
                self._chat_display._textbox.insert("end", display_text + "\n\n", "bot_msg")
            else:
                self._render_chat()
                return
        except Exception:
            self._render_chat()
            return
        self._chat_display.configure(state="disabled")
        self._chat_display.see("end")

    def _finalize_response(self, full_text):
        if self._chat_history and self._chat_history[-1]["role"] == "assistant":
            self._chat_history[-1]["content"] = full_text
        else:
            self._chat_history.append({"role": "assistant", "content": full_text})
        # NOTE: Do NOT call memory.save_message here — agent_core already persists
        self._render_chat()
        self._set_working(False)
        self._refresh_tab(self._current_tab)

    def _set_working(self, working: bool):
        self._agent_working = working
        if working:
            self._status_dot.configure(text_color=C_ACCENT)
            self._status_label.configure(text="Working\u2026")
        else:
            self._status_dot.configure(text_color=C_GREEN)
            self._status_label.configure(text="Online")

    def _send_suggestion(self, text):
        self._msg_input.delete(0, "end")
        self._msg_input.insert(0, text)
        self._on_send(None)

    # ══════════════════════════════════════════════════════════════════
    #  JURY MODE (bug-fixed streaming)
    # ══════════════════════════════════════════════════════════════════
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
                    full = chunk  # = not += (same fix)
            future = asyncio.run_coroutine_threadsafe(_do(), _loop)
            future.result(timeout=300)
        except Exception as e:
            full = f"Jury error: {e}"
        self.after(0, self._finalize_response, full)

    # ══════════════════════════════════════════════════════════════════
    #  VOICE (macOS dictation trigger)
    # ══════════════════════════════════════════════════════════════════
    def _toggle_voice(self):
        try:
            subprocess.Popen(
                ["osascript", "-e",
                 'tell application "System Events" to key code 63 using {command down}'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  MODEL MANAGEMENT
    # ══════════════════════════════════════════════════════════════════
    def _get_model_choices(self):
        try:
            import requests
            host = config.get("ollama_host", "http://localhost:11434")
            resp = requests.get(f"{host}/api/tags", timeout=5)
            models = [m["name"] for m in resp.json().get("models", [])]
            return models if models else [
                config.get("primary_model", "qwen3:30b").replace("ollama/", "")
            ]
        except Exception:
            return [config.get("primary_model", "qwen3:30b").replace("ollama/", "")]

    def _on_model_change(self, model_name):
        agent.set_model(model_name)
        self._model_label.configure(text=model_name)

    def _refresh_models(self):
        models = self._get_model_choices()
        self._settings_model_menu.configure(values=models)

    # ══════════════════════════════════════════════════════════════════
    #  NEW SESSION
    # ══════════════════════════════════════════════════════════════════
    def _new_session(self):
        self._chat_history.clear()
        memory.clear_day()
        self._append_message(
            "assistant", "New session started. Ready to assist.",
        )

    # ══════════════════════════════════════════════════════════════════
    #  BACKGROUND THREADS
    # ══════════════════════════════════════════════════════════════════
    def _start_background_threads(self):
        # Doctor health check
        def _doctor_loop():
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
        threading.Thread(target=_doctor_loop, daemon=True).start()

        # Calendar checker
        if scheduler:
            def _cal_loop():
                while True:
                    try:
                        for ev in scheduler.check_due():
                            title = ev.get("title", "Event")
                            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
                            subprocess.run(
                                ["osascript", "-e",
                                 f'display notification "{safe_title}" with title "LLTimmy" sound name "Funk"'],
                                capture_output=True, timeout=5,
                            )
                    except Exception:
                        pass
                    time.sleep(60)
            threading.Thread(target=_cal_loop, daemon=True).start()

        # Idle evolution
        evolution.start_idle_research(agent)

        # Periodic sidebar refresh (every 30 s)
        def _ui_loop():
            while True:
                try:
                    self.after(0, lambda: self._refresh_tab(self._current_tab))
                except Exception:
                    pass
                time.sleep(30)
        threading.Thread(target=_ui_loop, daemon=True).start()

        # Ollama warm-up
        def _warmup():
            try:
                import requests
                host = config.get("ollama_host", "http://localhost:11434")
                requests.post(
                    f"{host}/api/chat",
                    json={
                        "model": agent.current_model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                    timeout=60,
                )
                logger.info("Ollama warm-up complete")
            except Exception as e:
                logger.warning("Warmup failed: %s", e)
        threading.Thread(target=_warmup, daemon=True).start()

    def _start_doctor(self):
        try:
            subprocess.Popen(
                [VENV_PYTHON, str(BASE_DIR / "doctor.py")],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Doctor daemon started")
        except Exception as e:
            logger.warning("Failed to start Doctor: %s", e)

    # ══════════════════════════════════════════════════════════════════
    #  CLEAN EXIT
    # ══════════════════════════════════════════════════════════════════
    def _on_close(self):
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        evolution.stop_idle_research()
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = LLTimmyApp()
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        logging.getLogger(__name__).error("Cannot write PID file: %s", e)
    app.mainloop()
