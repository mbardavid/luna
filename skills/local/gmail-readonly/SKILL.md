---
name: gmail-readonly
description: Read Gmail messages in read-only mode for account triage and summaries. Use when the user asks to check inbox, list recent emails, or summarize unread messages. Do not use for sending, deleting, labeling, archiving, or any write action.
---

Use `/home/openclaw/.openclaw/workspace/scripts/gmail_last_messages.py`.

## Procedure
1. Confirm readonly intent if request is ambiguous.
2. Run the script with desired `--max` and optional `--query`.
3. Return a short summary first, then bullet list (date, from, subject).
4. Flag urgent items (security, billing, deadlines).

## Negative triggers
- Do not use when user asks to send/reply/delete/archive emails.
- Do not use when credentials are missing; ask for OAuth setup instead.

## Commands
- Recent messages: `python3 scripts/gmail_last_messages.py --max 10`
- Unread first: `python3 scripts/gmail_last_messages.py --query "is:unread" --max 10`
