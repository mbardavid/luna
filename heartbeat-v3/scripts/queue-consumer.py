#!/usr/bin/env python3
"""
queue-consumer.py — Queue consumption module for Luna.

Architecture:
  - Reads queue/pending/ → moves to queue/active/ → processes → moves to queue/done/ or queue/failed/
  - Atomic file operations (os.replace, not copy+delete)
  - Idempotent: if called 2x on the same item, second call is a no-op
  - Generates sessions_spawn parameters ready for Luna to execute

Usage:
  # As a standalone script (Luna calls this):
  python3 queue-consumer.py                    # Process all pending items
  python3 queue-consumer.py --peek             # List pending items without processing
  python3 queue-consumer.py --one              # Process exactly one item
  python3 queue-consumer.py --dry-run          # Simulate without moving files

  # As a module (Luna imports this):
  from queue_consumer import QueueConsumer
  consumer = QueueConsumer("/path/to/queue")
  items = consumer.peek()
  result = consumer.consume_one()
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class QueueConsumer:
    """Filesystem-based queue consumer with atomic operations."""

    def __init__(self, queue_dir: str, dry_run: bool = False):
        self.queue_dir = Path(queue_dir)
        self.pending = self.queue_dir / "pending"
        self.active = self.queue_dir / "active"
        self.done = self.queue_dir / "done"
        self.failed = self.queue_dir / "failed"
        self.escalated = self.queue_dir / "escalated"
        self.dry_run = dry_run

        # Ensure dirs exist
        for d in [self.pending, self.active, self.done, self.failed, self.escalated]:
            d.mkdir(parents=True, exist_ok=True)

    def peek(self) -> list[dict]:
        """List all pending queue items sorted by creation time (oldest first)."""
        items = []
        for f in sorted(self.pending.glob("*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                data["_filename"] = f.name
                data["_path"] = str(f)
                data["_age_seconds"] = int(time.time() - f.stat().st_mtime)
                items.append(data)
            except (json.JSONDecodeError, OSError) as e:
                # Skip corrupt files
                items.append({
                    "_filename": f.name,
                    "_path": str(f),
                    "_error": str(e),
                })
        return items

    def count_active(self) -> int:
        """Count items currently being processed (in active/)."""
        return len(list(self.active.glob("*.json")))

    def claim(self, filename: str) -> Optional[dict]:
        """
        Atomically claim a pending item by moving it to active/.

        Returns the parsed item data, or None if already claimed/missing.
        This is the idempotency guarantee: os.replace is atomic on Linux,
        so only one consumer can successfully claim an item.
        """
        src = self.pending / filename
        dst = self.active / filename

        if not src.exists():
            # Already claimed or doesn't exist → no-op (idempotent)
            # Check if it's in active/ already
            if dst.exists():
                try:
                    with open(dst) as f:
                        return json.load(f)
                except Exception:
                    return None
            return None

        if self.dry_run:
            try:
                with open(src) as f:
                    data = json.load(f)
                data["_dry_run"] = True
                return data
            except Exception:
                return None

        try:
            # Atomic move: pending → active
            os.replace(str(src), str(dst))
            with open(dst) as f:
                return json.load(f)
        except FileNotFoundError:
            # Race condition: another consumer claimed it first → no-op
            return None
        except Exception as e:
            # Unexpected error: try to read from wherever it ended up
            if dst.exists():
                try:
                    with open(dst) as f:
                        return json.load(f)
                except Exception:
                    pass
            return None

    def complete(self, filename: str, success: bool = True, result: dict = None) -> bool:
        """
        Move a claimed item from active/ to done/ or failed/.

        Appends result metadata to the item before moving.
        """
        src = self.active / filename
        if not src.exists():
            return False

        if self.dry_run:
            return True

        # Read, augment with result, write back
        try:
            with open(src) as f:
                data = json.load(f)

            data["completed_at"] = datetime.now(timezone.utc).isoformat()
            data["completed_by"] = "queue-consumer"
            data["success"] = success
            if result:
                data["result"] = result

            # Atomic write back to same location first
            with open(src, "w") as f:
                json.dump(data, f, indent=2)

            # Move to final location
            dest_dir = self.done if success else self.failed
            os.replace(str(src), str(dest_dir / filename))
            return True
        except Exception:
            return False

    def consume_one(self) -> Optional[dict]:
        """
        Consume the oldest pending item.

        Returns a dict with spawn_params ready for Luna to execute,
        or None if no items are pending.
        """
        items = self.peek()
        valid_items = [i for i in items if "_error" not in i]

        if not valid_items:
            return None

        item = valid_items[0]
        filename = item["_filename"]

        # Claim it
        data = self.claim(filename)
        if data is None:
            return None

        # Build spawn parameters based on item type
        item_type = data.get("type", "dispatch")
        task_id = data.get("task_id", "")
        title = data.get("title", "(sem título)")
        agent = data.get("agent", "luan")

        if item_type == "dispatch":
            return self._build_dispatch_result(data, filename)
        elif item_type == "respawn":
            return self._build_respawn_result(data, filename)
        elif item_type == "alert":
            return self._build_alert_result(data, filename)
        else:
            return {
                "action": "unknown",
                "queue_file": filename,
                "data": data,
            }

    def _build_dispatch_result(self, data: dict, filename: str) -> dict:
        """Build sessions_spawn parameters for a dispatch item."""
        task_id = data.get("task_id", "")
        title = data.get("title", "(sem título)")
        agent = data.get("agent", "luan")
        spawn_params = data.get("spawn_params", {})
        context = data.get("context", {})
        description = spawn_params.get("description", context.get("description", ""))
        priority = data.get("priority", "medium")

        message = f"""📋 Heartbeat V3 dispatch — execute a task abaixo.

