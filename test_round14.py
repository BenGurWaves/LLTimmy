#!/usr/bin/env python3
"""
Round 14 Test Agent — Grills Timmy with real messages and verifies with terminal.
"""
import asyncio
import json
import sys
import os
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from agent_core import AgentCore
from tools import ToolsSystem
from memory_manager import MemoryManager
from task_manager import TaskManager

config = json.loads((BASE_DIR / "config.json").read_text())
memory = MemoryManager(ollama_host=config.get("ollama_host", "http://localhost:11434"))
tools = ToolsSystem(config)
task_mgr = TaskManager()
agent = AgentCore(config=config, memory_manager=memory, tools_system=tools, task_mgr=task_mgr)

PASS = FAIL = PARTIAL = 0

def result(name, status, detail=""):
    global PASS, FAIL, PARTIAL
    icon = {"PASS": "\033[32mPASS\033[0m", "FAIL": "\033[31mFAIL\033[0m", "PARTIAL": "\033[33mPARTIAL\033[0m"}
    print(f"  [{icon[status]}] {name}: {detail}", flush=True)
    if status == "PASS": PASS += 1
    elif status == "FAIL": FAIL += 1
    else: PARTIAL += 1

async def send(msg: str, timeout_s: int = 180) -> str:
    """Send a message to the agent with timeout."""
    full = ""
    try:
        async def _run():
            nonlocal full
            async for chunk in agent.run(msg):
                full = chunk
        await asyncio.wait_for(_run(), timeout=timeout_s)
    except asyncio.TimeoutError:
        return full or "[TIMEOUT]"
    return full.strip()

