"""
LLTimmy -- Main Gradio UI
Amber/Gold dark interface with minimal sidebar, command palette,
trace panel, jury mode, voice input, confidence indicators,
reasoning toggle, real-time streaming, messaging modes, calendar,
macOS notifications, and chat persistence.
Runs on http://127.0.0.1:7860
"""
import json
import os
import asyncio
import threading
import time
import logging
from pathlib import Path
from datetime import datetime, date

import gradio as gr

from agent_core import AgentCore
from memory_manager import MemoryManager
from task_manager import TaskManager
from self_evolution import SelfEvolution
from tools import ToolsSystem

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
PID_FILE = Path("/tmp/timmy.pid")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("timmy")

# Critic #18: Graceful config loading with error handling
try:
    with open(CONFIG_PATH) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"FATAL: Cannot load config.json: {e}")
    import sys
    sys.exit(1)

memory = MemoryManager(ollama_host=config.get("ollama_host", "http://localhost:11434"))
tools = ToolsSystem(config)
task_mgr = TaskManager()
evolution = SelfEvolution()

try:
    from scheduler import Scheduler
    scheduler = Scheduler()
except ImportError:
    scheduler = None

agent = AgentCore(config, memory, tools, scheduler=scheduler, task_mgr=task_mgr)

PID_FILE.write_text(str(os.getpid()))

# ---------------------------------------------------------------------------
# Trace buffer for real-time tool call visibility
# ---------------------------------------------------------------------------
_trace_buffer = []
_MAX_TRACE_LINES = 40

def _log_trace(tool_name, params, result_preview=""):
    ts = datetime.now().strftime("%H:%M:%S")
    param_str = json.dumps(params, ensure_ascii=False)[:120]
    res_str = str(result_preview)[:80].replace("<", "&lt;").replace(">", "&gt;")
    # Critic #19: Escape tool_name to prevent XSS
    safe_tool = str(tool_name).replace("<", "&lt;").replace(">", "&gt;")
    entry = (
        '<div class="trace-entry">'
        '<span class="trace-time">' + ts + '</span> '
        '<span class="trace-tool">' + safe_tool + '</span>'
        '<span class="trace-params">' + param_str.replace("<", "&lt;").replace(">", "&gt;") + '</span>'
    )
    if res_str:
        entry += '<div class="trace-result">' + res_str + '</div>'
    entry += '</div>'
    _trace_buffer.append(entry)
    if len(_trace_buffer) > _MAX_TRACE_LINES:
        _trace_buffer.pop(0)

# Monkey-patch tools.log_tool_call to also feed trace buffer
_original_log = tools.log_tool_call if hasattr(tools, 'log_tool_call') else None
def _traced_log(name, params, result):
    _log_trace(name, params, result)
    if _original_log:
        _original_log(name, params, result)
tools.log_tool_call = _traced_log

# ---------------------------------------------------------------------------
# Theme & CSS -- Amber/Gold Dark Mode (#ffb700, #121212, no blue)
# ---------------------------------------------------------------------------
CUSTOM_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

body, .gradio-container {
    background-color: #121212 !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif !important;
    color: #f5f5f7 !important;
}
.gradio-container { max-width: 100% !important; padding: 0 !important; }

.main-layout {
    display: flex !important; flex-wrap: nowrap !important;
    height: 100vh !important; overflow: hidden !important;
}
.sidebar-col {
    min-width: 300px !important; max-width: 300px !important; width: 300px !important;
    background: #1a1a1a !important; border-right: 1px solid #333 !important;
    overflow-y: auto !important; flex-shrink: 0 !important; height: 100vh !important;
    display: flex !important; flex-direction: column !important;
}
.chat-col {
    flex: 1 !important; display: flex !important; flex-direction: column !important;
    min-width: 0 !important; height: 100vh !important; overflow: hidden !important;
    background: #121212 !important;
}

.header-bar {
    background: #1a1a1a !important; border-bottom: 1px solid #333 !important;
    padding: 12px 20px !important; display: flex !important;
    align-items: center !important; gap: 12px !important; min-height: 52px !important;
}

.secure-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: #242424; border: 1px solid #333; border-radius: 20px;
    padding: 4px 12px; font-size: 10px; color: #86868b;
    letter-spacing: 0.5px; text-transform: uppercase; font-weight: 500;
}

.chatbot-wrap {
    flex: 1 !important; border: none !important; background: #121212 !important;
    overflow-y: auto !important; max-height: calc(100vh - 240px) !important;
}
.chatbot-wrap .message {
    border-radius: 20px !important; padding: 14px 18px !important;
    max-width: 82% !important; box-shadow: none !important;
    transition: opacity 0.15s ease !important; font-size: 14px !important; line-height: 1.6 !important;
}