## Task
**Título:** {title}
**MC Task ID:** {task_id}
**Prioridade:** {priority}
**Agente designado:** {agent}

## Descrição
{description if description else '(sem descrição)'}

## Instruções
1. Executar a task conforme descrição
2. Linkar session_key ao MC task via mc-client.sh update-task {task_id} --fields '{{"mc_session_key":"<SESSION_KEY>"}}'
3. Se a task não tiver descrição suficiente, consultar MC para detalhes completos
4. Ao concluir, marcar task como done no MC

## Contexto
- Eligible tasks no inbox: {context.get('eligible_count', '?')}
- Tasks in_progress: {context.get('in_progress_count', '?')}
- Dispatch source: heartbeat-v3 queue"""

        return {
            "action": "spawn",
            "queue_file": filename,
            "task_id": task_id,
            "agent": agent,
            "spawn_params": {
                "agent": agent,
                "message": message,
                "label": f"hb-dispatch-{task_id[:8]}",
                "mc_task_id": task_id,
            },
        }

    def _build_respawn_result(self, data: dict, filename: str) -> dict:
        """Build sessions_spawn parameters for a respawn item."""
        task_id = data.get("task_id", "")
        title = data.get("title", "(sem título)")
        agent = data.get("agent", "luan")
        context = data.get("context", {})
        failure_type = context.get("failure_type", "UNKNOWN")
        retry_count = context.get("retry_count", 0)
        adjustments = context.get("adjustments", "nenhum")
        description = context.get("description", "")

        message = f"""🔄 Heartbeat V3 failure respawn — re-executar task que falhou.

## Task
**Título:** {title}
**MC Task ID:** {task_id}
**Retry:** #{retry_count + 1}

## Descrição
{description if description else '(sem descrição)'}

## Análise de Falha
**Tipo de erro:** {failure_type}
**Ajustes aplicados:** {adjustments}

## Instruções
1. Re-executar a task com os ajustes acima em mente
2. Linkar novo session_key ao MC task
3. Se retry falhar novamente, mover task para `review`
4. Atualizar mc_retry_count no MC

