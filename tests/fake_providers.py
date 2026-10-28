"""In-memory fakes of the Slack Web API, Gmail API and Microsoft Graph, shaped after the official
docs (see docs/team-integrations.md). They record every request so tests can check what was sent,
and they can inject failures: rate limits, revoked tokens, and timeouts after the provider applied
the request (the case that causes duplicate sends if handled badly)."""
import base64
import email
import email.policy
import itertools
import json

from lucid.comms_providers import Response
from lucid.sync import OutcomeUnknown


class FakeWorkspace:
    def __init__(self, tokens=("tok",)):
        self.tokens = set(tokens)
        self.requests = []
        self.inject = []          # list of (url_substring, action) consumed in order
        self.slack = {}           # channel -> [messages]
        self.gmail_sent = []      # {id, threadId, message_id, raw}
        self.gmail_drafts = []
        self.graph_msgs = {}      # id -> message
        self.teams = {}           # base url -> [messages]
        self.ids = itertools.count(1)

    def _take(self, url):
        for i, (sub, action) in enumerate(self.inject):
            if sub in url:
                self.inject.pop(i)
                return action
        return None

    def __call__(self, method, url, *, token, json_body=None, params=None, timeout=30):
        self.requests.append({"method": method, "url": url, "json": json_body, "params": params})
        action = self._take(url)
        if action == "429":
            return Response(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "7"})
        if action == "gmail_403_rate":
            return Response(403, {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}})
        if action == "500":
            return Response(500, {})
        if token not in self.tokens:
            if "slack.com" in url:
                return Response(200, {"ok": False, "error": "token_revoked"})
            return Response(401, {"error": {"code": "InvalidAuthenticationToken"}})
        resp = self._route(method, url, json_body or {}, params or {})
        if action == "timeout_after_apply":
            raise OutcomeUnknown("read timed out")
        if action == "timeout_before_apply":
            self._undo(url)
            raise OutcomeUnknown("connection reset")
        return resp

    def _undo(self, url):
        if "chat.postMessage" in url:
            for msgs in self.slack.values():
                if msgs:
                    msgs.pop()
        elif "messages/send" in url and self.gmail_sent:
            self.gmail_sent.pop()

    # ------------------------------------------------------------------ routing
    def _route(self, method, url, body, params):
        if "slack.com/api/" in url:
            return self._slack(url.rsplit("/", 1)[1], body, params)
        if "gmail.googleapis.com" in url:
            return self._gmail(method, url, body, params)
        if "graph.microsoft.com" in url:
            return self._graph(method, url, body, params)
        return Response(404, {})

    def _slack(self, m, body, params):
        if m == "chat.postMessage":
            ch = body["channel"]
            if ch.startswith("CARCHIVED"):
                return Response(200, {"ok": False, "error": "is_archived"})
            ts = f"1700000000.{next(self.ids):06d}"
            msg = {"ts": ts, "text": body["text"], "thread_ts": body.get("thread_ts")}
            if body.get("metadata"):
                msg["metadata"] = json.loads(body["metadata"])
            self.slack.setdefault(ch, []).append(msg)
            return Response(200, {"ok": True, "channel": ch, "ts": ts})
        if m in ("conversations.history", "conversations.replies"):
            msgs = list(self.slack.get(params["channel"], []))
            if m == "conversations.replies":
                msgs = [x for x in msgs if x.get("thread_ts") == params["ts"]]
            if params.get("include_all_metadata") != "true":
                msgs = [{k: v for k, v in x.items() if k != "metadata"} for x in msgs]
            return Response(200, {"ok": True, "messages": msgs})
        if m == "chat.update":
            for x in self.slack.get(body["channel"], []):
                if x["ts"] == body["ts"]:
                    x["text"] = body["text"]
            return Response(200, {"ok": True})
        if m == "chat.delete":
            self.slack[body["channel"]] = [x for x in self.slack.get(body["channel"], []) if x["ts"] != body["ts"]]
            return Response(200, {"ok": True})
        if m == "auth.revoke":
            self.tokens.clear()
            return Response(200, {"ok": True, "revoked": True})
        return Response(200, {"ok": False, "error": "unknown_method"})

    def _gmail(self, method, url, body, params):
        if url.endswith("messages/send"):
            raw = base64.urlsafe_b64decode(body["raw"])
            parsed = email.message_from_bytes(raw, policy=email.policy.default)
            rec = {"id": f"g{next(self.ids)}", "threadId": body.get("threadId") or f"t{next(self.ids)}",
                   "message_id": parsed["Message-ID"], "parsed": parsed}
            self.gmail_sent.append(rec)
            return Response(200, {"id": rec["id"], "threadId": rec["threadId"], "labelIds": ["SENT"]})
        if url.endswith("/drafts"):
            d = {"id": f"d{next(self.ids)}", "message": {"id": f"g{next(self.ids)}"}}
            self.gmail_drafts.append({**d, "parsed": email.message_from_bytes(
                base64.urlsafe_b64decode(body["message"]["raw"]), policy=email.policy.default)})
            return Response(200, d)
        if url.endswith("/messages") and method == "GET":
            q = params.get("q", "")
            want = q.split("rfc822msgid:", 1)[1] if "rfc822msgid:" in q else None
            hits = [{"id": r["id"], "threadId": r["threadId"]} for r in self.gmail_sent
                    if want and r["message_id"].strip("<>") == want]
            return Response(200, {"messages": hits} if hits else {"resultSizeEstimate": 0})
        return Response(404, {})

    def _graph(self, method, url, body, params):
        path = url.split("/v1.0/", 1)[1]
        if path == "me/messages" and method == "POST":
            mid = f"o{next(self.ids)}"
            self.graph_msgs[mid] = {"id": mid, "isDraft": True, **body}
            return Response(201, {"id": mid})
        if path.startswith("me/messages/") and path.endswith("/send"):
            mid = path.split("/")[2]
            self.graph_msgs[mid]["isDraft"] = False
            self.graph_msgs[mid]["sentDateTime"] = "2026-01-01T00:00:00Z"
            return Response(202, None)
        if path.startswith("me/messages/") and path.endswith("/createReply"):
            mid = f"o{next(self.ids)}"
            self.graph_msgs[mid] = {"id": mid, "isDraft": True, "replyTo": path.split("/")[2], **body["message"]}
            return Response(201, {"id": mid})
        if path.startswith("me/messages/") and method == "GET":
            m = self.graph_msgs.get(path.split("/")[2])
            return Response(200, m) if m else Response(404, {"error": {"code": "ErrorItemNotFound"}})
        if path == "me/mailFolders/sentitems/messages":
            return Response(200, {"value": [m for m in self.graph_msgs.values() if not m["isDraft"]]})
        if path.startswith(("chats/", "teams/")):
            base = path
            if method == "POST":
                mid = f"tm{next(self.ids)}"
                self.teams.setdefault(base, []).append({"id": mid, "body": body["body"],
                                                        "from": {"user": {"id": "user-1"}}})
                return Response(201, {"id": mid, "createdDateTime": "2026-01-01T00:00:00Z"})
            return Response(200, {"value": self.teams.get(base, [])})
        return Response(404, {})
