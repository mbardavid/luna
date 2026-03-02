#!/usr/bin/env python3
import argparse
import json
import os
import random
import string
from datetime import datetime, timezone
from pathlib import Path
from subprocess import run


def slug(value: str) -> str:
    safe = [c.lower() if c.isalnum() else "-" for c in value.strip()]
    out = "".join(safe).strip("-")
    return (out[:80] or "task")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def loop_id() -> str:
    token = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(16))
    return f"loop_{token}"


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def run_orchestrator(args):
    lid = args.loop_id or loop_id()
    root = Path(args.workspace)
    state_file = root / "memory" / "orchestration-state.json"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    task_spec = {
        "taskSpecVersion": "1.1",
        "handoffId": f"hs_{slug(args.title)}_{lid[:10]}",
        "correlationId": f"corr_{lid}",
        "loop_id": lid,
        "createdAt": now_iso(),
        "proposed_by": args.proposed_by,
        "source": {
            "agentId": "luna",
            "sessionId": "agent:main:orchestrator",
        },
        "target": {
            "agentId": args.agent,
            "capability": args.capability,
        },
        "routing": {
            "strategy": "capability",
            "routeKey": args.route_key,
            "fallbackAgentId": args.fallback_agent,
        },
        "mode": "dev",
        "risk_profile": args.risk_profile,
        "review_depth": args.review_depth,
        "review_feedback_required": args.review_feedback_required,
        "auto_approve_window": args.auto_approve_window,
        "review_reason": args.review_reason,
        "intent": {
            "operation": args.operation,
            "inputSchemaRef": args.input_schema_ref,
            "summary": args.summary,
            "input": {
                "objective": args.objective,
                "notes": args.notes,
            },
        },
        "acceptance": {
            "doneWhen": ["Resposta valida com artefatos esperados", "Métricas de validação registradas"],
            "expectedArtifacts": args.artifacts.split(",") if args.artifacts else [],
        },
        "safety": {
            "e2eActor": "authorized-harness",
            "allowExternalSideEffects": False,
            "requiresHumanApproval": args.requires_human_approval,
        },
        "rollback": {
            "required": True,
            "planRef": "docs/agentic-loop-contract.md",
            "trigger": "Falha de revisão ou risco não atendido",
        },
        "audit": {
            "requestId": f"req_{slug(args.title)[:24]}_{lid[:8]}",
            "idempotencyKey": f"idem_{lid}",
            "traceId": f"trace_{lid}",
            "delegation": {
                "policyRef": "config/cto-risk-policy.json",
                "envelopeHash": args.envelope_hash or "sha256:placeholder",
                "riskClassification": args.risk_profile,
                "authorizationRef": args.authorization_ref or None,
                "decision": "allowed" if args.auto_authorize else "pending",
                "validatedAt": now_iso(),
                "recordedBy": args.proposed_by,
            },
        },
    }

    orchestrator_payload = {
        "loop_id": lid,
        "state": "propose",
        "cycle": 1,
        "agent": args.agent,
        "max_cycles": args.review_depth,
        "task_spec": task_spec,
        "createdAt": now_iso(),
        "events": [
            {
                "at": now_iso(),
                "step": "propose",
                "actor": args.proposed_by,
                "status": "queued",
                "message": "Luan/loop start created",
            }
        ],
    }

    out_file = outdir / f"{lid}.json"
    save_json(out_file, orchestrator_payload)
    print(json.dumps(orchestrator_payload, ensure_ascii=False, indent=2))

    # Optional bootstrap: spawn through mc-spawn if tool exists
    if args.auto_spawn:
        mc_spawn = os.path.join(args.scripts_dir, "mc-spawn.sh")
        if os.path.exists(mc_spawn) and os.access(mc_spawn, os.X_OK):
            spawn_task = f"{args.objective}\n\nTaskSpec: {out_file}"
            proc = run(
                [
                    mc_spawn,
                    "--agent",
                    args.agent,
                    "--title",
                    args.title,
                    "--task",
                    spawn_task,
                    "--timeout",
                    str(args.timeout),
                    "--priority",
                    args.priority,
                    "--json",
                    "--risk-profile",
                    args.risk_profile,
                    "--loop-id",
                    lid,
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                print(f"[agent-loop-orchestrator] mc-spawn failed: {proc.stderr.strip()}")

    # Keep state updated with a lightweight handoff entry
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {
                "schemaVersion": "1.1",
                "updatedAt": now_iso(),
                "activeHandoffs": {},
                "routingTable": {},
                "promotion": {},
                "rollback": {
                    "required": True,
                    "planRef": "docs/agentic-loop-contract.md",
                    "lastRollbackAt": None,
                    "lastRollbackReason": None,
                },
                "delegationAuditLog": [],
            }
    else:
        state = {
            "schemaVersion": "1.1",
            "updatedAt": now_iso(),
            "activeHandoffs": {},
            "routingTable": {},
            "promotion": {
                "currentStage": "dev",
                "candidateStage": None,
                "gateStatus": "pending",
                "lastGatePassed": None,
                "approvedBy": None,
                "approvedAt": None,
            },
            "rollback": {
                "required": True,
                "planRef": "docs/agentic-loop-contract.md",
                "lastRollbackAt": None,
                "lastRollbackReason": None,
            },
            "delegationAuditLog": [],
        }

    handoffs = state.setdefault("activeHandoffs", {})
    handoffs[lid] = {
        "status": "proposed",
        "mode": "dev",
        "sourceAgent": "luna",
        "targetAgent": args.agent,
        "routeKey": args.route_key,
        "lastUpdatedAt": now_iso(),
        "loop_id": lid,
        "review_state": "proposed",
        "review_cycle": 1,
        "proposed_by": args.proposed_by,
        "review_reason": args.review_reason or None,
    }
    state["updatedAt"] = now_iso()
    save_json(state_file, state)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--scripts-dir", default="../scripts")
    parser.add_argument("--loop-id", default="")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--artifacts", default="")
    parser.add_argument("--route-key", default="luna.dev.v1")
    parser.add_argument("--capability", default="general")
    parser.add_argument("--fallback-agent", default="")
    parser.add_argument("--risk-profile", default="medium", choices=["low", "medium", "high", "critical"])
    parser.add_argument("--review-depth", type=int, default=2)
    parser.add_argument("--review-feedback-required", action="store_true")
    parser.add_argument("--auto-approve-window", type=int, default=600)
    parser.add_argument("--requires-human-approval", action="store_true")
    parser.add_argument("--review-reason", default="")
    parser.add_argument("--proposed-by", default="luna")
    parser.add_argument("--input-schema-ref", default="docs/agent-orchestration-a2a.md")
    parser.add_argument("--outdir", default="/tmp/openclaw-loop")
    parser.add_argument("--authorization-ref", default="")
    parser.add_argument("--envelope-hash", default="")
    parser.add_argument("--auto-authorize", action="store_true")
    parser.add_argument("--auto-spawn", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--priority", default="medium")
    args = parser.parse_args()

    args.scripts_dir = os.path.expanduser(args.scripts_dir)
    run_orchestrator(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
