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
import re
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
from tkinter import filedialog
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Path setup — src/ lives one level inside the project root
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

CONFIG_PATH = BASE_DIR / "config.json"
PID_FILE = Path("/tmp/timmy.pid")
DOCTOR_PID_FILE = Path("/tmp/doctor.pid")
LOCK_FILE = Path("/tmp/timmy.lock")
MEMORY_BASE = BASE_DIR / "memory"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("lltimmy")


# ---------------------------------------------------------------------------
# Single-instance guard — prevents multiple Timmy apps from spawning
# ---------------------------------------------------------------------------
def _acquire_single_instance_lock() -> bool:
    """Acquire an exclusive file lock. Returns True if we're the only instance."""
    import fcntl
    try:
        # Check PID file first — if another Timmy is alive, bail immediately
        if PID_FILE.exists():
            try:
                existing_pid = int(PID_FILE.read_text().strip())
                os.kill(existing_pid, 0)  # Check if process is alive
                # Process is alive — are we that process?
                if existing_pid != os.getpid():
                    logger.error("Another Timmy is already running (PID %d). Exiting.", existing_pid)
                    return False
            except (ProcessLookupError, PermissionError):
                # Stale PID file — process is dead, we can take over
                pass
            except (ValueError, OSError):
                pass

        # Acquire exclusive file lock (non-blocking)
        lock_fd = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            logger.error("Cannot acquire lock file — another Timmy is running. Exiting.")
            lock_fd.close()
            return False

        # Write our PID
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        # Keep fd open for the lifetime of the process (lock released on exit)
        _acquire_single_instance_lock._fd = lock_fd
        return True
    except Exception as e:
        logger.error("Single-instance check failed: %s", e)
        return True  # Fail-open: allow startup if lock mechanism is broken

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
agent._evolution = evolution  # wire shared evolution instance for self-healing
# NOTE: agent._stream_line_callback is wired in LLTimmyApp.__init__ after UI is built

# Resolve venv Python path once for reuse
_venv_path = BASE_DIR / ".venv" / "bin" / "python3"
VENV_PYTHON = str(_venv_path) if _venv_path.exists() else sys.executable


