"""Messaging adapters: Slack Web API, Gmail API, Microsoft Graph (Outlook mail and Teams chat).

Each adapter speaks the provider's documented HTTP API through an injectable `http` function,
so tests can run against recorded-shape fakes and real use can run against the real API.
Endpoints, scopes and behaviours were checked against official docs on 2026-10-28
(see docs/team-integrations.md for the sources). None of these adapters has been run
against a live account yet: treat them as "implemented, tested against fakes".

How each adapter settles an ambiguous send (a timeout after the request left):

| provider | marker sent with the message | how Lucid looks for it |
|---|---|---|
| Slack (bot token) | message metadata `lucid_send` with the op id | conversations.history/replies with include_all_metadata |
| Gmail | Message-ID header built from the op id | messages.list q=rfc822msgid:<id> |
| Outlook | x-lucid-op-id internet header on a draft created first | the draft's state, then Sent Items headers |
| Teams (delegated) | none available | recent messages from the same user with identical text (a heuristic) |
"""
from __future__ import annotations

import base64
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any, Callable

from .sync import OutcomeUnknown, TransientError

Http = Callable[..., "Response"]


class PermanentSendError(Exception):
    """Not retryable as-is: bad recipient, forbidden, archived channel."""


class AuthExpired(PermanentSendError):
    """The credential was revoked or expired; the account must be reconnected."""


class RateLimited(TransientError):
    def __init__(self, msg: str, retry_after: float):
        super().__init__(msg)
        self.retry_after = retry_after


@dataclass
class Response:
    status: int
    body: Any = None
    headers: dict = field(default_factory=dict)


@dataclass
class SendResult:
    provider_ref: str
    receipt: dict


