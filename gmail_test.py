from __future__ import annotations

import json
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

HERE = Path(__file__).resolve().parent
TOKEN = Path.home() / ".config" / "gogcli" / "token.json"
CREDS = Path.home() / ".config" / "gogcli" / "credentials.json"

# If you prefer local files in the script directory, drop token.json/credentials.json next to this script.
if (HERE / "token.json").exists():
    TOKEN = HERE / "token.json"
if (HERE / "credentials.json").exists():
    CREDS = HERE / "credentials.json"

creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)

service = build("gmail", "v1", credentials=creds)

profile = service.users().getProfile(userId="me").execute()
print("Email:", profile.get("emailAddress"))

# List last 5 messages
resp = service.users().messages().list(userId="me", maxResults=5).execute()
msgs = resp.get("messages", [])
print("Last messages:")
for m in msgs:
    msg = service.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From","Subject","Date"]).execute()
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    print("-", headers.get("Date",""), "|", headers.get("From",""), "|", headers.get("Subject",""))