async def run_tests():
    global PASS, FAIL, PARTIAL
    print("\n" + "="*70)
    print("ROUND 14 TEST SUITE — GRILLING TIMMY")
    print("="*70, flush=True)

    # Test 1: Task Creation + Disk Verification
    print("\n--- Test 1: Create Task + Verify ---", flush=True)
    resp = await send("Create a task called 'Test Round 14 Verification' with urgency high")
    tasks_data = json.loads((BASE_DIR / "memory" / "tasks.json").read_text())
    found = any("Test Round 14" in t.get("title", "") for t in tasks_data)
    if found:
        result("Task Create + Disk Verify", "PASS", "Task in tasks.json")
    elif "[TIMEOUT]" in resp:
        result("Task Create + Disk Verify", "FAIL", "Timed out")
    else:
        result("Task Create + Disk Verify", "FAIL", f"Not in tasks.json. Resp: {resp[:100]}")

    # Test 2: File Creation
    print("\n--- Test 2: Create File + Verify ---", flush=True)
    test_file = Path.home() / "Desktop" / "timmy_test_r14.txt"
    test_file.unlink(missing_ok=True)
    resp = await send(f"Create a file at {test_file} with content 'Round 14 test passed'")
    if test_file.exists() and "Round 14" in test_file.read_text():
        result("File Create + Verify", "PASS", "File exists with content")
    elif test_file.exists():
        result("File Create + Verify", "PARTIAL", f"File exists, content: {test_file.read_text()[:50]}")
    else:
        result("File Create + Verify", "FAIL", f"File missing. Resp: {resp[:100]}")

    # Test 3: Sandbox Exec
    print("\n--- Test 3: Sandbox Exec ---", flush=True)
    resp = await send("Use sandbox_exec to run: print(sum(range(100)))")
    if "4950" in resp:
        result("Sandbox Exec", "PASS", "Got 4950")
    else:
        result("Sandbox Exec", "FAIL", f"Resp: {resp[:100]}")

    # Test 4: Knowledge Graph
    print("\n--- Test 4: Knowledge Graph ---", flush=True)
    resp = await send('Use knowledge_graph with action add_entity, name "TestBot", entity_type "agent"')
    if "added" in resp.lower() or "testbot" in resp.lower() or "entity" in resp.lower():
        result("Knowledge Graph Add", "PASS", "Entity added")
    else:
        result("Knowledge Graph Add", "FAIL", f"Resp: {resp[:100]}")

    # Test 5: Knowledge Graph Query
    print("\n--- Test 5: Knowledge Graph Query ---", flush=True)
    resp = await send('Use knowledge_graph with action query, entity_name "TestBot"')
    if "testbot" in resp.lower() or "agent" in resp.lower():
        result("Knowledge Graph Query", "PASS", "Entity queried")
    else:
        result("Knowledge Graph Query", "FAIL", f"Resp: {resp[:100]}")

    # Test 6: Red Team Audit
    print("\n--- Test 6: Red Team Audit ---", flush=True)
    resp = await send('Use red_team_audit with content "Python is compiled and runs on JVM" and focus accuracy')
    if any(w in resp.lower() for w in ["issue", "incorrect", "error", "compiled", "jvm", "interpreted", "audit", "wrong", "inaccur"]):
        result("Red Team Audit", "PASS", "Detected errors")
    else:
        result("Red Team Audit", "FAIL", f"Resp: {resp[:150]}")

    # Test 7: Clipboard
    print("\n--- Test 7: Clipboard ---", flush=True)
    resp = await send('Use write_clipboard to copy "TIMMY_R14_CLIP_TEST" to clipboard')
    clip = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
    if "TIMMY_R14_CLIP_TEST" in clip.stdout:
        result("Clipboard Write", "PASS", "Verified via pbpaste")
    else:
        result("Clipboard Write", "PARTIAL", f"pbpaste: {clip.stdout[:50]}, resp: {resp[:80]}")

    # Test 8: Anti-Hallucination
    print("\n--- Test 8: Anti-Hallucination ---", flush=True)
    resp = await send("Did you successfully download the file I asked about earlier?")
    if any(w in resp.lower() for w in ["don't", "didn't", "no ", "not ", "which file", "haven't", "what file", "i don"]):
        result("Anti-Hallucination", "PASS", "Correctly denied false claim")
    else:
        result("Anti-Hallucination", "FAIL", f"May have hallucinated: {resp[:100]}")

    # Test 9: Scaffold
    print("\n--- Test 9: Scaffold Project ---", flush=True)
    sdir = Path.home() / "Desktop" / "test_scaffold_r14"
    if sdir.exists():
        import shutil; shutil.rmtree(sdir)
    resp = await send(f'Use scaffold_project: project_type python_package, name test_pkg, dest_dir {sdir}')
    if sdir.exists() and len(list(sdir.rglob("*"))) >= 3:
        result("Scaffold Project", "PASS", f"{len(list(sdir.rglob('*')))} files created")
    elif sdir.exists():
        result("Scaffold Project", "PARTIAL", f"Dir exists but {len(list(sdir.rglob('*')))} files")
    else:
        result("Scaffold Project", "FAIL", f"Dir not created. Resp: {resp[:100]}")

    # Test 10: List Tasks
    print("\n--- Test 10: List Tasks ---", flush=True)
    resp = await send("List all tasks")
    if "Test Round 14" in resp or "task" in resp.lower():
        result("List Tasks", "PASS", "Tasks listed")
    else:
        result("List Tasks", "FAIL", f"Resp: {resp[:100]}")

    # Summary
    print("\n" + "="*70)
    total = PASS + FAIL + PARTIAL
    print(f"RESULTS: {PASS} PASS | {FAIL} FAIL | {PARTIAL} PARTIAL | {total} TOTAL")
    print("="*70, flush=True)

    # Cleanup
    Path(Path.home() / "Desktop" / "timmy_test_r14.txt").unlink(missing_ok=True)
    for d in [Path.home() / "Desktop" / "test_scaffold_r14"]:
        if d.exists():
            import shutil; shutil.rmtree(d)

if __name__ == "__main__":
    asyncio.run(run_tests())
