#!/usr/bin/env python3
"""Discord steering adapter for chairman directives."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .runtime_store import RuntimeStore, to_iso


DISCORD_API = "https://discord.com/api/v10"
DEFAULT_CHANNEL_ID = "1475476446947442850"


class ChairmanAdapter:
    def __init__(self, workspace: str | Path, *, openclaw_bin: str = "openclaw", config_path: str | Path | None = None):
        self.workspace = Path(workspace)
        self.openclaw_bin = openclaw_bin
        self.channel_id = os.environ.get("CHAIRMAN_DISCORD_CHANNEL_ID", DEFAULT_CHANNEL_ID)
        self.config_path = Path(config_path or os.environ.get("OPENCLAW_CONFIG", "/home/openclaw/.openclaw/openclaw.json"))
        self.state_path = self.workspace / "state" / "chairman-discord-state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._token = ""

    def _load_token(self) -> str:
        if self._token:
            return self._token
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if token:
            self._token = token
            return token
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        token = str((((payload.get("channels") or {}).get("discord") or {}).get("token")) or "").strip()
        if not token:
            raise RuntimeError("discord token not found")
        self._token = token
        return token

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{DISCORD_API}{path}"
        data = None
        headers = {
            "Authorization": f"Bot {self._load_token()}",
            "User-Agent": "controller-v1/1.0",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"discord api {exc.code}: {body}") from exc

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"last_message_id": "0", "last_polled_at": ""}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"last_message_id": "0", "last_polled_at": ""}

    def _save_state(self, state: dict[str, Any]) -> None:
        state = dict(state)
        state["last_polled_at"] = to_iso()
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _list_messages(self, *, after: str = "0", limit: int = 50) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"limit": str(limit)})
        items = self._request("GET", f"/channels/{self.channel_id}/messages?{query}")
        if not isinstance(items, list):
            return []
        if after and after != "0":
            filtered = [item for item in items if int(str(item.get("id") or "0")) > int(after)]
        else:
            filtered = items
        filtered.sort(key=lambda item: int(str(item.get("id") or "0")))
        return filtered

    def reply(self, message: str) -> None:
        try:
            self._request("POST", f"/channels/{self.channel_id}/messages", {"content": message[:1800]})
        except Exception:
            subprocess.run(
                [self.openclaw_bin, "message", "send", "--channel", "discord", "--target", self.channel_id, "--text", message[:1800]],
                text=True,
                capture_output=True,
                timeout=20,
            )

    def _parse_command(self, content: str) -> tuple[str, dict[str, Any]] | None:
        text = (content or "").strip()
        if not text:
            return None
        patterns = [
            (r"^STEER\s+([0-9a-fA-F-]{8,})\s+(.+)$", "STEER", ("project_id", "text")),
            (r"^PAUSE\s+([0-9a-fA-F-]{8,})$", "PAUSE", ("project_id",)),
            (r"^RESUME\s+([0-9a-fA-F-]{8,})$", "RESUME", ("project_id",)),
            (r"^APPROVE_RISK\s+([0-9a-fA-F-]{8,})\s+(.+)$", "APPROVE_RISK", ("project_id", "text")),
            (r"^REJECT_RISK\s+([0-9a-fA-F-]{8,})\s+(.+)$", "REJECT_RISK", ("project_id", "text")),
            (r"^APPROVE_PROPOSAL\s+([A-Za-z0-9._:-]+)$", "APPROVE_PROPOSAL", ("proposal_id",)),
            (r"^REJECT_PROPOSAL\s+([A-Za-z0-9._:-]+)\s+(.+)$", "REJECT_PROPOSAL", ("proposal_id", "text")),
        ]
        for pattern, command, keys in patterns:
            match = re.match(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            values = match.groups()
            payload = {keys[index]: values[index].strip() for index in range(len(keys))}
            return command.upper(), payload
        return None

    def poll(self, store: RuntimeStore) -> dict[str, Any]:
        state = self._load_state()
        last_message_id = str(state.get("last_message_id") or "0")
        processed = 0
        directives = 0
        approvals = 0
        latest_seen = last_message_id
        for message in self._list_messages(after=last_message_id):
            message_id = str(message.get("id") or "0")
            latest_seen = max(latest_seen, message_id, key=lambda item: int(item))
            author = message.get("author") or {}
            if author.get("bot"):
                continue
            processed += 1
            parsed = self._parse_command(str(message.get("content") or ""))
            if not parsed:
                self.reply(
                    "Uso aceito neste canal: `STEER <project_id> <texto>`, `PAUSE <project_id>`, `RESUME <project_id>`, "
                    "`APPROVE_RISK <project_id> <texto>`, `REJECT_RISK <project_id> <texto>`, "
                    "`APPROVE_PROPOSAL <proposal_id>`, `REJECT_PROPOSAL <proposal_id> <texto>`."
                )
                continue
            command, payload = parsed
            source_ref = f"discord:{message_id}"
            if command in {"APPROVE_PROPOSAL", "REJECT_PROPOSAL"}:
                proposal_id = str(payload.get("proposal_id") or "")
                if proposal_id:
                    status = "approved" if command == "APPROVE_PROPOSAL" else "rejected"
                    store.set_chairman_proposal_status(proposal_id, status)
                    approvals += 1
                    self.reply(f"Chairman proposal `{proposal_id}` marcada como `{status}`.")
                continue
            project_id = str(payload.get("project_id") or "")
            if not project_id:
                continue
            if store.has_event(source_ref):
                continue
            directive_id = f"dir-{message_id}"
            store.add_chairman_directive(
                directive_id=directive_id,
                project_id=project_id,
                directive_type=command,
                payload=payload,
                author_id=str(author.get("id") or ""),
                source_ref=source_ref,
            )
            store.add_event(source_ref=source_ref, event_type="chairman-directive", task_id=project_id, payload={"command": command, **payload})
            directives += 1
            self.reply(f"Directive `{command}` registrada para o projeto `{project_id[:8]}`.")
        self._save_state({"last_message_id": latest_seen})
        return {"processed_messages": processed, "new_directives": directives, "proposal_updates": approvals}