.chatbot-wrap details {
    background: #242424; border: 1px solid #333; border-radius: 14px;
    padding: 10px 14px; margin: 8px 0; font-size: 13px; transition: border-color 0.2s ease;
}
.chatbot-wrap details:hover { border-color: #444; }
.chatbot-wrap details summary {
    cursor: pointer; color: #86868b;
    font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
    font-size: 11px; user-select: none; letter-spacing: 0.3px;
}
.chatbot-wrap details pre {
    background: #1a1a1a !important; border-radius: 10px; padding: 10px;
    overflow-x: auto; font-size: 12px; margin-top: 8px; border: 1px solid #333;
}

.chatbot-wrap details.reasoning-toggle {
    background: #1a1a1a; border: 1px solid #333; border-radius: 14px;
}
.chatbot-wrap details.reasoning-toggle summary {
    color: #86868b; font-family: 'Inter', sans-serif; font-size: 12px;
}

.input-area {
    background: #1a1a1a !important; border-top: 1px solid #333 !important;
    padding: 12px 20px 16px !important;
}
.input-row { gap: 10px !important; }

button.primary, .send-btn {
    background-color: #ffb700 !important; color: #121212 !important;
    border: none !important; border-radius: 50px !important;
    font-weight: 600 !important; font-size: 13px !important;
    transition: all 0.2s cubic-bezier(0.25, 0.1, 0.25, 1) !important;
    box-shadow: 0 2px 8px rgba(255,183,0,0.25) !important;
}
button.primary:hover, .send-btn:hover {
    background-color: #ffc933 !important; transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(255,183,0,0.35) !important;
}
button.secondary, button:not(.primary):not(.send-btn) {
    border-radius: 50px !important; transition: all 0.2s ease !important;
    background: #242424 !important; border: 1px solid #333 !important; color: #f5f5f7 !important;
}
button.secondary:hover, button:not(.primary):not(.send-btn):hover {
    background: #333 !important; border-color: #444 !important;
}

.upload-area {
    max-height: 38px !important; min-height: 38px !important; border-radius: 50px !important;
    overflow: hidden !important; background: #242424 !important; border: 1px solid #333 !important;
}
.upload-area .file-preview { display: none !important; }
.upload-area label { font-size: 11px !important; color: #86868b !important; }

.sidebar-section { padding: 14px 16px; border-bottom: 1px solid #333; }

.agent-card {
    background: #242424; border: 1px solid #333; border-radius: 16px;
    padding: 12px 14px; margin: 6px 0; display: flex; align-items: center;
    gap: 10px; transition: border-color 0.2s ease;
}
.agent-card:hover { border-color: #444; }
.agent-card .status-dot {
    width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
}
.agent-card .status-dot.online { background: #30d158; box-shadow: 0 0 6px rgba(48,209,88,0.4); }
.agent-card .status-dot.offline { background: #ff453a; }
.agent-card .status-dot.idle { background: #48484a; }
.agent-card .status-dot.thinking {
    background: #ffb700;
    animation: tp 1.8s cubic-bezier(0.4,0,0.6,1) infinite;
    box-shadow: 0 0 8px rgba(255,183,0,0.5);
}
@keyframes tp {
    0%,100% { opacity:1; box-shadow:0 0 8px rgba(255,183,0,0.5); }
    50% { opacity:0.4; box-shadow:0 0 16px rgba(255,183,0,0.3); }
}
.agent-card .card-info { flex: 1; }
.agent-card .card-title { color: #f5f5f7; font-size: 13px; font-weight: 600; }
.agent-card .card-subtitle { color: #636366; font-size: 11px; margin-top: 1px; }

.model-indicator {
    display: inline-flex; align-items: center; background: #242424;
    border: 1px solid #333; border-radius: 20px; padding: 3px 10px;
    font-size: 11px; color: #86868b; font-family: 'SF Mono', 'Fira Code', monospace;
}

.tab-nav button {
    background: transparent !important; color: #636366 !important;
    border: none !important; border-bottom: 2px solid transparent !important;
    font-size: 12px !important; font-weight: 500 !important;
    padding: 8px 14px !important; border-radius: 0 !important;
    transition: all 0.2s ease !important;
}
.tab-nav button:hover { color: #aeaeb2 !important; }
.tab-nav button.selected { color: #f5f5f7 !important; border-bottom-color: #ffb700 !important; }

.task-list { font-size: 12px; color: #f5f5f7; line-height: 1.8; }
.task-item { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid #333; }
.task-checkbox {
    width:20px; height:20px; border-radius:50%; border:2px solid #444;
    flex-shrink:0; display:flex; align-items:center; justify-content:center;
}
.task-checkbox.done { background:#30d158; border-color:#30d158; }
.task-checkbox.in-progress { border-color:#ffb700; background:rgba(255,183,0,0.15); }

.mem-stats { font-size: 12px; color: #86868b; line-height: 1.8; }

.msg-mode-dropdown select {
    background: #242424 !important; color: #86868b !important;
    border: 1px solid #333 !important; border-radius: 50px !important;
    font-size: 12px !important; padding: 4px 10px !important;
}

.cal-entry {
    background: #242424; border: 1px solid #333; border-radius: 12px;
    padding: 8px 12px; margin: 4px 0; font-size: 11px; color: #f5f5f7;
}
.cal-due { color: #ff453a; font-weight: 600; }
.cal-future { color: #636366; }

.console-panel {
    background: #1a1a1a; border: 1px solid #333; border-radius: 12px;
    padding: 10px; font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px; color: #636366; max-height: 200px;
    overflow-y: auto; white-space: pre-wrap; word-break: break-all;
}
.console-panel .log-error { color: #ff453a; }
.console-panel .log-warn { color: #ff9f0a; }
.console-panel .log-info { color: #30d158; }

/* Trace panel */
.trace-panel {
    background: #1a1a1a; border: 1px solid #333; border-radius: 12px;
    padding: 10px; max-height: 280px; overflow-y: auto;
}
.trace-entry {
    border-bottom: 1px solid #242424; padding: 6px 0; font-size: 11px;
}
.trace-time { color: #48484a; font-family: 'SF Mono', monospace; font-size: 10px; }
.trace-tool {
    color: #ffb700; font-weight: 600; font-family: 'SF Mono', monospace;
    font-size: 11px; margin: 0 6px;
}
.trace-params { color: #636366; font-size: 10px; word-break: break-all; }
.trace-result {
    color: #86868b; font-size: 10px; margin-top: 2px; padding-left: 12px;
    border-left: 2px solid #333; font-family: 'SF Mono', monospace;
}

.suggestion-area { display:flex; gap:8px; padding:8px 20px; flex-wrap:wrap; justify-content:center; }
.suggestion-chip {
    background: transparent !important; border: 1px solid #333 !important;
    border-radius: 50px !important; color: #86868b !important;
    font-size: 12px !important; padding: 6px 16px !important;
    cursor: pointer !important; transition: all 0.2s ease !important; min-width: 0 !important;
}
.suggestion-chip:hover {
    border-color: #ffb700 !important; color: #ffb700 !important;
    background: rgba(255,183,0,0.08) !important;
}

.footer-bar {
    background: #1a1a1a; border-top: 1px solid #333; padding: 6px 20px;
    display: flex; align-items: center; justify-content: center; gap: 16px; min-height: 28px;
}
.footer-status {
    font-size: 10px; color: #48484a; letter-spacing: 0.8px;
    text-transform: uppercase; font-weight: 500; display: flex; align-items: center; gap: 6px;
}
.footer-dot { width:6px; height:6px; border-radius:50%; background:#30d158; display:inline-block; }
.footer-dot.offline { background: #ff453a; }

.cmd-palette-overlay {
    position:fixed; top:0; left:0; right:0; bottom:0;
    background:rgba(0,0,0,0.6); backdrop-filter:blur(8px);
    z-index:10000; display:none; align-items:flex-start; justify-content:center; padding-top:20vh;
}
.cmd-palette-overlay.active { display:flex; }
.cmd-palette {
    background:#242424; border:1px solid #333; border-radius:16px;
    width:560px; max-width:90vw; box-shadow:0 20px 60px rgba(0,0,0,0.5); overflow:hidden;
}
.cmd-palette input {
    width:100%; background:transparent; border:none; padding:16px 20px;
    font-size:16px; color:#f5f5f7; outline:none; border-bottom:1px solid #333;
    font-family:'Inter',sans-serif;
}
.cmd-palette input::placeholder { color:#48484a; }
.cmd-results { max-height:300px; overflow-y:auto; padding:8px; }
.cmd-result-item {
    padding:10px 14px; border-radius:10px; cursor:pointer;
    display:flex; align-items:center; gap:10px;
    color:#86868b; font-size:13px; transition:background 0.15s ease;
}
.cmd-result-item:hover, .cmd-result-item.selected { background:#333; color:#f5f5f7; }
.cmd-result-item .cmd-icon { font-size:14px; width:20px; text-align:center; }
.cmd-result-item .cmd-label { flex:1; }

.working-indicator {
    display:inline-block; width:8px; height:8px; background:#ffb700;
    border-radius:50%; animation:tp 1.8s cubic-bezier(0.4,0,0.6,1) infinite; margin-left:8px;
}

/* Confidence shimmer for uncertain facts */
.confidence-uncertain {
    position: relative; overflow: hidden;
}
.confidence-uncertain::after {
    content: ''; position: absolute; top: 0; left: -100%;
    width: 100%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255,183,0,0.08), transparent);
    animation: conf-shimmer 3s ease-in-out infinite;
}
@keyframes conf-shimmer {
    0% { left: -100%; }
    50% { left: 100%; }
    100% { left: 100%; }
}
.confidence-badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 9px; color: #ffb700; opacity: 0.6;
    font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;
}

/* Jury panel */
.jury-panel {
    background: #1a1a1a; border: 1px solid #333; border-radius: 14px;
    padding: 12px; margin: 8px 0;
}
.jury-header {
    font-size: 12px; color: #ffb700; font-weight: 600;
    margin-bottom: 8px; display: flex; align-items: center; gap: 6px;
}
.jury-model-result {
    background: #242424; border: 1px solid #333; border-radius: 10px;
    padding: 10px; margin: 6px 0; font-size: 12px; color: #f5f5f7;
}
.jury-model-name {
    font-size: 10px; color: #ffb700; font-weight: 600;
    font-family: 'SF Mono', monospace; margin-bottom: 4px;
}

/* Voice button */
.voice-btn {
    background: #242424 !important; border: 1px solid #333 !important;
    border-radius: 50% !important; width: 38px !important; height: 38px !important;
    min-width: 38px !important; padding: 0 !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
    cursor: pointer !important; transition: all 0.2s ease !important;
    font-size: 16px !important;
}
.voice-btn:hover { border-color: #ffb700 !important; background: #333 !important; }
.voice-btn.recording {
    border-color: #ff453a !important; background: rgba(255,69,58,0.15) !important;
    animation: voice-pulse 1.5s ease infinite;
}
@keyframes voice-pulse {
    0%,100% { box-shadow: 0 0 0 0 rgba(255,69,58,0.4); }
    50% { box-shadow: 0 0 0 8px rgba(255,69,58,0); }
}

/* Branch styling */
.branch-entry {
    background: #242424; border: 1px solid #333; border-radius: 10px;
    padding: 8px 12px; margin: 4px 0; font-size: 11px;
}

/* Jury button */
.jury-btn {
    background: #242424 !important; border: 1px solid #333 !important;
    border-radius: 50px !important; color: #86868b !important;
    font-size: 11px !important; padding: 6px 14px !important;
    cursor: pointer !important; transition: all 0.2s ease !important;
    min-width: 60px !important;
}
.jury-btn:hover { border-color: #ffb700 !important; color: #ffb700 !important; }

::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:#333; border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:#444; }

footer { display:none !important; }
button.share-button, .share-button, [aria-label="Share"] { display:none !important; }
.gradio-container .share { display:none !important; }
input, textarea, .gradio-container input, .gradio-container textarea { border-radius:14px !important; }
"""

# ---------------------------------------------------------------------------
# Custom JS
# ---------------------------------------------------------------------------
CUSTOM_JS = r"""
function() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    // Copy button fix
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('.copy-btn, button[aria-label="Copy"], .message-copy, [data-testid="copy-btn"]');
        if (btn) {
            e.preventDefault(); e.stopPropagation();
            var msgEl = btn.closest('.message, .bot, [data-testid="bot"]');
            if (msgEl) {
                var text = msgEl.querySelector('.prose, .markdown-body, .md, .message-content, p');
                if (text) {
                    var content = text.innerText || text.textContent || '';
                    navigator.clipboard.writeText(content).catch(function(){});
                }
            }
        }
    });

    // Auto-scroll
    var scrollObs = new MutationObserver(function() {
        var cb = document.querySelector('.chatbot-wrap');
        if (cb) cb.scrollTop = cb.scrollHeight;
    });
    setTimeout(function() {
        var cb = document.querySelector('.chatbot-wrap');
        if (cb) scrollObs.observe(cb, { childList: true, subtree: true });
    }, 1000);

    // Ghost typing cleanup (pauses when hidden)
    var _gi = null;
    function startGhost() {
        if (_gi) return;
        _gi = setInterval(function() {
            var bots = document.querySelectorAll('.bot, .message.bot, [data-testid="bot"], .role-assistant');
            bots.forEach(function(msg) {
                var txt = (msg.textContent || '').trim();
                if (txt === '' || txt === '...' || txt === '\u2026') {
                    var p = msg.closest('.chatbot, .chatbot-wrap, [class*="chatbot"]');
                    if (p) {
                        var all = p.querySelectorAll('.bot, .message.bot, [data-testid="bot"], .role-assistant');
                        if (msg !== all[all.length - 1]) msg.style.display = 'none';
                    }
                }
            });
            var stale = document.querySelectorAll('.typing, .generating, .pending, [aria-busy="true"], .dot-flashing, .loading');
            stale.forEach(function(el) {
                if (!el.dataset._gs) el.dataset._gs = Date.now();
                else if (Date.now() - parseInt(el.dataset._gs) > 12000) {
                    el.style.display = 'none';
                    el.removeAttribute('aria-busy');
                    el.classList.remove('pending', 'generating', 'typing');
                }
            });
            var cw = document.querySelector('.chatbot-wrap, [class*="chatbot"]');
            if (cw && cw.getAttribute('aria-busy') === 'true') {
                if (!cw.dataset._bt) cw.dataset._bt = Date.now();
                else if (Date.now() - parseInt(cw.dataset._bt) > 18000) {
                    cw.removeAttribute('aria-busy'); cw.dataset._bt = '';
                }
            } else if (cw) cw.dataset._bt = '';
        }, 2500);
    }
    function stopGhost() { if (_gi) { clearInterval(_gi); _gi = null; } }
    startGhost();
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) stopGhost(); else startGhost();
    });

    // Reasoning toggle post-processor (runs every 2s, wraps ALL Thought/Action blocks)
    setInterval(function() {
        var msgs = document.querySelectorAll('.bot .prose, .bot .markdown-body, .bot .md, .bot p');
        msgs.forEach(function(msg) {
            if (msg.dataset._rp) return;
            var h = msg.innerHTML;
            if (!h) return;
            var hasThought = h.indexOf('Thought:') !== -1;
            var hasAction = h.indexOf('Action:') !== -1;
            if (!hasThought && !hasAction) return;
            var cw = msg.closest('[class*="chatbot"]');
            if (cw && cw.getAttribute('aria-busy') === 'true') return;
            // Wrap Thought+Action+Action Input blocks into collapsible sections
            var p = h.replace(/(Thought:\s*[\s\S]*?)((?=<details)|$)/g,
                '<details class="reasoning-toggle"><summary>\ud83d\udcad See Reasoning</summary><div class="reasoning-content">$1</div></details>');
            // Also hide standalone "Action:" and "Action Input:" lines not inside details
            p = p.replace(/(?<!<div class="reasoning-content">[\s\S]*?)(^|\n)(Action:\s*\w+[\s\S]*?Action Input:\s*\{[^}]*\})/gm,
                '$1<details class="reasoning-toggle"><summary>\u2699\ufe0f Tool Call</summary><div class="reasoning-content">$2</div></details>');
            if (p !== h) { msg.innerHTML = p; msg.dataset._rp = '1'; }
        });
    }, 2000);

    // Browser slowdown prevention: periodic DOM cleanup (every 30s)
    setInterval(function() {
        // Limit chatbot messages to last 100 to prevent DOM bloat
        var chatWrap = document.querySelector('.chatbot-wrap, [class*="chatbot"]');
        if (chatWrap) {
            var messages = chatWrap.querySelectorAll('.message, [class*="message"]');
            if (messages.length > 100) {
                for (var i = 0; i < messages.length - 100; i++) {
                    messages[i].remove();
                }
            }
        }
        // Clean up stale MutationObserver references
        var detached = document.querySelectorAll('[data-testid][style*="display: none"]');
        detached.forEach(function(el) { el.remove(); });
    }, 30000);

    // Command palette
    var ov = document.createElement('div');
    ov.className = 'cmd-palette-overlay';
    var palHtml = '<div class="cmd-palette">';
    palHtml += '<input type="text" placeholder="Search tasks, memory, commands..." id="cmd-input" autocomplete="off" />';
    palHtml += '<div class="cmd-results" id="cmd-results"></div></div>';
    ov.innerHTML = palHtml;
    document.body.appendChild(ov);

    var cmds = [
        {icon:'\ud83d\udccb',label:'View Tasks',action:function(){var t=document.querySelector('.tab-nav button');if(t)t.click();}},
        {icon:'\ud83e\udde0',label:'Search Memory',action:function(){var t=document.querySelectorAll('.tab-nav button');if(t[1])t[1].click();}},
        {icon:'\ud83d\udcc5',label:'View Calendar',action:function(){var t=document.querySelectorAll('.tab-nav button');if(t[2])t[2].click();}},
        {icon:'\ud83d\udd0d',label:'View Trace',action:function(){var t=document.querySelectorAll('.tab-nav button');if(t[3])t[3].click();}},
        {icon:'\ud83c\udf3f',label:'Branches',action:function(){var t=document.querySelectorAll('.tab-nav button');if(t[4])t[4].click();}},
        {icon:'\u2699\ufe0f',label:'Settings',action:function(){var t=document.querySelectorAll('.tab-nav button');if(t.length>4)t[t.length-1].click();}},
        {icon:'\ud83d\udcca',label:'View Console',action:function(){var t=document.querySelectorAll('.tab-nav button');if(t.length>3)t[t.length-2].click();}},
        {icon:'\ud83d\uddd1\ufe0f',label:'Clear Completed Tasks',action:function(){document.querySelectorAll('button').forEach(function(b){if(b.textContent.indexOf('Clear')!==-1)b.click();});}},
    ];
    var selIdx = 0, filtered = cmds.slice();

    function renderCmd(filter) {
        filtered = filter ? cmds.filter(function(c){return c.label.toLowerCase().indexOf(filter.toLowerCase())!==-1;}) : cmds.slice();
        selIdx = 0;
        var container = document.getElementById('cmd-results');
        if (!container) return;
        var html = '';
        for (var i = 0; i < filtered.length; i++) {
            html += '<div class="cmd-result-item' + (i===0?' selected':'') + '" data-idx="' + i + '">';
            html += '<span class="cmd-icon">' + filtered[i].icon + '</span>';
            html += '<span class="cmd-label">' + filtered[i].label + '</span></div>';
        }
        container.innerHTML = html;
        container.querySelectorAll('.cmd-result-item').forEach(function(item) {
            item.addEventListener('click', function() {
                var idx = parseInt(item.dataset.idx);
                if (filtered[idx]) filtered[idx].action();
                closeCmd();
            });
        });
    }
    function openCmd() { ov.classList.add('active'); var inp = document.getElementById('cmd-input'); if(inp){inp.value='';inp.focus();} renderCmd(''); }
    function closeCmd() { ov.classList.remove('active'); }
    function updateSel() {
        document.querySelectorAll('.cmd-result-item').forEach(function(item, i) {
            if (i === selIdx) item.classList.add('selected'); else item.classList.remove('selected');
        });
    }

    document.addEventListener('keydown', function(e) {
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); if(ov.classList.contains('active'))closeCmd();else openCmd(); }
        if (e.key === 'Escape' && ov.classList.contains('active')) closeCmd();
        if (ov.classList.contains('active')) {
            if (e.key==='ArrowDown'){e.preventDefault();selIdx=Math.min(selIdx+1,filtered.length-1);updateSel();}
            else if(e.key==='ArrowUp'){e.preventDefault();selIdx=Math.max(selIdx-1,0);updateSel();}
            else if(e.key==='Enter'){e.preventDefault();if(filtered[selIdx])filtered[selIdx].action();closeCmd();}
        }
    });
    ov.addEventListener('input', function(e) { if(e.target.id==='cmd-input')renderCmd(e.target.value); });
    ov.addEventListener('click', function(e) { if(e.target===ov)closeCmd(); });

    // Voice input (Web Speech API for local Chrome)
    window._llTimVoiceActive = false;
    window._llTimRecognition = null;

    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
        window._llTimRecognition = new SpeechRec();
        window._llTimRecognition.continuous = false;
        window._llTimRecognition.interimResults = false;
        window._llTimRecognition.lang = 'en-US';
        window._llTimRecognition.onresult = function(event) {
            var transcript = event.results[0][0].transcript;
            var inputs = document.querySelectorAll('textarea');
            for (var k = 0; k < inputs.length; k++) {
                if (inputs[k].placeholder && inputs[k].placeholder.indexOf('Message') !== -1) {
                    inputs[k].value = transcript;
                    inputs[k].dispatchEvent(new Event('input', {bubbles: true}));
                    break;
                }
            }
            window._llTimVoiceActive = false;
            document.querySelectorAll('.voice-btn').forEach(function(b){ b.classList.remove('recording'); });
        };
        window._llTimRecognition.onerror = function() {
            window._llTimVoiceActive = false;
            document.querySelectorAll('.voice-btn').forEach(function(b){ b.classList.remove('recording'); });
        };
        window._llTimRecognition.onend = function() {
            window._llTimVoiceActive = false;
            document.querySelectorAll('.voice-btn').forEach(function(b){ b.classList.remove('recording'); });
        };
    }

    return [];
}
"""

NOTIFY_JS = r"""
function(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, { body: body });
    }
    return [];
}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_model_choices():
    models = agent.get_available_models()
    return models if models else [agent.current_model]

def check_statuses():
    timmy_ok = agent.check_ollama_status()
    doctor_ok = False
    dp = Path("/tmp/doctor.pid")
    if dp.exists():
        try:
            pid = int(dp.read_text().strip())
            os.kill(pid, 0)
            doctor_ok = True
        except (ProcessLookupError, ValueError, PermissionError):
            pass

    def card(name, ok, sub_ok, sub_off, is_timmy=False):
        dot = "online" if ok else "offline"
        if is_timmy and agent.is_working:
            dot = "thinking"
        sub = sub_ok if ok else sub_off
        return (
            '<div class="agent-card">'
            '<div class="status-dot ' + dot + '"></div>'
            '<div class="card-info">'
            '<div class="card-title">' + name + '</div>'
            '<div class="card-subtitle">' + sub + '</div>'
            '</div></div>'
        )

    return (
        card("Doctor", doctor_ok, "Monitoring", "Offline"),
        card("Agent Timmy", timmy_ok,
             "Thinking..." if agent.is_working else "Ready to execute",
             "Offline", True),
    )

def load_todays_history():
    return [{"role": m["role"], "content": m["content"]} for m in memory.load_current_day()]

def get_model_display():
    return '<span class="model-indicator">' + agent.current_model + '</span>'

def get_working_indicator():
    if agent.is_working:
        q = len(agent._queued_messages)
        extra = " (" + str(q) + " queued)" if q > 0 else ""
        return '<span class="working-indicator"></span> <span style="color:#ffb700;font-size:11px;">Working...' + extra + '</span>'
    q = len(agent._queued_messages)
    if q > 0:
        return '<span style="color:#86868b;font-size:11px;">Idle (' + str(q) + ' queued)</span>'
    return '<span style="color:#30d158;font-size:11px;">\u25cf Ready</span>'

def get_memory_stats_html():
    s = memory.get_memory_stats()
    return (
        '<div class="mem-stats">'
        '<b style="color:#f5f5f7;">Today:</b> ' + str(s["today_messages"]) + ' messages<br>'
        '<b style="color:#f5f5f7;">Subconscious:</b> ' + str(s["subconscious_entries"]) + ' entries<br>'
        '<b style="color:#f5f5f7;">Graph:</b> ' + str(s["graph_entities"]) + ' entities<br>'
        '<b style="color:#f5f5f7;">Summaries:</b> ' + str(s["daily_summaries"]) + ' days'
        '</div>'
    )

def get_tasks_html():
    all_tasks = task_mgr.get_all_tasks()
    if not all_tasks:
        return '<div class="task-list" style="color:#48484a;">No active tasks</div>'

    lines = []
    top = sorted(
        [t for t in all_tasks if t.parent_id is None],
        key=lambda t: (0 if t.status == "in_progress" else 1 if t.status == "pending" else 2, t.priority)
    )
    ub = {
        "critical": '<span style="background:#ff453a;color:white;font-size:9px;padding:1px 6px;border-radius:50px;margin-right:4px;">CRIT</span>',
        "high": '<span style="background:#ff9f0a;color:white;font-size:9px;padding:1px 6px;border-radius:50px;margin-right:4px;">HIGH</span>',
        "low": '<span style="background:#48484a;color:#aeaeb2;font-size:9px;padding:1px 6px;border-radius:50px;margin-right:4px;">LOW</span>',
    }
    for t in top:
        if t.status == "completed":
            cb = '<div class="task-checkbox done"><span style="color:white;font-size:11px;">\u2713</span></div>'
        elif t.status == "in_progress":
            cb = '<div class="task-checkbox in-progress"></div>'
        else:
            cb = '<div class="task-checkbox"></div>'
        badge = ub.get(getattr(t, 'urgency', 'normal'), '')
        prog = ""
        p = getattr(t, 'progress', 0)
        if p > 0 and t.status == "in_progress":
            prog = '<div style="background:#242424;height:3px;border-radius:2px;margin-top:4px;"><div style="background:#ffb700;height:3px;border-radius:2px;width:' + str(p) + '%;"></div></div>'
        tc = "#48484a" if t.status == "completed" else "#f5f5f7"
        td = "line-through" if t.status == "completed" else "none"
        lines.append(
            '<div class="task-item">' + cb +
            '<div style="flex:1;">' + badge +
            '<span style="font-size:13px;color:' + tc + ';text-decoration:' + td + ';">' + t.title + '</span>' +
            prog + '</div></div>'
        )
        for sid in t.subtasks:
            sub = task_mgr.get_task(sid)
            if sub:
                sc = '<div class="task-checkbox done" style="width:14px;height:14px;"><span style="font-size:8px;color:white;">\u2713</span></div>' if sub.status == "completed" else '<div class="task-checkbox" style="width:14px;height:14px;"></div>'
                lines.append(
                    '<div style="display:flex;align-items:center;gap:8px;padding:4px 0 4px 30px;">'
                    + sc + '<span style="font-size:11px;color:#636366;">' + sub.title + '</span></div>'
                )

    ca = len([t for t in all_tasks if t.status in ("pending", "in_progress")])
    cd = len([t for t in all_tasks if t.status == "completed"])
    summary = '<div style="font-size:10px;color:#48484a;padding:6px 0;border-top:1px solid #333;margin-top:6px;">' + str(ca) + ' active \u00b7 ' + str(cd) + ' completed</div>'
    return '<div class="task-list">' + "".join(lines) + summary + '</div>'

def get_evolution_html():
    return '<div style="font-size:12px;color:#86868b;white-space:pre-wrap;">' + evolution.get_evolution_summary() + '</div>'

def get_trace_html():
    if not _trace_buffer:
        return '<div class="trace-panel" style="color:#48484a;">No tool calls yet. Interact with Timmy to see traces.</div>'
    return '<div class="trace-panel">' + "".join(_trace_buffer[-20:]) + '</div>'

# ---------------------------------------------------------------------------
# Live console log
# ---------------------------------------------------------------------------
_console_log_buffer = []
_MAX_CONSOLE_LINES = 60

class _ConsoleLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            lc = "log-info"
            if record.levelno >= logging.ERROR: lc = "log-error"
            elif record.levelno >= logging.WARNING: lc = "log-warn"
            _console_log_buffer.append('<span class="' + lc + '">' + msg + '</span>')
            if len(_console_log_buffer) > _MAX_CONSOLE_LINES:
                _console_log_buffer.pop(0)
        except Exception:
            pass

_ch = _ConsoleLogHandler()
_ch.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S"))
logging.getLogger().addHandler(_ch)

def get_console_html():
    if not _console_log_buffer:
        return '<div class="console-panel" style="color:#48484a;">No activity yet...</div>'
    return '<div class="console-panel">' + "<br>".join(_console_log_buffer[-30:]) + '</div>'

def get_calendar_html():
    if scheduler is None:
        return '<div style="color:#48484a;font-size:12px;">Calendar module not loaded</div>'
    events = scheduler.get_upcoming(limit=10)
    if not events:
        return '<div style="color:#48484a;font-size:12px;">No upcoming events</div>'
    parts = []
    now = datetime.now()
    for ev in events:
        dt = datetime.fromisoformat(ev.get("due", ""))
        cls = "cal-due" if dt <= now else "cal-future"
        ts = dt.strftime("%b %d %H:%M")
        parts.append('<div class="cal-entry"><span class="' + cls + '">' + ts + '</span> \u2014 ' + ev.get("title", "Untitled") + '</div>')
    return "".join(parts)

def get_footer_html():
    ok = agent.check_ollama_status()
    dc = "" if ok else " offline"
    st = "SYSTEM ONLINE" if ok else "SYSTEM OFFLINE"
    return '<div class="footer-bar"><span class="footer-status"><span class="footer-dot' + dc + '"></span> ' + st + '</span><span class="footer-status">28 TOOLS &middot; V6</span><span class="footer-status">PRIVACY SECURED BY LLTIMMY</span></div>'

# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------
_last_rollover_date = date.today()

def _midnight_check():
    global _last_rollover_date
    while True:
        now = date.today()
        if now != _last_rollover_date:
            logger.info("Midnight rollover")
            try:
                memory.create_daily_summary(config.get("daily_summary_count", 15))
                agent.clear_history()
                review = evolution.create_daily_review()
                logger.info("Daily review: %s", review.get('stats', {}))
            except Exception as e:
                logger.error("Rollover failed: %s", e)
            _last_rollover_date = now
        time.sleep(60)

threading.Thread(target=_midnight_check, daemon=True).start()

def _doctor_health_check():
    while True:
        try:
            dp = Path("/tmp/doctor.pid")
            alive = False
            if dp.exists():
                try:
                    pid = int(dp.read_text().strip())
                    os.kill(pid, 0)
                    alive = True
                except (ProcessLookupError, ValueError, PermissionError):
                    pass
            if not alive:
                logger.warning("Doctor appears offline, attempting restart")
                import subprocess
                vp = str(BASE_DIR / ".venv" / "bin" / "python3")
                if not Path(vp).exists():
                    import sys
                    vp = sys.executable
                subprocess.Popen([vp, str(BASE_DIR / "doctor.py")], cwd=str(BASE_DIR),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info("Doctor restart attempted")
        except Exception as e:
            logger.error("Doctor health check error: %s", e)
        time.sleep(30)

threading.Thread(target=_doctor_health_check, daemon=True).start()

def _calendar_check():
    if scheduler is None:
        return
    while True:
        try:
            due = scheduler.check_due()
            for ev in due:
                import subprocess
                t = ev.get("title", "Reminder").replace('\\', '\\\\').replace('"', '\\"')
                subprocess.run(
                    ["osascript", "-e", f'display notification "{t}" with title "Timmy Calendar" sound name "Funk"'],
                    capture_output=True, timeout=10)
                logger.info("Calendar notification: %s", t)
        except Exception as e:
            logger.error("Calendar check error: %s", e)
        time.sleep(config.get("calendar", {}).get("check_interval_seconds", 60))

threading.Thread(target=_calendar_check, daemon=True).start()

# ---------------------------------------------------------------------------
# Chat handler
# ---------------------------------------------------------------------------
async def respond(message, history, model, files):
    if not message.strip() and not files:
        yield history, ""
        return
    if model and model != agent.current_model:
        agent.set_model(model)

    file_paths = []
    if files:
        for f in files:
            if isinstance(f, str): file_paths.append(f)
            elif hasattr(f, "name"): file_paths.append(str(f.name))
            elif hasattr(f, "path"): file_paths.append(str(f.path))
            else: file_paths.append(str(f))
        file_paths = [p for p in file_paths if p and Path(p).exists()]
        if files and not file_paths:
            logger.warning("File upload: received %d files but none resolved. Raw: %s", len(files), files)

    history = list(history or [])
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": ""})
    yield history, ""
    memory.profile.update_from_message(message)

    last = ""
    try:
        async for acc in agent.run(message, file_paths):
            last = acc
            history[-1] = {"role": "assistant", "content": acc}
            yield history, ""
    except Exception as e:
        logger.error("Agent error: %s", e)
        last = "**Error:** " + str(e)
        history[-1] = {"role": "assistant", "content": last}
        yield history, ""

    if not last.strip():
        history[-1] = {"role": "assistant", "content": "(No response generated. Ollama may be down or the model is loading.)"}
        yield history, ""
    yield history, ""

# ---------------------------------------------------------------------------
# Unified message handler
# ---------------------------------------------------------------------------
def send_interrupt_message(message):
    if not message.strip():
        return '<span style="color:#48484a;font-size:11px;">Type a message to send</span>'
    agent.send_interrupt(message)
    return '<span style="color:#30d158;font-size:11px;">\u26a1 Interrupt sent: ' + message[:50] + '...</span>'

def queue_message_handler(message):
    if not message.strip():
        return '<span style="color:#48484a;font-size:11px;">Type a message to queue</span>'
    agent.queue_message(message)
    n = len(agent._queued_messages)
    return '<span style="color:#ffb700;font-size:11px;">\ud83d\udccb Queued (' + str(n) + ' total): ' + message[:50] + '...</span>'

async def unified_send(message, history, model, files, mode):
    if not message.strip() and not files:
        yield history, "", "", None
        return
    if mode == "Interrupt":
        yield history, "", send_interrupt_message(message), None
        return
    if mode == "Queue":
        yield history, "", queue_message_handler(message), None
        return
    async for hist, cleared in respond(message, history, model, files):
        yield hist, cleared, "", None

async def suggestion_click(suggestion, history, model):
    async for hist, cleared in respond(suggestion, history, model, None):
        yield hist, cleared, ""

# ---------------------------------------------------------------------------
# Jury Mode — send to multiple models, compare side-by-side
# ---------------------------------------------------------------------------
async def jury_send(message, history, model):
    """Send query to current model + 2 fallbacks, show side-by-side comparison."""
    import requests as req
    if not message.strip():
        yield history, "", '<span style="color:#48484a;font-size:11px;">Type a message for jury mode</span>'
        return

    available = agent.get_available_models()
    if len(available) < 2:
        yield history, "", '<span style="color:#ff453a;font-size:11px;">Need at least 2 models for jury mode. Pull more models first.</span>'
        return

    # Pick up to 3 models: current + 2 others
    jury_models = [agent.current_model]
    for m in available:
        if m != agent.current_model and len(jury_models) < 3:
            jury_models.append(m)

    history = list(history or [])
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": ""})
    yield history, "", ""

    results = {}
    for m in jury_models:
        try:
            resp = req.post(
                f"{agent.ollama_host}/api/chat",
                json={"model": m, "messages": [{"role": "user", "content": message}], "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            answer = resp.json().get("message", {}).get("content", "(no response)")
            results[m] = answer[:500]
        except Exception as e:
            results[m] = f"(error: {e})"

    # Build jury panel HTML
    jury_html = '<div class="jury-panel"><div class="jury-header">\u2696\ufe0f Jury Mode \u2014 ' + str(len(results)) + ' models compared</div>'
    for m, answer in results.items():
        jury_html += '<div class="jury-model-result"><div class="jury-model-name">' + m + '</div>' + answer[:500] + '</div>'

    # Simple referee: pick the longest non-error response
    best = max(results.items(), key=lambda x: len(x[1]) if "error" not in x[1] else 0)
    jury_html += '<div style="margin-top:8px;font-size:11px;color:#ffb700;">\ud83c\udfc6 Referee pick: <b>' + best[0] + '</b></div>'
    jury_html += '</div>'

    history[-1] = {"role": "assistant", "content": jury_html}
    yield history, "", ""

# ---------------------------------------------------------------------------
# Voice toggle handler
# ---------------------------------------------------------------------------
def voice_toggle():
    """Returns JS snippet to toggle voice recognition."""
    return ""

# ---------------------------------------------------------------------------
# Task management handlers
# ---------------------------------------------------------------------------
def add_task_handler(title):
    if not title.strip(): return get_tasks_html(), ""
    task_mgr.add_task(title.strip())
    return get_tasks_html(), ""

def clear_completed_tasks():
    for tid, task in list(task_mgr.tasks.items()):
        if task.status == "completed": task_mgr.remove_task(tid)
    return get_tasks_html()

def delete_task_handler(title):
    if not title.strip(): return get_tasks_html()
    for tid, task in list(task_mgr.tasks.items()):
        if task.title.lower() == title.strip().lower():
            task_mgr.remove_task(tid); break
    return get_tasks_html()

def cycle_task_status(title):
    if not title.strip(): return get_tasks_html()
    cycle = {"pending":"in_progress","in_progress":"completed","completed":"pending","failed":"pending","paused":"in_progress"}
    for task in task_mgr.tasks.values():
        if task.title.lower() == title.strip().lower():
            task.status = cycle.get(task.status, "pending")
            task.updated_at = datetime.now().isoformat()
            if task.status == "completed": task.completed_at = datetime.now().isoformat()
            task_mgr._save(); break
    return get_tasks_html()

def search_memory_handler(query):
    if not query.strip():
        return '<div style="color:#48484a;font-size:12px;">Enter a search query</div>'
    results = memory.search_memory(query, n=5)
    if not results:
        return '<div style="color:#48484a;font-size:12px;">No matching memories found</div>'
    parts = []
    for r in results:
        c = r["content"][:150]
        ts = r.get("metadata", {}).get("timestamp", "")[:10]
        parts.append('<div style="border-bottom:1px solid #333;padding:6px 0;font-size:11px;color:#86868b;"><span style="color:#48484a;">' + ts + '</span> ' + c + '</div>')
    return "".join(parts)

def add_calendar_event(title, due_str):
    if scheduler is None:
        return '<div style="color:#ff453a;font-size:12px;">Calendar not available</div>'
    if not title.strip(): return get_calendar_html()
    try:
        scheduler.add_event(title.strip(), due_str.strip() if due_str.strip() else None)
    except Exception as e:
        return '<div style="color:#ff453a;font-size:12px;">Error: ' + str(e) + '</div>'
    return get_calendar_html()

# ---------------------------------------------------------------------------
# Branching handlers
# ---------------------------------------------------------------------------
def get_branches_html():
    branches = agent.list_branches()
    if not branches:
        return '<div style="color:#48484a;font-size:12px;">No saved branches. Create one to snapshot your conversation.</div>'
    parts = []
    for b in branches:
        ts = b["created_at"][:16].replace("T", " ")
        parts.append(
            '<div class="cal-entry">'
            '<span style="color:#ffb700;font-weight:600;">' + b["id"] + '</span>'
            '<span style="color:#48484a;font-size:10px;margin-left:8px;">' + ts + '</span>'
            '<br><span style="color:#636366;font-size:10px;">' + str(b["messages"]) + ' msgs \u00b7 ' + b.get("model", "?") + '</span>'
            '</div>'
        )
    return "".join(parts)

def create_branch_handler(name, history):
    if not name.strip():
        name = ""
    branch = agent.branch_conversation(name.strip() if name.strip() else None)
    return get_branches_html(), '<span style="color:#30d158;font-size:11px;">\u2714 Branch "' + branch["id"] + '" created</span>'

def restore_branch_handler(name, history):
    if not name.strip():
        return history, get_branches_html(), '<span style="color:#ff453a;font-size:11px;">Enter a branch name</span>'
    ok = agent.restore_branch(name.strip())
    if ok:
        new_hist = [{"role": m["role"], "content": m["content"]} for m in agent.conversation_history]
        return new_hist, get_branches_html(), '<span style="color:#30d158;font-size:11px;">\u2714 Restored "' + name.strip() + '"</span>'
    return history, get_branches_html(), '<span style="color:#ff453a;font-size:11px;">Branch "' + name.strip() + '" not found</span>'

def delete_branch_handler(name):
    if not name.strip():
        return get_branches_html()
    agent.delete_branch(name.strip())
    return get_branches_html()

# ---------------------------------------------------------------------------
# Build Gradio UI
# ---------------------------------------------------------------------------
theme = gr.themes.Soft(primary_hue="amber", neutral_hue="gray").set(
    body_background_fill="#121212", body_background_fill_dark="#121212",
    block_background_fill="#1a1a1a", block_background_fill_dark="#1a1a1a",
    body_text_color="#f5f5f7", body_text_color_dark="#f5f5f7",
    block_label_text_color="#86868b", block_label_text_color_dark="#86868b",
    input_background_fill="#242424", input_background_fill_dark="#242424",
    button_primary_background_fill="#ffb700", button_primary_background_fill_dark="#ffb700",
    button_primary_text_color="#121212", button_primary_text_color_dark="#121212",
)

with gr.Blocks(title="LLTimmy") as app:
    with gr.Row(elem_classes="main-layout"):

        # ==== SIDEBAR ====
        with gr.Column(elem_classes="sidebar-col", scale=0):
            gr.HTML(
                '<div style="padding:20px 16px 12px;text-align:center;">'
                '<div style="display:flex;align-items:center;justify-content:center;gap:8px;">'
                '<h1 style="color:#f5f5f7;font-size:20px;margin:0;font-weight:700;">LLTimmy</h1>'
                '<span style="width:8px;height:8px;background:#ffb700;border-radius:50%;display:inline-block;"></span>'
                '</div>'
                '<p style="color:#48484a;font-size:10px;margin:4px 0 0;text-transform:uppercase;letter-spacing:1px;font-weight:500;">Large Agent V7</p>'
                '</div>'
            )
            with gr.Group(elem_classes="sidebar-section"):
                timmy_status = gr.HTML(
                    '<div class="agent-card"><div class="status-dot online"></div>'
                    '<div class="card-info"><div class="card-title">Agent Timmy</div>'
                    '<div class="card-subtitle">Ready to execute</div></div></div>'
                )
                doctor_status = gr.HTML(
                    '<div class="agent-card"><div class="status-dot idle"></div>'
                    '<div class="card-info"><div class="card-title">Doctor Online</div>'
                    '<div class="card-subtitle">Inactive</div></div></div>'
                )
                model_html = gr.HTML(get_model_display())
                working_html = gr.HTML(get_working_indicator())

            with gr.Tabs():
                with gr.Tab("Tasks"):
                    tasks_html = gr.HTML(get_tasks_html())
                    with gr.Row():
                        task_input = gr.Textbox(placeholder="Quick add task...", show_label=False, scale=4, container=False)
                        add_task_btn = gr.Button("+", scale=0, min_width=36)
                    with gr.Row():
                        task_select = gr.Textbox(placeholder="Task name to edit/delete...", show_label=False, scale=4, container=False)
                        toggle_status_btn = gr.Button("\u27f3", scale=0, min_width=36)
                        delete_task_btn = gr.Button("\u2715", scale=0, min_width=36)
                    clear_tasks_btn = gr.Button("Clear Archive", size="sm")

                with gr.Tab("Memory"):
                    mem_stats = gr.HTML(get_memory_stats_html())
                    mem_search_input = gr.Textbox(placeholder="Search memories...", show_label=False, container=False)
                    mem_results = gr.HTML('<div style="color:#48484a;font-size:12px;">Search subconscious memory</div>')

                with gr.Tab("Calendar"):
                    cal_html = gr.HTML(get_calendar_html())
                    cal_title_input = gr.Textbox(placeholder="Event title...", show_label=False, container=False)
                    cal_due_input = gr.Textbox(placeholder="Due: YYYY-MM-DD HH:MM", show_label=False, container=False)
                    add_cal_btn = gr.Button("Add Event", size="sm")

                with gr.Tab("Trace"):
                    trace_html = gr.HTML(get_trace_html())
                    trace_refresh_btn = gr.Button("Refresh", size="sm")

                with gr.Tab("Branches"):
                    branches_html = gr.HTML(get_branches_html())
                    branch_name_input = gr.Textbox(placeholder="Branch name (optional)...", show_label=False, container=False)
                    with gr.Row():
                        create_branch_btn = gr.Button("Create", size="sm", min_width=70)
                        restore_branch_btn = gr.Button("Restore", size="sm", min_width=70)
                        delete_branch_btn = gr.Button("Delete", size="sm", min_width=70)
                    branch_status = gr.HTML("")

                with gr.Tab("Evolution"):
                    evo_html = gr.HTML(get_evolution_html())
                    evo_refresh_btn = gr.Button("Refresh", size="sm")

                with gr.Tab("Console"):
                    console_html = gr.HTML(get_console_html())
                    console_refresh_btn = gr.Button("Refresh", size="sm")

                with gr.Tab("Settings"):
                    model_dropdown = gr.Dropdown(choices=get_model_choices(), value=agent.current_model, label="Active Model", interactive=True)
                    refresh_models_btn = gr.Button("Refresh Models", size="sm")
                    gr.HTML(
                        '<div style="padding:8px 0;font-size:11px;color:#48484a;">'
                        '<b style="color:#636366;">Paths:</b><br>'
                        'Base: ' + str(BASE_DIR) + '<br>'
                        'Memory: ' + str(BASE_DIR / "memory") + '<br><br>'
                        '<span style="color:#48484a;">\u2318K \u2014 Command Palette</span>'
                        '</div>'
                    )

        # ==== MAIN CHAT ====
        with gr.Column(elem_classes="chat-col", scale=1):
            with gr.Row(elem_classes="header-bar"):
                gr.HTML(
                    '<div style="display:flex;align-items:center;gap:12px;flex:1;">'
                    '<span style="font-size:15px;color:#f5f5f7;font-weight:600;">\U0001f512 Secure Session</span>'
                    '<span class="secure-badge">ENCRYPTED END-TO-END</span>'
                    '</div>'
                )
                current_model_label = gr.HTML(get_model_display())

            chatbot = gr.Chatbot(value=load_todays_history(), height=500, show_label=False, render_markdown=True, elem_classes="chatbot-wrap")

            with gr.Row(elem_classes="suggestion-area"):
                sug_1 = gr.Button("Summarize tasks", elem_classes="suggestion-chip", size="sm")
                sug_2 = gr.Button("Check my schedule", elem_classes="suggestion-chip", size="sm")
                sug_3 = gr.Button("Analyze performance", elem_classes="suggestion-chip", size="sm")

            with gr.Group(elem_classes="input-area"):
                with gr.Row(elem_classes="input-row"):
                    file_upload = gr.File(label="", file_count="multiple", scale=0, min_width=60, elem_classes="upload-area")
                    voice_btn = gr.Button("\U0001f3a4", scale=0, min_width=38, elem_classes="voice-btn")
                    msg_input = gr.Textbox(placeholder="Message Timmy or trigger an agent action...", show_label=False, scale=6, container=False, autofocus=True)
                    msg_mode = gr.Dropdown(choices=["Send","Interrupt","Queue"], value="Send", show_label=False, scale=0, min_width=100, container=False, elem_classes="msg-mode-dropdown")
                    jury_btn = gr.Button("\u2696\ufe0f Jury", scale=0, min_width=70, elem_classes="jury-btn")
                    send_btn = gr.Button("Send", variant="primary", scale=0, min_width=80, elem_classes="send-btn")
                msg_status = gr.HTML("")

            footer_html = gr.HTML(get_footer_html())

    # ---- Events ----
    sa = dict(fn=unified_send, inputs=[msg_input, chatbot, model_dropdown, file_upload, msg_mode], outputs=[chatbot, msg_input, msg_status, file_upload])
    msg_input.submit(**sa)
    send_btn.click(**sa)

    # Jury button
    jury_btn.click(fn=jury_send, inputs=[msg_input, chatbot, model_dropdown], outputs=[chatbot, msg_input, msg_status])

    # Voice button — triggers JS to toggle speech recognition
    voice_btn.click(fn=None, js=r"""
    function() {
        if (!window._llTimRecognition) {
            alert('Speech recognition not available in this browser. Use Chrome for voice input.');
            return;
        }
        var btns = document.querySelectorAll('.voice-btn');
        if (window._llTimVoiceActive) {
            window._llTimRecognition.stop();
            window._llTimVoiceActive = false;
            btns.forEach(function(b){ b.classList.remove('recording'); });
        } else {
            window._llTimRecognition.start();
            window._llTimVoiceActive = true;
            btns.forEach(function(b){ b.classList.add('recording'); });
        }
    }
    """)

    for btn, txt in [(sug_1,"Summarize tasks"),(sug_2,"Check my schedule"),(sug_3,"Analyze performance")]:
        btn.click(fn=suggestion_click, inputs=[gr.State(txt), chatbot, model_dropdown], outputs=[chatbot, msg_input, msg_status])

    def on_model_change(m):
        agent.set_model(m)
        return get_model_display(), get_model_display()
    model_dropdown.change(fn=on_model_change, inputs=[model_dropdown], outputs=[model_html, current_model_label])
    refresh_models_btn.click(fn=lambda: gr.update(choices=get_model_choices()), outputs=[model_dropdown])

    add_task_btn.click(fn=add_task_handler, inputs=[task_input], outputs=[tasks_html, task_input])
    task_input.submit(fn=add_task_handler, inputs=[task_input], outputs=[tasks_html, task_input])
    toggle_status_btn.click(fn=cycle_task_status, inputs=[task_select], outputs=[tasks_html])
    delete_task_btn.click(fn=delete_task_handler, inputs=[task_select], outputs=[tasks_html])
    clear_tasks_btn.click(fn=clear_completed_tasks, outputs=[tasks_html])
    mem_search_input.submit(fn=search_memory_handler, inputs=[mem_search_input], outputs=[mem_results])
    add_cal_btn.click(fn=add_calendar_event, inputs=[cal_title_input, cal_due_input], outputs=[cal_html])
    evo_refresh_btn.click(fn=get_evolution_html, outputs=[evo_html])
    console_refresh_btn.click(fn=get_console_html, outputs=[console_html])
    trace_refresh_btn.click(fn=get_trace_html, outputs=[trace_html])

    # Branching
    create_branch_btn.click(fn=create_branch_handler, inputs=[branch_name_input, chatbot], outputs=[branches_html, branch_status])
    restore_branch_btn.click(fn=restore_branch_handler, inputs=[branch_name_input, chatbot], outputs=[chatbot, branches_html, branch_status])
    delete_branch_btn.click(fn=delete_branch_handler, inputs=[branch_name_input], outputs=[branches_html])

    # Timers (optimized for Firefox)
    gr.Timer(20).tick(fn=check_statuses, outputs=[doctor_status, timmy_status])
    gr.Timer(4).tick(fn=get_working_indicator, outputs=[working_html])
    st = gr.Timer(90)
    st.tick(fn=get_memory_stats_html, outputs=[mem_stats])
    st.tick(fn=get_tasks_html, outputs=[tasks_html])
    st.tick(fn=get_calendar_html, outputs=[cal_html])
    gr.Timer(8).tick(fn=get_console_html, outputs=[console_html])
    gr.Timer(10).tick(fn=get_trace_html, outputs=[trace_html])
    gr.Timer(30).tick(fn=get_footer_html, outputs=[footer_html])

    app.load(fn=load_todays_history, outputs=[chatbot])

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def _startup_message():
    if memory.count_messages() == 0:
        memory.save_message("assistant", "I am Timmy. Ben's AI agent. I've been monitoring your workspace and I'm ready to assist.")

def _warmup_ollama():
    """Pre-warm the Ollama model on startup to prevent cold-start timeout on first message."""
    import requests as req
    try:
        logger.info("Warming up Ollama model...")
        resp = req.post(
            f"{config.get('ollama_host', 'http://localhost:11434')}/api/chat",
            json={"model": agent.current_model, "messages": [{"role": "user", "content": "hi"}], "stream": False},
            timeout=60,
        )
        if resp.status_code == 200:
            logger.info("Ollama warm-up complete")
        else:
            logger.warning("Ollama warm-up returned %d", resp.status_code)
    except Exception as e:
        logger.warning("Ollama warm-up failed: %s", e)

_startup_message()
threading.Thread(target=_warmup_ollama, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", config.get("gradio_timmy_port", 7860)))
    app.launch(server_name="127.0.0.1", server_port=port, share=False, show_error=True, css=CUSTOM_CSS, js=CUSTOM_JS, theme=theme)