# ═══════════════════════════════════════════════════════════════════════════
# Quiet Luxury Color Palette
# ═══════════════════════════════════════════════════════════════════════════
C_BG         = "#121212"    # Deep matte charcoal — the canvas
C_SURFACE    = "#161616"    # Panel / sidebar surface
C_SURFACE_2  = "#1e1e1e"    # Elevated surface (cards, hovers)
C_SURFACE_3  = "#272727"    # Hover / active states
C_BORDER     = "#1a1a1a"    # Barely-visible border
C_BORDER_VIS = "#2c2c2c"    # Slightly more visible border for inputs
C_ACCENT     = "#f5d06b"    # Soft pastel amber/gold — quiet luxury
C_ACCENT_DIM = "#1e1a0f"    # Dim amber for subtle bg hints
C_ACCENT_HOV = "#e0bc58"    # Accent hover — muted
C_TEXT       = "#ededef"    # Primary text — warm off-white
C_TEXT_SEC   = "#7c7c82"    # Secondary text
C_TEXT_MUTED = "#404044"    # Muted / disabled
C_GREEN      = "#5cbf6e"    # Online / success — softer green
C_RED        = "#e05c54"    # Error / failure — softer red
C_INPUT_BG   = "#191919"    # Input field background


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
        self._finalize_token = 0           # unique token per agent run
        self._chat_history: List[Dict] = []
        self._current_tab = "Tasks"
        self._show_reasoning = True
        self._last_stream_update = 0.0
        self._debug_visible = False
        self._debug_entries: List[Dict] = []  # live debug feed
        self._debug_lock = threading.Lock()
        self._warmup_done = False              # set True by warmup thread
        self._image_cache = {}                 # prevent GC of PhotoImage refs

        # Load today's conversation
        self._load_chat_history()

        # Build everything
        self._build_ui()
        self._start_background_threads()

        # Wire streaming terminal callback to debug panel
        agent._stream_line_callback = lambda line: self._push_debug("result", line)

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
        # Column 0 = sidebar (fixed), column 1 = chat (flex), column 2 = debug (toggleable)
        self._main_frame.grid_columnconfigure(0, weight=0, minsize=260)
        self._main_frame.grid_columnconfigure(1, weight=1)
        self._main_frame.grid_columnconfigure(2, weight=0, minsize=0)
        self._main_frame.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_chat_area()
        self._build_debug_panel()

    # ══════════════════════════════════════════════════════════════════
    #  SIDEBAR — 250 px, generous breathing room
    # ══════════════════════════════════════════════════════════════════
    def _build_sidebar(self):
        sb = ctk.CTkFrame(
            self._main_frame, width=260, fg_color=C_SURFACE,
            corner_radius=0, border_width=1, border_color=C_BORDER,
        )
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(4, weight=1)
        self._sidebar = sb

        # ── Brand header ──────────────────────────────────────────────
        hdr = ctk.CTkFrame(sb, fg_color="transparent", height=52)
        hdr.grid(row=0, column=0, sticky="ew", padx=18, pady=(20, 4))
        hdr.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(hdr, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            brand, text="LLTimmy",
            font=("SF Pro Display", 20, "bold"), text_color=C_TEXT,
        ).pack(side="left")

        # Soft amber status dot
        ctk.CTkLabel(
            brand, text="\u25cf", font=("SF Pro", 7),
            text_color=C_ACCENT, width=10,
        ).pack(side="left", padx=(6, 0), pady=(2, 0))

        # New-session button
        ctk.CTkButton(
            hdr, text="+", width=28, height=28,
            fg_color=C_SURFACE_2, hover_color=C_SURFACE_3,
            text_color=C_TEXT_SEC, font=("SF Pro", 14),
            corner_radius=14, command=self._new_session,
        ).grid(row=0, column=1, sticky="e")

        # ── Agent status cards ────────────────────────────────────────
        cards = ctk.CTkFrame(sb, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=14, pady=(10, 4))

        self._agent_card = self._make_agent_card(
            cards, "Agent Timmy", "Ready", C_ACCENT, active=True)
        self._agent_card.pack(fill="x", pady=(0, 4))

        self._doctor_card = self._make_agent_card(
            cards, "Doctor", "Watching", C_TEXT_MUTED, active=False)
        self._doctor_card.pack(fill="x")

        # ── Tab row: Tasks · Memory · Calendar  +  ⋯ ─────────────────
        tab_row = ctk.CTkFrame(sb, fg_color="transparent", height=32)
        tab_row.grid(row=2, column=0, sticky="ew", padx=14, pady=(14, 0))

        self._tab_buttons = {}
        for name in ("Tasks", "Memory", "Calendar"):
            active = name == "Tasks"
            btn = ctk.CTkButton(
                tab_row, text=name, width=62, height=26,
                fg_color=C_ACCENT if active else "transparent",
                hover_color=C_SURFACE_2,
                text_color=C_BG if active else C_TEXT_SEC,
                font=("SF Pro", 11, "bold" if active else "normal"),
                corner_radius=13,
                command=lambda t=name: self._switch_tab(t),
            )
            btn.pack(side="left", padx=(0, 3))
            self._tab_buttons[name] = btn

        # Three-dot menu
        ctk.CTkButton(
            tab_row, text="\u22ef", width=26, height=26,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_SEC, font=("SF Pro", 14),
            corner_radius=13, command=self._show_extended_menu,
        ).pack(side="right")

        # ── Raw Debug View toggle ──────────────────────────────────────
        debug_row = ctk.CTkFrame(sb, fg_color="transparent", height=28)
        debug_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 0))

        self._debug_var = ctk.BooleanVar(master=self, value=False)
        self._debug_switch = ctk.CTkSwitch(
            debug_row, text="Raw Debug", variable=self._debug_var,
            font=("SF Pro", 10), text_color=C_TEXT_SEC,
            fg_color=C_SURFACE_2, progress_color=C_ACCENT,
            button_color=C_TEXT_SEC, button_hover_color=C_TEXT,
            width=34, height=16,
            command=self._toggle_debug_panel,
        )
        self._debug_switch.pack(side="left")

        # ── Tab content area ──────────────────────────────────────────
        self._tab_content = ctk.CTkFrame(sb, fg_color="transparent")
        self._tab_content.grid(row=4, column=0, sticky="nsew", padx=14, pady=(12, 8))

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
        btm = ctk.CTkFrame(sb, fg_color="transparent", height=56)
        btm.grid(row=5, column=0, sticky="sew", padx=14, pady=(0, 14))

        self._quick_add = ctk.CTkEntry(
            btm, placeholder_text="Quick add task\u2026",
            fg_color=C_INPUT_BG, border_color=C_BORDER_VIS,
            text_color=C_TEXT, font=("SF Pro", 11), height=32,
            corner_radius=16,
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
            corner_radius=16, height=44, **kw,
        )
        card.pack_propagate(False)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=6)

        ctk.CTkLabel(
            inner, text="\u25cf", font=("SF Pro", 7),
            text_color=dot_color, width=10,
        ).pack(side="left", padx=(0, 7))

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            info, text=name, font=("SF Pro", 12, "bold"),
            text_color=C_TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            info, text=status_text, font=("SF Pro", 9),
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
                    font=("SF Pro", 11, "bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent", text_color=C_TEXT_SEC,
                    font=("SF Pro", 11),
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
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                sz = f.tell()
                f.seek(max(0, sz - 32768))
                tail = f.read().decode("utf-8", errors="ignore")
            lines = tail.strip().split("\n")[-20:]
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

        # Pending improvements (staging area)
        pending = evolution.get_pending_improvements()
        if pending:
            ctk.CTkLabel(
                self._evo_container, text="Staging Area",
                font=("SF Pro", 11, "bold"), text_color=C_ACCENT,
            ).pack(anchor="w", padx=4, pady=(4, 6))

            for imp in pending[:5]:
                card = ctk.CTkFrame(
                    self._evo_container, fg_color=C_SURFACE_2,
                    corner_radius=10,
                )
                card.pack(fill="x", pady=(0, 4))
                ctk.CTkLabel(
                    card, text=f"#{imp['id']}: {imp['title'][:35]}",
                    font=("SF Pro", 10, "bold"), text_color=C_TEXT,
                    wraplength=200, justify="left",
                ).pack(anchor="w", padx=8, pady=(6, 0))
                ctk.CTkLabel(
                    card, text=imp["description"][:80],
                    font=("SF Mono", 9), text_color=C_TEXT_SEC,
                    wraplength=200, justify="left",
                ).pack(anchor="w", padx=8, pady=(2, 4))

                btns = ctk.CTkFrame(card, fg_color="transparent")
                btns.pack(fill="x", padx=8, pady=(0, 6))
                ctk.CTkButton(
                    btns, text="Approve", width=60, height=20,
                    fg_color=C_GREEN, text_color=C_BG,
                    font=("SF Pro", 9), corner_radius=10,
                    command=lambda iid=imp["id"]: self._approve_evolution(iid),
                ).pack(side="left", padx=(0, 4))
                ctk.CTkButton(
                    btns, text="Reject", width=60, height=20,
                    fg_color=C_SURFACE_3, text_color=C_TEXT_SEC,
                    font=("SF Pro", 9), corner_radius=10,
                    command=lambda iid=imp["id"]: self._reject_evolution(iid),
                ).pack(side="left")

        # New ideas from idle research
        ideas = evolution.get_new_ideas()
        if ideas:
            ctk.CTkLabel(
                self._evo_container, text="Research Ideas",
                font=("SF Pro", 11, "bold"), text_color=C_TEXT_SEC,
            ).pack(anchor="w", padx=4, pady=(10, 4))
            for idea in ideas[:5]:
                ctk.CTkLabel(
                    self._evo_container, text=f"  {idea['title'][:40]}",
                    font=("SF Mono", 9), text_color=C_TEXT_SEC,
                    wraplength=220, justify="left",
                ).pack(anchor="w", padx=4)

        # Overall summary
        summary = evolution.get_evolution_summary()
        ctk.CTkLabel(
            self._evo_container, text=summary,
            font=("SF Mono", 9), text_color=C_TEXT_MUTED,
            wraplength=220, justify="left",
        ).pack(fill="x", padx=4, pady=(10, 4))

    def _render_console(self):
        log_path = BASE_DIR / "tim_audit.log"
        self._console_text.configure(state="normal")
        self._console_text.delete("1.0", "end")
        if log_path.exists():
            try:
                with open(log_path, "rb") as f:
                    f.seek(0, 2)
                    sz = f.tell()
                    f.seek(max(0, sz - 32768))
                    tail = f.read().decode("utf-8", errors="ignore")
                lines = tail.strip().split("\n")[-30:]
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
            chat, fg_color=C_SURFACE, height=48,
            corner_radius=0, border_width=0,
        )
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(hdr, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=24, pady=10)

        ctk.CTkLabel(
            left, text="Secure Session",
            font=("SF Pro Display", 13, "bold"), text_color=C_TEXT,
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
            font=("SF Pro", 12), corner_radius=0, border_width=0,
            wrap="word", state="disabled", spacing3=5,
        )
        self._chat_display.grid(row=1, column=0, sticky="nsew", padx=24, pady=(12, 0))

        # Text tags for message styling
        tb = self._chat_display._textbox
        tb.tag_configure("user_name",
                         foreground=C_TEXT, font=("SF Pro Display", 11, "bold"),
                         spacing1=10)
        tb.tag_configure("bot_name",
                         foreground=C_ACCENT, font=("SF Pro Display", 11, "bold"),
                         spacing1=10)
        tb.tag_configure("timestamp",
                         foreground=C_TEXT_MUTED, font=("SF Mono", 9))
        tb.tag_configure("user_msg",
                         foreground=C_TEXT, font=("SF Pro", 12),
                         lmargin1=4, lmargin2=4, spacing1=3, spacing3=5)
        tb.tag_configure("bot_msg",
                         foreground="#e0e0e2", font=("SF Pro", 12),
                         lmargin1=4, lmargin2=4, spacing1=3, spacing3=5)
        tb.tag_configure("error_msg",
                         foreground=C_RED, font=("SF Pro", 12))
        tb.tag_configure("dim_msg",
                         foreground=C_TEXT_MUTED, font=("SF Mono", 10),
                         lmargin1=4, lmargin2=4, spacing1=2, spacing3=2)
        tb.tag_configure("sep", font=("SF Pro", 2))
        # Rich rendering tags (code blocks, bold, headings)
        tb.tag_configure("code_block",
                         background="#1e1e1e", foreground="#c9d1d9",
                         font=("SF Mono", 11),
                         lmargin1=8, lmargin2=8, rmargin=8,
                         spacing1=2, spacing3=2)
        tb.tag_configure("code_lang",
                         foreground=C_ACCENT, font=("SF Mono", 9, "bold"),
                         lmargin1=8)
        tb.tag_configure("inline_code",
                         background="#1e1e1e", foreground="#e0e0e2",
                         font=("SF Mono", 11))
        tb.tag_configure("bold_text",
                         font=("SF Pro Display", 12, "bold"),
                         foreground=C_TEXT)
        tb.tag_configure("heading",
                         font=("SF Pro Display", 14, "bold"),
                         foreground=C_TEXT, spacing1=8)

        # ── Suggestion chips ──────────────────────────────────────────
        sug = ctk.CTkFrame(chat, fg_color="transparent", height=34)
        sug.grid(row=2, column=0, sticky="ew", padx=28, pady=(8, 4))

        for text in ("Summarize tasks", "Check my schedule", "Analyze performance"):
            ctk.CTkButton(
                sug, text=text, height=26,
                fg_color=C_SURFACE_2, hover_color=C_SURFACE_3,
                text_color=C_TEXT_SEC, font=("SF Pro", 10),
                corner_radius=13, border_width=1, border_color=C_BORDER,
                command=lambda t=text: self._send_suggestion(t),
            ).pack(side="left", padx=(0, 6))

        # ── Input bar ─────────────────────────────────────────────────
        inp = ctk.CTkFrame(
            chat, fg_color=C_SURFACE,
            corner_radius=24, border_width=1, border_color=C_BORDER,
            height=52,
        )
        inp.grid(row=3, column=0, sticky="ew", padx=28, pady=(8, 8))
        inp.grid_columnconfigure(1, weight=1)

        # Left tool icons
        tools_f = ctk.CTkFrame(inp, fg_color="transparent")
        tools_f.grid(row=0, column=0, padx=(14, 4), pady=8)

        # Attach file button (real file picker)
        self._attach_btn = ctk.CTkButton(
            tools_f, text="\U0001f4ce", width=26, height=26,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_MUTED, font=("SF Pro", 13),
            corner_radius=13, command=self._attach_file,
        )
        self._attach_btn.pack(side="left", padx=1)

        # Microphone button (real speech recognition)
        self._mic_btn = ctk.CTkButton(
            tools_f, text="\U0001f3a4", width=26, height=26,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_MUTED, font=("SF Pro", 13),
            corner_radius=13, command=self._toggle_voice,
        )
        self._mic_btn.pack(side="left", padx=1)
        self._mic_recording = False

        # Trace / Reasoning button
        self._trace_btn = ctk.CTkButton(
            tools_f, text="\u2699", width=26, height=26,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_MUTED, font=("SF Pro", 13),
            corner_radius=13, command=self._toggle_trace_panel,
        )
        self._trace_btn.pack(side="left", padx=1)

        # Clipboard paste button
        self._clip_btn = ctk.CTkButton(
            tools_f, text="\U0001f4cb", width=26, height=26,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_TEXT_MUTED, font=("SF Pro", 13),
            corner_radius=13, command=self._paste_clipboard_context,
        )
        self._clip_btn.pack(side="left", padx=1)

        # Text entry
        self._msg_input = ctk.CTkEntry(
            inp,
            placeholder_text="Message Timmy\u2026",
            fg_color="transparent", border_width=0,
            text_color=C_TEXT, font=("SF Pro", 13), height=34,
        )
        self._msg_input.grid(row=0, column=1, sticky="ew", pady=8)
        self._msg_input.bind("<Return>", self._on_send)

        # Pending file attachment display
        self._attached_file = None

        # Right: jury + send
        right_tools = ctk.CTkFrame(inp, fg_color="transparent")
        right_tools.grid(row=0, column=2, padx=(4, 10), pady=8)

        ctk.CTkButton(
            right_tools, text="\u2696", width=28, height=28,
            fg_color="transparent", hover_color=C_SURFACE_2,
            text_color=C_ACCENT, font=("SF Pro", 14),
            corner_radius=14, command=self._jury_send,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            right_tools, text="\u27a4", width=34, height=34,
            fg_color=C_ACCENT, hover_color=C_ACCENT_HOV,
            text_color=C_BG, font=("SF Pro", 14, "bold"),
            corner_radius=17, command=lambda: self._on_send(None),
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
    #  RAW DEBUG VIEW — right panel
    # ══════════════════════════════════════════════════════════════════
    def _build_debug_panel(self):
        """Right-side debug panel showing live thoughts, tool calls, etc."""
        self._debug_panel = ctk.CTkFrame(
            self._main_frame, width=320, fg_color=C_SURFACE,
            corner_radius=0, border_width=1, border_color=C_BORDER,
        )
        # Not gridded initially — shown/hidden by toggle
        self._debug_panel.grid_propagate(False)

        # Header
        dhdr = ctk.CTkFrame(self._debug_panel, fg_color="transparent", height=40)
        dhdr.pack(fill="x", padx=14, pady=(14, 4))

        ctk.CTkLabel(
            dhdr, text="Raw Debug View",
            font=("SF Pro Display", 13, "bold"), text_color=C_ACCENT,
        ).pack(side="left")

        ctk.CTkButton(
            dhdr, text="Clear", width=44, height=22,
            fg_color=C_SURFACE_2, hover_color=C_SURFACE_3,
            text_color=C_TEXT_SEC, font=("SF Mono", 9),
            corner_radius=11, command=self._clear_debug,
        ).pack(side="right")

        # Debug content — scrollable textbox
        self._debug_text = ctk.CTkTextbox(
            self._debug_panel, fg_color=C_BG,
            text_color=C_GREEN, font=("SF Mono", 10),
            corner_radius=8, border_width=1, border_color=C_BORDER,
            wrap="word", state="disabled",
        )
        self._debug_text.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        # Text tags for debug entries
        dtb = self._debug_text._textbox
        dtb.tag_configure("dbg_tool",
                          foreground=C_ACCENT, font=("SF Mono", 10, "bold"))
        dtb.tag_configure("dbg_thought",
                          foreground="#8888cc", font=("SF Mono", 10, "italic"))
        dtb.tag_configure("dbg_result",
                          foreground=C_TEXT_SEC, font=("SF Mono", 9))
        dtb.tag_configure("dbg_time",
                          foreground=C_TEXT_MUTED, font=("SF Mono", 8))
        dtb.tag_configure("dbg_mem",
                          foreground="#6bccaa", font=("SF Mono", 9))

    def _toggle_debug_panel(self):
        """Show/hide the right debug panel."""
        self._debug_visible = self._debug_var.get()
        if self._debug_visible:
            self._debug_panel.grid(row=0, column=2, sticky="nsew")
            self._refresh_debug()
        else:
            self._debug_panel.grid_forget()

    def _clear_debug(self):
        with self._debug_lock:
            self._debug_entries.clear()
        self._debug_text.configure(state="normal")
        self._debug_text.delete("1.0", "end")
        self._debug_text.configure(state="disabled")

    def _push_debug(self, category: str, content: str):
        """Add a debug entry (called from streaming/agent callbacks)."""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {"ts": ts, "cat": category, "content": content}
        with self._debug_lock:
            self._debug_entries.append(entry)
            if len(self._debug_entries) > 200:
                self._debug_entries = self._debug_entries[-200:]
        if self._debug_visible:
            self.after(0, self._append_debug_entry, entry)

    def _append_debug_entry(self, entry):
        self._debug_text.configure(state="normal")
        ts = entry["ts"]
        cat = entry["cat"]
        content = entry["content"]

        tag_map = {
            "tool": "dbg_tool", "thought": "dbg_thought",
            "result": "dbg_result", "memory": "dbg_mem",
        }
        tag = tag_map.get(cat, "dbg_result")

        self._debug_text.insert("end", f"[{ts}] ", "dbg_time")
        self._debug_text.insert("end", f"{cat.upper()}: ", tag)
        self._debug_text.insert("end", content[:300] + "\n", tag)
        self._debug_text.configure(state="disabled")
        self._debug_text.see("end")

    def _refresh_debug(self):
        """Refresh debug panel with recent audit log entries."""
        self._debug_text.configure(state="normal")
        self._debug_text.delete("1.0", "end")

        # Load recent audit log entries
        log_path = BASE_DIR / "tim_audit.log"
        if log_path.exists():
            try:
                with open(log_path, "rb") as f:
                    f.seek(0, 2)
                    sz = f.tell()
                    f.seek(max(0, sz - 16384))
                    tail = f.read().decode("utf-8", errors="ignore")
                lines = tail.strip().split("\n")[-30:]
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("ts", "")[-8:]  # HH:MM:SS
                        tool = entry.get("tool", "?")
                        result = entry.get("result", "")[:200]
                        self._debug_text.insert("end", f"[{ts}] ", "dbg_time")
                        self._debug_text.insert("end", f"TOOL: {tool}\n", "dbg_tool")
                        self._debug_text.insert("end", f"  {result}\n", "dbg_result")
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass

        # Also show subconscious search results if any
        try:
            stats = memory.get_memory_stats()
            self._debug_text.insert("end", f"\nMEMORY: {stats['subconscious_entries']} entries, "
                                    f"{stats['graph_entities']} graph nodes\n", "dbg_mem")
        except Exception:
            pass

        self._debug_text.configure(state="disabled")
        self._debug_text.see("end")

    # ══════════════════════════════════════════════════════════════════
    #  CHAT SANITIZER — clean raw markdown/HTML for human-like display
    # ══════════════════════════════════════════════════════════════════
    @staticmethod
    def _sanitize_chat(text: str) -> str:
        """Strip markdown, HTML, and ReAct artifacts for clean display."""
        # Remove entire <details>...</details> blocks (loop for nested)
        prev = None
        while prev != text:
            prev = text
            text = re.sub(r'<details[^>]*>.*?</details>', '', text, flags=re.DOTALL)
        # Remove any orphaned tags (from partial streaming)
        text = re.sub(r'<details[^>]*>', '', text)
        text = re.sub(r'</details>', '', text)
        text = re.sub(r'<summary[^>]*>.*?</summary>', '', text, flags=re.DOTALL)
        # Remove code fences (``` with optional language)
        text = re.sub(r'```\w*\n?', '', text)
        # Remove bold markers **text** → text
        text = re.sub(r'\*\*([^*]*)\*\*', r'\1', text)
        # Remove italic markers *text* → text (single asterisk)
        text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', text)
        # Remove horizontal rules (--- or ===)
        text = re.sub(r'^-{3,}\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^={3,}\s*$', '', text, flags=re.MULTILINE)
        # Remove markdown headings (### etc) — keep the text
        text = re.sub(r'^#{1,4}\s+', '', text, flags=re.MULTILINE)
        # Remove backtick inline code markers
        text = re.sub(r'`([^`]*)`', r'\1', text)
        # Remove leading bullet decorators that are purely ornamental
        # (but keep real list items with content)
        text = re.sub(r'^\s*[\*\-]\s*$', '', text, flags=re.MULTILINE)
        # Clean up (completed, no output) noise
        text = re.sub(r'\(completed,\s*no output\)', '', text)
        # Clean up excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ══════════════════════════════════════════════════════════════════
    #  RICH MESSAGE RENDERER — code blocks, bold, headings, images
    # ══════════════════════════════════════════════════════════════════
    _CODE_FENCE_RE = re.compile(r'```(\w*)\n?(.*?)```', re.DOTALL)

    def _render_rich_message(self, tb, content: str, base_tag: str):
        """Render message with code block highlighting into tk.Text widget."""
        segments = []
        pos = 0
        for m in self._CODE_FENCE_RE.finditer(content):
            pre = content[pos:m.start()]
            if pre:
                segments.append(("text", pre))
            lang = m.group(1) or ""
            code = m.group(2).rstrip()
            segments.append((f"code:{lang}", code))
            pos = m.end()
        if pos < len(content):
            segments.append(("text", content[pos:]))

        for seg_type, seg_text in segments:
            if seg_type.startswith("code:"):
                lang = seg_type[5:]
                if lang:
                    tb.insert("end", f" {lang} \n", "code_lang")
                tb.insert("end", seg_text + "\n\n", "code_block")
            else:
                clean = self._sanitize_chat(seg_text)
                for line in clean.split("\n"):
                    if not line.strip():
                        tb.insert("end", "\n", base_tag)
                        continue
                    # Render bold + inline code within line
                    parts = re.split(r'(\*\*[^*]+\*\*|`[^`\n]+`)', line)
                    for part in parts:
                        if part.startswith("**") and part.endswith("**"):
                            tb.insert("end", part[2:-2], "bold_text")
                        elif part.startswith("`") and part.endswith("`"):
                            tb.insert("end", part[1:-1], "inline_code")
                        elif part:
                            tb.insert("end", part, base_tag)
                    tb.insert("end", "\n", base_tag)

    def _embed_image_in_chat(self, image_path: str):
        """Embed a thumbnail image into the chat display.
        Caller must ensure widget is in 'normal' state (e.g. inside _render_chat)."""
        try:
            from PIL import Image as PILImage, ImageTk
            img = PILImage.open(image_path)
            img.thumbnail((400, 300), PILImage.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._image_cache[image_path] = photo
            # Don't toggle widget state — caller owns it
            self._chat_display._textbox.image_create("end", image=photo, padx=4, pady=4)
            self._chat_display._textbox.insert("end", "\n")
        except ImportError:
            logger.warning("Pillow not installed — cannot embed images")
        except Exception as e:
            logger.warning("Image embed failed: %s", e)

    def _paste_clipboard_context(self):
        """Read clipboard and inject into message input."""
        try:
            proc = subprocess.run(["pbpaste"], capture_output=True, timeout=3)
            content = proc.stdout.decode("utf-8", errors="replace").strip()
            content = content.replace("\n", " ").replace("\r", " ")  # CTkEntry is single-line
            if not content:
                return
            current = self._msg_input.get()
            if current:
                self._msg_input.delete(0, "end")
                self._msg_input.insert(0, f"{current} [Clipboard]: {content[:500]}")
            else:
                self._msg_input.insert(0, content[:500])
        except Exception as e:
            logger.warning("Clipboard paste error: %s", e)

    # ══════════════════════════════════════════════════════════════════
    #  CHAT RENDERING
    # ══════════════════════════════════════════════════════════════════
    def _render_chat(self):
        self._chat_display.configure(state="normal")
        self._chat_display.delete("1.0", "end")

        tb = self._chat_display._textbox

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

            if role == "assistant":
                # Rich render: code blocks, bold, inline code, image embedding
                self._render_rich_message(tb, content, "bot_msg")
                # Embed images found in response
                img_paths = re.findall(
                    r'(/[^\s]+\.(?:png|jpg|jpeg|webp|gif))', content)
                for img_path in img_paths:
                    if os.path.exists(img_path):
                        self._embed_image_in_chat(img_path)
            else:
                self._chat_display.insert("end", content + "\n", "user_msg")

            self._chat_display.insert("end", "\n", "sep")

        self._chat_display.configure(state="disabled")
        self._chat_display.see("end")

    def _filter_reasoning(self, text: str) -> str:
        """Strip ReAct Thought/Action/Observation blocks and HTML wrappers."""
        # First strip <details>...</details> blocks entirely (loop for nested)
        prev = None
        while prev != text:
            prev = text
            text = re.sub(r'<details[^>]*>.*?</details>', '', text, flags=re.DOTALL)
        lines = text.split("\n")
        out = []
        skip = False
        for line in lines:
            stripped = line.strip()
            # Enter skip mode for any ReAct keyword (including Action Input)
            if stripped.startswith(("Thought:", "Action:", "Action Input:", "Observation:")):
                skip = True
                continue
            # Exit skip only for Final Answer or [Agent ...] markers
            if skip:
                if stripped.startswith(("Final Answer", "**[", "[Agent")):
                    skip = False
                else:
                    continue
            out.append(line)
        result = "\n".join(out).strip()
        return result if result else text

    def _toggle_reasoning(self):
        self._show_reasoning = self._reasoning_var.get()
        if self._show_reasoning:
            self._trace_btn.configure(text_color=C_ACCENT)
            self._show_tab("Trace")
        else:
            self._trace_btn.configure(text_color=C_TEXT_MUTED)
            self._show_tab("Tasks")
        self._render_chat()

    def _approve_evolution(self, improvement_id):
        """Approve a staged improvement and send to Doctor for application."""
        imp = evolution.approve_improvement(improvement_id)
        if imp:
            # Notify Doctor to apply the change
            try:
                doctor_signal = BASE_DIR / "memory" / "doctor_apply.json"
                doctor_signal.write_text(json.dumps(imp, indent=2))
                self._push_debug("tool", f"Approved improvement #{improvement_id} -> Doctor")
            except Exception as e:
                logger.warning("Failed to signal Doctor: %s", e)
        self._render_evolution()

    def _reject_evolution(self, improvement_id):
        """Reject a staged improvement."""
        evolution.reject_improvement(improvement_id)
        self._render_evolution()

    # ══════════════════════════════════════════════════════════════════
    #  SENDING MESSAGES + STREAMING (bug-fixed)
    # ══════════════════════════════════════════════════════════════════
    def _on_send(self, event):
        text = self._msg_input.get().strip()
        if not text or self._agent_working:
            return
        self._msg_input.delete(0, "end")
        # Include attached file info if present
        file_paths = None
        if self._attached_file:
            file_paths = [self._attached_file]
            fname = Path(self._attached_file).name
            text = f"{text}\n\n[Attached: {fname}]"
            self._attached_file = None
            self._attach_btn.configure(text_color=C_TEXT_MUTED)
        self._append_message("user", text)
        self._set_working(True)
        self._finalize_token += 1
        token = self._finalize_token
        threading.Thread(target=self._run_agent, args=(text, file_paths, token), daemon=True).start()

    def _run_agent(self, user_message: str, file_paths=None, token: int = 0):
        """Run agent ReAct loop with streaming. BUG-FIX: agent.run() yields
        the full accumulated response so far, not deltas — use = not +=."""
        full_response = ""
        self._push_debug("thought", f"Processing: {user_message[:80]}")

        # Wait for warmup to finish if it's still running (max 30s)
        if not self._warmup_done:
            self._push_debug("thought", "Waiting for model to load...")
            for _ in range(60):
                if self._warmup_done:
                    break
                time.sleep(0.5)

        try:
            async def _do():
                nonlocal full_response
                async for chunk in agent.run(user_message, file_paths=file_paths):
                    # CRITICAL: agent.run() yields accumulated text, not deltas
                    prev_len = len(full_response)
                    full_response = chunk
                    now = time.time()
                    if now - self._last_stream_update > 0.1:
                        self._last_stream_update = now
                        snapshot = full_response
                        self.after(0, self._update_streaming, snapshot)
                        # Push new content to debug
                        new_text = chunk[prev_len:]
                        if new_text.strip():
                            for line in new_text.split("\n"):
                                s = line.strip()
                                if s.startswith("Thought:"):
                                    self._push_debug("thought", s[8:].strip())
                                elif s.startswith("Action:"):
                                    self._push_debug("tool", s[7:].strip())
                                elif s.startswith("Observation:"):
                                    self._push_debug("result", s[12:].strip())

            future = asyncio.run_coroutine_threadsafe(_do(), _loop)
            future.result(timeout=300)
        except Exception as e:
            full_response = f"Error: {e}"
            logger.error("Agent error: %s", e)
            self._push_debug("result", f"ERROR: {e}")

        self._push_debug("result", "Response complete")
        self.after(0, self._finalize_response, full_response, token)

    def _update_streaming(self, partial):
        """Incremental streaming update — ONLY updates the text widget.
        Does NOT modify _chat_history (that's done in _finalize_response)
        to avoid race conditions between the bg thread and Tk main loop."""
        # Optionally filter reasoning
        display_text = partial
        if not self._show_reasoning:
            display_text = self._filter_reasoning(partial)
        # Always sanitize (strip HTML/markdown artifacts)
        display_text = self._sanitize_chat(display_text)

        self._chat_display.configure(state="normal")
        try:
            # Search by tag instead of text content (avoids matching "Timmy" in messages)
            bot_ranges = self._chat_display._textbox.tag_ranges("bot_name")
            last_bot = str(bot_ranges[-2]) if len(bot_ranges) >= 2 else None
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

    def _finalize_response(self, full_text, token: int = 0):
        # Guard: only the matching token can finalize (prevents jury/agent overlap)
        if not self._agent_working or (token and token != self._finalize_token):
            return
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
            # Update model badge to show current model
            self._model_label.configure(text=agent.current_model)
        else:
            self._status_dot.configure(text_color=C_GREEN)
            self._status_label.configure(text="Online")
            self._model_label.configure(text=agent.current_model)

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
        self._finalize_token += 1
        token = self._finalize_token
        threading.Thread(target=self._run_jury, args=(text, token), daemon=True).start()

    def _run_jury(self, query, token: int = 0):
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
        self.after(0, self._finalize_response, full, token)

    # ══════════════════════════════════════════════════════════════════
    #  ATTACH FILE (real file picker dialog)
    # ══════════════════════════════════════════════════════════════════
    def _attach_file(self):
        path = filedialog.askopenfilename(
            title="Attach File",
            filetypes=[
                ("All Files", "*.*"),
                ("Images", "*.png *.jpg *.jpeg *.gif *.webp"),
                ("Text", "*.txt *.md *.py *.json *.csv"),
                ("Documents", "*.pdf *.docx *.xlsx"),
            ],
        )
        if path:
            self._attached_file = path
            fname = Path(path).name
            self._attach_btn.configure(text_color=C_ACCENT)
            # APPEND to existing text — never replace what user typed
            current = self._msg_input.get()
            if current and not current.endswith(" "):
                self._msg_input.insert("end", " ")
            self._msg_input.insert("end", f"[Attached: {fname}]")
            logger.info("File attached: %s", path)

    # ══════════════════════════════════════════════════════════════════
    #  VOICE (macOS speech recognition via osascript)
    # ══════════════════════════════════════════════════════════════════
    def _toggle_voice(self):
        if self._mic_recording:
            self._mic_recording = False
            self._mic_btn.configure(text_color=C_TEXT_MUTED)
            return
        self._mic_recording = True
        self._mic_btn.configure(text_color=C_RED)
        threading.Thread(target=self._do_voice_capture, daemon=True).start()

    def _do_voice_capture(self):
        """Use speech_recognition library if available, else macOS dictation."""
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                logger.info("Listening for speech...")
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=30)
            text = recognizer.recognize_google(audio)
            self.after(0, lambda: self._insert_voice_text(text))
        except ImportError:
            # Inform user to install speech_recognition
            self.after(0, lambda: self._insert_voice_text(
                "[Voice: Install SpeechRecognition — pip install SpeechRecognition PyAudio]"
            ))
        except Exception as e:
            logger.warning("Voice capture error: %s", e)
            self.after(0, lambda: self._insert_voice_text(f"[Voice error: {e}]"))
        finally:
            self._mic_recording = False
            self.after(0, lambda: self._mic_btn.configure(text_color=C_TEXT_MUTED))

    def _insert_voice_text(self, text):
        """Append voice transcription to existing input text."""
        current = self._msg_input.get()
        if current and not current.endswith(" "):
            self._msg_input.insert("end", " ")
        self._msg_input.insert("end", text)

    # ══════════════════════════════════════════════════════════════════
    #  TRACE PANEL (reasoning trace toggle — opens sidebar Trace tab)
    # ══════════════════════════════════════════════════════════════════
    def _toggle_trace_panel(self):
        """Toggle the Trace sidebar tab and reasoning visibility in chat."""
        self._show_reasoning = not self._show_reasoning
        self._reasoning_var.set(self._show_reasoning)
        if self._show_reasoning:
            self._trace_btn.configure(text_color=C_ACCENT)
            self._show_tab("Trace")
        else:
            self._trace_btn.configure(text_color=C_TEXT_MUTED)
            self._show_tab("Tasks")
        self._render_chat()

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

        # Periodic sidebar + debug refresh (every 30 s)
        def _ui_loop():
            while True:
                try:
                    self.after(0, lambda: self._refresh_tab(self._current_tab))
                    if self._debug_visible:
                        self.after(0, self._refresh_debug)
                except Exception:
                    pass
                time.sleep(30)
        threading.Thread(target=_ui_loop, daemon=True).start()

        # Ollama warm-up (delayed, minimal, skippable)
        self._warmup_done = False
        def _warmup():
            time.sleep(8)  # Wait for UI to initialize and user to potentially type
            if self._agent_working:
                logger.info("User already active — skipping warmup")
                self._warmup_done = True
                return
            try:
                agent._ollama_request_sync(
                    {"model": agent.current_model,
                     "messages": [{"role": "user", "content": "hi"}],
                     "stream": False,
                     "options": {"num_predict": 1}},  # Minimal: just load model
                    timeout=60,
                )
                logger.info("Ollama warm-up complete")
            except Exception as e:
                logger.warning("Warmup failed: %s", e)
            self._warmup_done = True
        threading.Thread(target=_warmup, daemon=True).start()

    def _start_doctor(self):
        """Start Doctor daemon only if not already running."""
        try:
            # Double-check Doctor isn't already alive (psutil cmdline check)
            import psutil
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = proc.info.get("cmdline") or []
                    if any("doctor.py" in arg for arg in cmdline):
                        logger.info("Doctor already running (PID %d), skipping start", proc.pid)
                        return
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

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
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        # Explicitly close lock fd to release fcntl lock synchronously
        fd = getattr(_acquire_single_instance_lock, '_fd', None)
        if fd:
            try:
                fd.close()
            except Exception:
                pass
        evolution.stop_idle_research()
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Single-instance guard: prevent multiple Timmy apps from spawning
    if not _acquire_single_instance_lock():
        sys.exit(0)

    _loop_thread.start()
    app = LLTimmyApp()
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        logging.getLogger(__name__).error("Cannot write PID file: %s", e)
    app.mainloop()
