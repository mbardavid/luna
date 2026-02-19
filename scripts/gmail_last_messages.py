#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=10)
    p.add_argument("--query", type=str, default="")
    p.add_argument("--token", type=Path, default=Path.home() / ".config" / "gogcli" / "token.json")
    args = p.parse_args()

    creds = Credentials.from_authorized_user_file(str(args.token), SCOPES)
    svc = build("gmail", "v1", credentials=creds)

    profile = svc.users().getProfile(userId="me").execute()
    print(f"ACCOUNT: {profile.get('emailAddress')}")

    params = {"userId": "me", "maxResults": args.max}
    if args.query:
        params["q"] = args.query

    resp = svc.users().messages().list(**params).execute()
    msgs = resp.get("messages", [])
    print(f"COUNT: {len(msgs)}")

    for m in msgs:
        msg = svc.users().messages().get(
            userId="me",
            id=m["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        print(f"- {headers.get('Date', '')} | {headers.get('From', '')} | {headers.get('Subject', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