def urllib_http(method: str, url: str, *, token: str, json_body: Any = None, params: dict | None = None,
                timeout: float = 30) -> Response:
    """Real HTTP transport. A timeout or dropped connection after sending is reported as OutcomeUnknown."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(json_body).encode() if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - fixed https API hosts only
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read()
            return Response(r.status, json.loads(raw) if raw else None, dict(r.headers))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = {"raw": raw[:500].decode(errors="replace")}
        return Response(e.code, body, dict(e.headers or {}))
    except (socket.timeout, TimeoutError, ConnectionResetError) as e:
        if method != "GET":  # the request may have been applied, even with no body (e.g. Graph /send)
            raise OutcomeUnknown(f"{method} {url}: {e}") from e
        raise TransientError(str(e)) from e
    except urllib.error.URLError as e:
        raise TransientError(str(e.reason)) from e


def _retry_after(resp: Response, default: float = 30.0) -> float:
    for k, v in (resp.headers or {}).items():
        if k.lower() == "retry-after":
            try:
                return float(v)
            except ValueError:
                return default
    return default


class Provider:
    name = "base"
    kind = "chat"
    capabilities: dict[str, Any] = {}

    def __init__(self, http: Http, token: Callable[[dict], str]):
        self.http = http
        self.token = token

    def _call(self, account: dict, method: str, url: str, **kw) -> Response:
        return self.http(method, url, token=self.token(account), **kw)

    def validate_recipient(self, r: str) -> str | None:
        return None

    def can_reconcile(self, account: dict) -> bool:
        return True

    def send(self, account: dict, msg: dict) -> SendResult:
        raise NotImplementedError

    def lookup(self, account: dict, msg: dict) -> SendResult | None:
        return None

    def create_draft(self, account: dict, msg: dict) -> SendResult:
        raise PermanentSendError(f"{self.name} has no draft API; the draft stays in Lucid")

    def edit(self, account: dict, msg: dict, new_body: str) -> dict:
        raise PermanentSendError(f"{self.name} does not support editing sent messages")

    def delete(self, account: dict, msg: dict) -> dict:
        raise PermanentSendError(f"{self.name} does not support deleting sent messages")

    def revoke(self, account: dict) -> dict:
        return {"remote_revoked": False, "note": "revoke the grant in the provider's admin settings"}


# ---------------------------------------------------------------- Slack
SLACK = "https://slack.com/api/"
SLACK_AUTH_ERRORS = {"invalid_auth", "token_revoked", "token_expired", "account_inactive", "not_authed"}


class SlackProvider(Provider):
    name = "slack"
    kind = "chat"
    capabilities = {"drafts": False, "edit": True, "delete": True, "threads": True,
                    "reconcile": "exact (bot tokens: message metadata); none for user tokens",
                    "app_identity": "bot user", "sources": ["https://docs.slack.dev/reference/methods/chat.postMessage"]}

    def validate_recipient(self, r: str) -> str | None:
        return None if re.fullmatch(r"[CGDU][A-Z0-9]{6,}", r) else f"{r!r} is not a Slack channel or user id"

    def can_reconcile(self, account: dict) -> bool:
        return account["identity_kind"] == "bot"  # message metadata needs an app/bot token

    def _check(self, resp: Response) -> dict:
        if resp.status == 429:
            raise RateLimited("slack rate limited", _retry_after(resp, 1.0))
        if resp.status >= 500:
            raise TransientError(f"slack HTTP {resp.status}")
        body = resp.body or {}
        if not body.get("ok"):
            err = body.get("error", f"HTTP {resp.status}")
            if err in ("ratelimited", "rate_limited"):
                raise RateLimited(err, _retry_after(resp, 1.0))
            if err in SLACK_AUTH_ERRORS:
                raise AuthExpired(err)
            raise PermanentSendError(err)
        return body

    def send(self, account: dict, msg: dict) -> SendResult:
        payload: dict[str, Any] = {"channel": msg["channel"], "text": msg["body"]}
        if msg.get("thread"):
            payload["thread_ts"] = msg["thread"]
        if account["identity_kind"] == "bot":  # metadata needs an app/bot token
            payload["metadata"] = json.dumps({"event_type": "lucid_send", "event_payload": {"op_id": msg["op_id"]}})
        body = self._check(self._call(account, "POST", SLACK + "chat.postMessage", json_body=payload))
        return SendResult(body["ts"], {"channel": body.get("channel"), "ts": body["ts"]})

    def lookup(self, account: dict, msg: dict) -> SendResult | None:
        if account["identity_kind"] != "bot":
            return None
        params = {"channel": msg["channel"], "include_all_metadata": "true", "limit": 200}
        if msg.get("thread"):
            params["ts"] = msg["thread"]
            url = SLACK + "conversations.replies"
        else:
            url = SLACK + "conversations.history"
        body = self._check(self._call(account, "GET", url, params=params))
        for m in body.get("messages", []):
            meta = m.get("metadata") or {}
            if meta.get("event_type") == "lucid_send" and (meta.get("event_payload") or {}).get("op_id") == msg["op_id"]:
                return SendResult(m["ts"], {"channel": msg["channel"], "ts": m["ts"], "reconciled": True})
        return None

    def edit(self, account: dict, msg: dict, new_body: str) -> dict:
        return self._check(self._call(account, "POST", SLACK + "chat.update",
                                      json_body={"channel": msg["channel"], "ts": msg["provider_ref"],
                                                 "text": new_body}))

    def delete(self, account: dict, msg: dict) -> dict:
        return self._check(self._call(account, "POST", SLACK + "chat.delete",
                                      json_body={"channel": msg["channel"], "ts": msg["provider_ref"]}))

    def revoke(self, account: dict) -> dict:
        try:
            self._check(self._call(account, "POST", SLACK + "auth.revoke", json_body={}))
            return {"remote_revoked": True}
        except PermanentSendError as e:
            return {"remote_revoked": False, "note": str(e)}


# ---------------------------------------------------------------- Gmail
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/"
EMAIL_RE = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[A-Za-z]{2,}")


def message_id_for(op_id: str, sender: str) -> str:
    domain = sender.split("@", 1)[1] if "@" in sender else "lucid.local"
    return f"<lucid-{op_id}@{domain}>"


def build_mime(account: dict, msg: dict) -> EmailMessage:
    m = EmailMessage()
    m["From"] = account["identity"]
    m["To"] = ", ".join(msg["recipients"])
    m["Subject"] = msg.get("subject") or ""
    m["Date"] = formatdate(localtime=False)
    m["Message-ID"] = message_id_for(msg["op_id"], account["identity"])
    if msg.get("in_reply_to"):
        m["In-Reply-To"] = msg["in_reply_to"]
        m["References"] = msg.get("references") or msg["in_reply_to"]
    m.set_content(msg["body"])
    for a in msg.get("attachments", []):
        with open(a["path"], "rb") as fh:
            data = fh.read()
        maintype, _, subtype = (a.get("mime") or "application/octet-stream").partition("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=a["name"])
    return m


class GmailProvider(Provider):
    name = "gmail"
    kind = "email"
    capabilities = {"drafts": True, "edit": False, "delete": False, "threads": True,
                    "reconcile": "exact (Message-ID search)", "app_identity": "the user's own address or a verified alias",
                    "sources": ["https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send"]}

    def validate_recipient(self, r: str) -> str | None:
        return None if EMAIL_RE.fullmatch(r) else f"{r!r} is not an email address"

    def _check(self, resp: Response) -> Any:
        if resp.status in (200, 201, 204):
            return resp.body
        reason = ""
        try:
            reason = (resp.body or {}).get("error", {}).get("errors", [{}])[0].get("reason", "")
        except (AttributeError, IndexError):
            reason = ""
        if resp.status == 429 or (resp.status == 403 and reason in ("rateLimitExceeded", "userRateLimitExceeded")):
            raise RateLimited(reason or "gmail rate limited", _retry_after(resp, 2.0))
        if resp.status == 401:
            raise AuthExpired("gmail credential expired or revoked")
        if resp.status >= 500:
            raise TransientError(f"gmail HTTP {resp.status}")
        raise PermanentSendError(f"gmail HTTP {resp.status} {reason}".strip())

    def _raw(self, account: dict, msg: dict) -> dict:
        raw = base64.urlsafe_b64encode(bytes(build_mime(account, msg))).decode()
        out: dict[str, Any] = {"raw": raw}
        if msg.get("thread"):
            out["threadId"] = msg["thread"]
        return out

    def send(self, account: dict, msg: dict) -> SendResult:
        body = self._check(self._call(account, "POST", GMAIL + "messages/send", json_body=self._raw(account, msg)))
        # Google: a 200 does not prove delivery; it means Gmail accepted the message.
        return SendResult(body["id"], {"id": body["id"], "threadId": body.get("threadId"), "accepted": True,
                                       "message_id": message_id_for(msg["op_id"], account["identity"])})

    def lookup(self, account: dict, msg: dict) -> SendResult | None:
        q = "rfc822msgid:" + message_id_for(msg["op_id"], account["identity"]).strip("<>")
        body = self._check(self._call(account, "GET", GMAIL + "messages", params={"q": q, "includeSpamTrash": "true"}))
        found = (body or {}).get("messages") or []
        if found:
            return SendResult(found[0]["id"], {"id": found[0]["id"], "threadId": found[0].get("threadId"),
                                               "reconciled": True})
        return None

    def create_draft(self, account: dict, msg: dict) -> SendResult:
        body = self._check(self._call(account, "POST", GMAIL + "drafts", json_body={"message": self._raw(account, msg)}))
        return SendResult(body["id"], {"draft_id": body["id"], "message_id": (body.get("message") or {}).get("id")})


# ---------------------------------------------------------------- Microsoft Graph
GRAPH = "https://graph.microsoft.com/v1.0/"


class _GraphMixin:
    def _check(self, resp: Response) -> Any:
        if resp.status in (200, 201, 202, 204):
            return resp.body
        code = ((resp.body or {}).get("error") or {}).get("code", "") if isinstance(resp.body, dict) else ""
        if resp.status in (429, 503):
            raise RateLimited(code or "graph throttled", _retry_after(resp, 5.0))
        if resp.status == 401:
            raise AuthExpired("Microsoft Graph credential expired or revoked")
        if resp.status >= 500:
            raise TransientError(f"graph HTTP {resp.status}")
        raise PermanentSendError(f"graph HTTP {resp.status} {code}".strip())


class OutlookProvider(_GraphMixin, Provider):
    name = "outlook"
    kind = "email"
    capabilities = {"drafts": True, "edit": False, "delete": False, "threads": True,
                    "reconcile": "exact (draft state, then x-lucid-op-id header in Sent Items)",
                    "app_identity": "the signed-in user, or a shared mailbox with Send As / Send on Behalf rights",
                    "sources": ["https://learn.microsoft.com/en-us/graph/api/message-send?view=graph-rest-1.0"]}

    def validate_recipient(self, r: str) -> str | None:
        return None if EMAIL_RE.fullmatch(r) else f"{r!r} is not an email address"

    def _message(self, msg: dict) -> dict:
        return {"subject": msg.get("subject") or "", "body": {"contentType": "Text", "content": msg["body"]},
                "toRecipients": [{"emailAddress": {"address": r}} for r in msg["recipients"]],
                "internetMessageHeaders": [{"name": "x-lucid-op-id", "value": msg["op_id"]}]}

    def create_draft(self, account: dict, msg: dict) -> SendResult:
        if msg.get("in_reply_to"):
            body = self._check(self._call(account, "POST", GRAPH + f"me/messages/{msg['in_reply_to']}/createReply",
                                          json_body={"message": self._message(msg)}))
        else:
            body = self._check(self._call(account, "POST", GRAPH + "me/messages", json_body=self._message(msg)))
        return SendResult(body["id"], {"draft_id": body["id"]})

    def send(self, account: dict, msg: dict) -> SendResult:
        # Two steps so an ambiguous send can be settled: the draft id is known before sending.
        draft_id = msg.get("provider_ref") or self.create_draft(account, msg).provider_ref
        msg["provider_ref"] = draft_id
        try:
            self._check(self._call(account, "POST", GRAPH + f"me/messages/{draft_id}/send"))
        except OutcomeUnknown as e:
            raise OutcomeUnknown(f"draft {draft_id}: {e}") from e
        return SendResult(draft_id, {"draft_id": draft_id, "accepted": True, "note": "202 Accepted"})

    def lookup(self, account: dict, msg: dict) -> SendResult | None:
        if msg.get("provider_ref"):
            r = self._call(account, "GET", GRAPH + f"me/messages/{msg['provider_ref']}",
                           params={"$select": "isDraft,sentDateTime"})
            if r.status == 200 and (r.body or {}).get("isDraft") is True:
                return None  # still a draft: the send did not happen
        r = self._call(account, "GET", GRAPH + "me/mailFolders/sentitems/messages",
                       params={"$top": 50, "$select": "id,internetMessageHeaders,sentDateTime"})
        for m in (self._check(r) or {}).get("value", []):
            for h in m.get("internetMessageHeaders") or []:
                if h.get("name", "").lower() == "x-lucid-op-id" and h.get("value") == msg["op_id"]:
                    return SendResult(m["id"], {"id": m["id"], "sentDateTime": m.get("sentDateTime"), "reconciled": True})
        return None


class TeamsProvider(_GraphMixin, Provider):
    name = "teams"
    kind = "chat"
    capabilities = {"drafts": False, "edit": False, "delete": False, "threads": True,
                    "reconcile": "heuristic (same sender, identical text, recent)",
                    "app_identity": "delegated user only; Graph app-only sending is for migration, bots need "
                                    "Bot Framework (not implemented)",
                    "sources": ["https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0"]}

    def validate_recipient(self, r: str) -> str | None:
        ok = r.startswith("chat:") or re.fullmatch(r"team:[^/]+/channel:.+", r)
        return None if ok else f"{r!r} must be chat:<id> or team:<id>/channel:<id>"

    def _base(self, channel: str) -> str:
        if channel.startswith("chat:"):
            return GRAPH + f"chats/{channel[5:]}/messages"
        team, ch = channel.split("/", 1)
        return GRAPH + f"teams/{team[5:]}/channels/{ch[8:]}/messages"

    def send(self, account: dict, msg: dict) -> SendResult:
        if account["identity_kind"] != "user_delegated":
            raise PermanentSendError("Teams messages via Graph need a delegated user token")
        url = self._base(msg["channel"]) + (f"/{msg['thread']}/replies" if msg.get("thread") else "")
        body = self._check(self._call(account, "POST", url,
                                      json_body={"body": {"contentType": "text", "content": msg["body"]}}))
        return SendResult(body["id"], {"id": body["id"], "createdDateTime": body.get("createdDateTime")})

    def lookup(self, account: dict, msg: dict) -> SendResult | None:
        url = self._base(msg["channel"]) + (f"/{msg['thread']}/replies" if msg.get("thread") else "")
        body = self._check(self._call(account, "GET", url, params={"$top": 20}))
        for m in (body or {}).get("value", []):
            sender = (((m.get("from") or {}).get("user")) or {}).get("id")
            if sender == account["identity"] and ((m.get("body") or {}).get("content") or "").strip() == msg["body"].strip():
                return SendResult(m["id"], {"id": m["id"], "reconciled": True, "method": "content match"})
        return None


PROVIDERS: dict[str, type[Provider]] = {"slack": SlackProvider, "gmail": GmailProvider, "outlook": OutlookProvider,
                                        "teams": TeamsProvider}