## Contexto
- Esta é uma re-execução automática após falha detectada
- Dispatch source: heartbeat-v3 queue respawn"""

        return {
            "action": "respawn",
            "queue_file": filename,
            "task_id": task_id,
            "agent": agent,
            "spawn_params": {
                "agent": agent,
                "message": message,
                "label": f"hb-respawn-{task_id[:8]}",
                "mc_task_id": task_id,
            },
        }

    def _build_alert_result(self, data: dict, filename: str) -> dict:
        """Build alert notification result."""
        return {
            "action": "alert",
            "queue_file": filename,
            "task_id": data.get("task_id", ""),
            "message": data.get("context", {}).get("message", "Alert from heartbeat-v3"),
            "data": data,
        }

    def gc_completed(self, max_age_hours: int = 24) -> int:
        """Garbage collect old done/ and failed/ items."""
        count = 0
        max_age_seconds = max_age_hours * 3600
        now = time.time()

        for directory in [self.done, self.failed]:
            for f in directory.glob("*.json"):
                try:
                    age = now - f.stat().st_mtime
                    if age > max_age_seconds:
                        if not self.dry_run:
                            f.unlink()
                        count += 1
                except Exception:
                    pass

        return count


def main():
    """CLI entry point for queue-consumer."""
    import argparse
    parser = argparse.ArgumentParser(description="Heartbeat V3 Queue Consumer")
    parser.add_argument("--queue-dir", default=None, help="Queue directory path")
    parser.add_argument("--config", default=None, help="V3 config file path")
    parser.add_argument("--peek", action="store_true", help="List pending items without processing")
    parser.add_argument("--one", action="store_true", help="Process exactly one item")
    parser.add_argument("--gc", action="store_true", help="Garbage collect completed items")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without side-effects")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Determine queue directory
    queue_dir = args.queue_dir
    if not queue_dir:
        config_path = args.config or str(
            Path(__file__).resolve().parent.parent / "config" / "v3-config.json"
        )
        try:
            with open(config_path) as f:
                config = json.load(f)
            queue_dir = config.get("queue_dir")
        except Exception:
            pass
    if not queue_dir:
        queue_dir = str(Path(__file__).resolve().parent.parent / "queue")

    consumer = QueueConsumer(queue_dir, dry_run=args.dry_run)

    if args.peek:
        items = consumer.peek()
        if args.json:
            print(json.dumps(items, indent=2))
        else:
            if not items:
                print("No pending items.")
            else:
                for item in items:
                    age = item.get("_age_seconds", 0)
                    age_min = age // 60
                    err = item.get("_error")
                    if err:
                        print(f"  ❌ {item['_filename']} — ERROR: {err}")
                    else:
                        print(f"  📋 {item['_filename']} — {item.get('type', '?')} — "
                              f"{item.get('title', '?')} — {age_min}min old")
        return

    if args.gc:
        count = consumer.gc_completed()
        print(f"Cleaned {count} completed items.")
        return

    # Consume
    if args.one:
        result = consumer.consume_one()
        if result is None:
            if args.json:
                print(json.dumps({"status": "empty"}))
            else:
                print("No pending items to consume.")
            return

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Consumed: {result.get('queue_file', '?')}")
            print(f"  Action: {result.get('action', '?')}")
            print(f"  Task: {result.get('task_id', '?')}")
            print(f"  Agent: {result.get('agent', '?')}")
            if "spawn_params" in result:
                print(f"  Spawn label: {result['spawn_params'].get('label', '?')}")
    else:
        # Process all pending
        processed = 0
        while True:
            result = consumer.consume_one()
            if result is None:
                break
            processed += 1
            if args.json:
                print(json.dumps(result))
            else:
                print(f"Consumed: {result.get('queue_file', '?')} → {result.get('action', '?')}")

        if processed == 0:
            if args.json:
                print(json.dumps({"status": "empty", "processed": 0}))
            else:
                print("No pending items to consume.")
        else:
            if not args.json:
                print(f"\nProcessed {processed} item(s).")


if __name__ == "__main__":
    main()
