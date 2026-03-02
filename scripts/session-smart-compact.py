#!/usr/bin/env python3
"""session-smart-compact.py — Intelligent session compaction with pendency extraction.

Analyzes JSONL session files, extracts pending work items, creates MC inbox
tasks for the heartbeat to resolve, updates active-tasks.md, and triggers
gateway compaction.

Usage:
    session-smart-compact.py [--dry-run] [--threshold-mb 3] [--agent main]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from subprocess import run as subrun, PIPE

# ─── Configuration ──────────────────────────────────────────────────────────
AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", "/home/openclaw/.openclaw/agents"))
WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/home/openclaw/.openclaw/workspace"))
MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
ACTIVE_TASKS_FILE = WORKSPACE / "memory" / "active-tasks.md"
DISCORD_CHANNEL = os.environ.get("SENTINEL_DISCORD_CHANNEL", "1476255906894446644")
LOG_TAG = "[smart-compact]"

# Pendency detection patterns
PENDENCY_PATTERNS = [
    # Markdown checklists
    (re.compile(r"[-*]\s*\[\s*[/ ]\s*\]\s+(.+)", re.IGNORECASE), "checklist"),
    # TODO/FIXME markers
    (re.compile(r"(?:TODO|FIXME|HACK|XXX)[:\s]+(.+)", re.IGNORECASE), "dev_marker"),
    # Explicit next steps
    (re.compile(r"(?:próximo[s]?\s*passo[s]?|next\s*step[s]?)[:\s]+(.+)", re.IGNORECASE), "next_step"),
    # TASK_UPDATE with blocked/failed
    (re.compile(r'TASK_UPDATE\s*\{.*"status"\s*:\s*"(blocked|failed|stalled)".*\}', re.IGNORECASE), "task_blocked"),
    # needs_approval / stalled mentions
    (re.compile(r"(?:needs_approval|stalled|pendente|bloqueado)[:\s]+(.+)", re.IGNORECASE), "status_pending"),
]

# Completion patterns (to identify done work)
COMPLETION_PATTERNS = [
    re.compile(r"[-*]\s*\[x\]\s+(.+)", re.IGNORECASE),
    re.compile(r"(?:concluído|completado|done|finished|completed)[:\s]+(.+)", re.IGNORECASE),
    re.compile(r'TASK_UPDATE\s*\{.*"status"\s*:\s*"done".*\}', re.IGNORECASE),
]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"{now_iso()} {LOG_TAG} {msg}")


# ─── Session parsing ────────────────────────────────────────────────────────
def parse_session(session_path: Path) -> dict:
    """Parse a JSONL session file and extract metadata + messages."""
    header = None
    messages = []
    compaction_summaries = []
    total_lines = 0

    with open(session_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            total_lines += 1
            try:
                entry = json.loads(line.rstrip())
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            if entry_type == "session":
                header = entry
            elif entry_type == "message":
                messages.append(entry)
            elif entry_type == "compaction":
                compaction_summaries.append(entry)

    size_mb = session_path.stat().st_size / (1024 * 1024)

    return {
        "path": session_path,
        "id": header.get("id", session_path.stem) if header else session_path.stem,
        "header": header,
        "messages": messages,
        "compaction_summaries": compaction_summaries,
        "total_lines": total_lines,
        "size_mb": round(size_mb, 1),
    }


# ─── Pendency extraction ────────────────────────────────────────────────────
def extract_pendencies(session_data: dict) -> list[dict]:
    """Extract pending work items from session messages."""
    pendencies = []
    seen_texts = set()

    # Focus on the last 200 messages (most relevant context)
    recent_messages = session_data["messages"][-200:]

    for msg in recent_messages:
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue

        for pattern, ptype in PENDENCY_PATTERNS:
            for match in pattern.finditer(content):
                text = match.group(1).strip()[:200]
                # Deduplicate
                norm = text.lower().strip()
                if norm in seen_texts or len(norm) < 10:
                    continue
                seen_texts.add(norm)
                pendencies.append({
                    "text": text,
                    "type": ptype,
                    "source": "session",
                    "session_id": session_data["id"],
                })

    # Also extract from last compaction summary if available
    if session_data["compaction_summaries"]:
        last_summary = session_data["compaction_summaries"][-1]
        summary_text = last_summary.get("summary", last_summary.get("content", ""))
        if isinstance(summary_text, str):
            for pattern, ptype in PENDENCY_PATTERNS:
                for match in pattern.finditer(summary_text):
                    text = match.group(1).strip()[:200]
                    norm = text.lower().strip()
                    if norm not in seen_texts and len(norm) >= 10:
                        seen_texts.add(norm)
                        pendencies.append({
                            "text": text,
                            "type": ptype,
                            "source": "compaction_summary",
                            "session_id": session_data["id"],
                        })

    return pendencies


def extract_completions(session_data: dict) -> list[str]:
    """Extract completed work items from session messages."""
    completions = []
    seen = set()

    for msg in session_data["messages"][-200:]:
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue
        for pattern in COMPLETION_PATTERNS:
            for match in pattern.finditer(content):
                text = match.group(1).strip()[:200]
                norm = text.lower().strip()
                if norm not in seen and len(norm) >= 10:
                    seen.add(norm)
                    completions.append(text)

    return completions


def generate_state_summary(session_data: dict, pendencies: list, completions: list) -> str:
    """Generate a compact state summary for the compacted session."""
    parts = [f"Session {session_data['id']} — {session_data['size_mb']}MB, "
             f"{len(session_data['messages'])} msgs, "
             f"{len(session_data['compaction_summaries'])} prior compactions"]

    if completions:
        parts.append(f"\nCompleted ({len(completions)}):")
        for c in completions[:10]:
            parts.append(f"  - {c}")

    if pendencies:
        parts.append(f"\nPending ({len(pendencies)}):")
        for p in pendencies[:10]:
            parts.append(f"  - [{p['type']}] {p['text']}")

    return "\n".join(parts)


# ─── MC Integration ─────────────────────────────────────────────────────────
def create_mc_task(title: str, description: str, session_id: str, priority: str = "medium",
                   dry_run: bool = False) -> bool:
    """Create a task in MC inbox for the heartbeat to pick up."""
    # Embed metadata in description (MC API rejects unknown custom fields)
    full_desc = f"{description}\n\n---\n_source: auto-compact | session: {session_id[:8]}_"

    if dry_run:
        log(f"  [DRY-RUN] Would create MC task: '{title}'")
        return True

    try:
        result = subrun(
            ["bash", str(MC_CLIENT), "create-task",
             title, full_desc, "luna", priority, "inbox", ""],
            capture_output=True, text=True, timeout=30,
            env={**os.environ,
                 "MC_AUTH_TOKEN": os.environ.get("MC_API_TOKEN", os.environ.get("MC_AUTH_TOKEN", ""))}
        )
        if result.returncode == 0:
            log(f"  MC task created: '{title}'")
            return True
        else:
            log(f"  MC task creation failed: {result.stderr.strip()[:100]}")
            return False
    except Exception as e:
        log(f"  MC task creation error: {e}")
        return False


# ─── Active Tasks Update ────────────────────────────────────────────────────
def update_active_tasks(pendencies: list, completions: list, session_id: str,
                        dry_run: bool = False) -> None:
    """Update active-tasks.md with extracted pendencies and completions."""
    if not ACTIVE_TASKS_FILE.exists():
        log(f"  active-tasks.md not found at {ACTIVE_TASKS_FILE}")
        return

    content = ACTIVE_TASKS_FILE.read_text(encoding="utf-8")

    # Add auto-compact timestamp
    timestamp = now_iso()
    compact_marker = f"\n\n## Auto-Compact ({timestamp})\n"
    compact_marker += f"*Session {session_id[:8]} analyzed*\n"

    # Add completions to Recently Completed
    if completions:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        completion_lines = "\n".join(f"- {date_str}: [auto-compact] {c}" for c in completions[:5])

        if "## Recently Completed" in content:
            content = content.replace(
                "## Recently Completed",
                f"## Recently Completed\n{completion_lines}"
            )
        else:
            content += f"\n## Recently Completed\n{completion_lines}\n"

    # Add pendencies to In Progress or Blocked
    if pendencies:
        pending_lines = []
        blocked_lines = []
        for p in pendencies[:8]:
            line = f"- **[auto-compact]** {p['text']}"
            if p["type"] in ("task_blocked", "status_pending"):
                blocked_lines.append(line)
            else:
                pending_lines.append(line)

        if pending_lines and "## In Progress" in content:
            content = content.replace(
                "## In Progress",
                f"## In Progress\n" + "\n".join(pending_lines)
            )
        if blocked_lines and "## Blocked" in content:
            content = content.replace(
                "## Blocked\n- None.",
                f"## Blocked\n" + "\n".join(blocked_lines)
            )

    if dry_run:
        log(f"  [DRY-RUN] Would update active-tasks.md ({len(pendencies)} pending, {len(completions)} complete)")
        return

    ACTIVE_TASKS_FILE.write_text(content, encoding="utf-8")
    log(f"  Updated active-tasks.md")


# ─── Compaction ──────────────────────────────────────────────────────────────
def trigger_compaction(session_data: dict, state_summary: str, dry_run: bool = False) -> bool:
    """Trigger session compaction — truncate JSONL keeping essential context."""
    session_path = session_data["path"]

    if dry_run:
        log(f"  [DRY-RUN] Would compact {session_path.name} ({session_data['size_mb']}MB → estimated <1MB)")
        return True

    # Strategy: keep header + last compaction summary + last 50 messages
    # This preserves enough context for continuity while reducing file size dramatically
    try:
        header_line = None
        recent_messages = session_data["messages"][-50:]
        last_compaction = session_data["compaction_summaries"][-1:] if session_data["compaction_summaries"] else []

        with open(session_path, encoding="utf-8", errors="replace") as f:
            first_line = f.readline()
            try:
                first = json.loads(first_line)
                if first.get("type") == "session":
                    header_line = first_line.rstrip()
            except json.JSONDecodeError:
                pass

        # Build new compaction entry
        new_compaction = {
            "type": "compaction",
            "timestamp": now_iso(),
            "summary": state_summary,
            "original_size_mb": session_data["size_mb"],
            "original_lines": session_data["total_lines"],
            "original_messages": len(session_data["messages"]),
            "pendencies_extracted": True,
            "compacted_by": "session-smart-compact.py",
        }

        # Write compacted session
        backup_path = session_path.parent / "archived" / f"{session_path.stem}.pre-compact.jsonl"
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        # Move original to backup
        import shutil
        shutil.copy2(str(session_path), str(backup_path))
        log(f"  Backup: {backup_path.name}")

        # Write new compacted file
        with open(session_path, "w", encoding="utf-8") as f:
            if header_line:
                f.write(header_line + "\n")
            # Write previous compaction summaries (keep last 3)
            for comp in last_compaction:
                f.write(json.dumps(comp, ensure_ascii=False) + "\n")
            # Write new compaction entry
            f.write(json.dumps(new_compaction, ensure_ascii=False) + "\n")
            # Write recent messages
            for msg in recent_messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        new_size = session_path.stat().st_size / (1024 * 1024)
        log(f"  Compacted: {session_data['size_mb']}MB → {new_size:.1f}MB "
            f"({session_data['total_lines']} → {2 + len(last_compaction) + len(recent_messages)} lines)")
        return True

    except Exception as e:
        log(f"  Compaction failed: {e}")
        return False


# ─── Main orchestrator ──────────────────────────────────────────────────────
def process_agent(agent_name: str, threshold_mb: float, dry_run: bool,
                  skip_compact: bool) -> dict:
    """Process all sessions for an agent."""
    sessions_dir = AGENTS_DIR / agent_name / "sessions"
    if not sessions_dir.exists():
        return {"agent": agent_name, "skipped": True}

    results = {
        "agent": agent_name,
        "sessions_scanned": 0,
        "sessions_compacted": 0,
        "pendencies_found": 0,
        "mc_tasks_created": 0,
        "errors": 0,
    }

    # Find session files exceeding threshold
    for jsonl_file in sorted(sessions_dir.glob("*.jsonl")):
        size_mb = jsonl_file.stat().st_size / (1024 * 1024)
        results["sessions_scanned"] += 1

        if size_mb < threshold_mb:
            continue

        log(f"\nProcessing {agent_name}/{jsonl_file.name} ({size_mb:.1f}MB)")

        # Parse session
        try:
            session_data = parse_session(jsonl_file)
        except Exception as e:
            log(f"  Parse error: {e}")
            results["errors"] += 1
            continue

        # Extract pendencies and completions
        pendencies = extract_pendencies(session_data)
        completions = extract_completions(session_data)
        results["pendencies_found"] += len(pendencies)

        log(f"  Found: {len(pendencies)} pendencies, {len(completions)} completions, "
            f"{len(session_data['compaction_summaries'])} prior compactions")

        # Generate state summary
        summary = generate_state_summary(session_data, pendencies, completions)

        # Create MC tasks for significant pendencies
        for p in pendencies[:5]:  # Cap at 5 tasks per session
            title = f"[auto-compact] {p['text'][:80]}"
            desc = (f"Extraído da sessão {session_data['id'][:8]} ({agent_name}) durante auto-compactação.\n"
                    f"Tipo: {p['type']}, Fonte: {p['source']}")
            if create_mc_task(title, desc, session_data["id"], dry_run=dry_run):
                results["mc_tasks_created"] += 1

        # Update active-tasks.md
        update_active_tasks(pendencies, completions, session_data["id"], dry_run=dry_run)

        # Trigger compaction
        if not skip_compact:
            if trigger_compaction(session_data, summary, dry_run=dry_run):
                results["sessions_compacted"] += 1
        else:
            log(f"  Skipping compaction (--skip-compact)")

    return results


def main():
    parser = argparse.ArgumentParser(description="Smart session auto-compaction")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without modifying anything")
    parser.add_argument("--threshold-mb", type=float, default=3.0, help="Min session size to process (MB)")
    parser.add_argument("--agent", default="", help="Process specific agent (empty = all)")
    parser.add_argument("--skip-compact", action="store_true", help="Extract pendencies without compacting")
    parser.add_argument("--agents-dir", default="", help="Override agents directory")
    parser.add_argument("--workspace-dir", default="", help="Override workspace directory")
    args = parser.parse_args()

    global AGENTS_DIR, WORKSPACE, MC_CLIENT, ACTIVE_TASKS_FILE
    if args.agents_dir:
        AGENTS_DIR = Path(args.agents_dir)
    if args.workspace_dir:
        WORKSPACE = Path(args.workspace_dir)
        MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
        ACTIVE_TASKS_FILE = WORKSPACE / "memory" / "active-tasks.md"

    log(f"Starting smart compaction (threshold={args.threshold_mb}MB, dry_run={args.dry_run})")

    # Determine which agents to process
    if args.agent:
        agents = [args.agent]
    else:
        agents = [d.name for d in AGENTS_DIR.iterdir()
                  if d.is_dir() and (d / "sessions").exists()]

    log(f"Agents to process: {agents}")

    # Load MC token
    if not os.environ.get("MC_API_TOKEN") and not os.environ.get("MC_AUTH_TOKEN"):
        bashrc = Path.home() / ".bashrc"
        if bashrc.exists():
            for line in bashrc.read_text().splitlines():
                if "MC_API_TOKEN" in line and "export" in line:
                    # Extract token value
                    match = re.search(r'MC_API_TOKEN="([^"]+)"', line)
                    if match:
                        os.environ["MC_API_TOKEN"] = match.group(1)
                        os.environ["MC_AUTH_TOKEN"] = match.group(1)
                        break

    # Process each agent
    all_results = []
    for agent_name in sorted(agents):
        result = process_agent(agent_name, args.threshold_mb, args.dry_run, args.skip_compact)
        all_results.append(result)

    # Summary
    total_scanned = sum(r.get("sessions_scanned", 0) for r in all_results)
    total_compacted = sum(r.get("sessions_compacted", 0) for r in all_results)
    total_pendencies = sum(r.get("pendencies_found", 0) for r in all_results)
    total_mc = sum(r.get("mc_tasks_created", 0) for r in all_results)
    total_errors = sum(r.get("errors", 0) for r in all_results)

    log(f"\n{'='*60}")
    log(f"SUMMARY")
    log(f"  Sessions scanned: {total_scanned}")
    log(f"  Sessions compacted: {total_compacted}")
    log(f"  Pendencies found: {total_pendencies}")
    log(f"  MC tasks created: {total_mc}")
    log(f"  Errors: {total_errors}")
    log(f"{'='*60}")

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
