# Email, Slack and Microsoft Teams

Lucid can draft and send messages through Gmail, Outlook, Slack and Microsoft Teams. Other team tools can be added as connectors. All of this is optional.

**Status.** The code is written and tested against in-memory fakes. The fakes follow the documented request and response shapes (`tests/fake_providers.py`). None of the four adapters has been run against a live account yet. Treat them as unverified until you have tried one with your own workspace.

## What connecting an account allows

Connecting an account only lets Lucid **draft**. Anything more needs a policy that you set yourself (`lucid comms policy`, or `Comms.set_policy`):

| mode | what happens |
|---|---|
| `draft_only` | Lucid writes drafts. For Gmail and Outlook, it can put them in your drafts folder. It never sends. |
| `send_after_review` | You approve the exact text. Any edit after approval needs approval again. Flagged items, such as a recipient not on your list, must be confirmed one by one. |
| `auto` | Lucid sends without asking, but only within the policy. Messages Lucid sends on its own end with a note that Lucid wrote them for you. A message you approved yourself is yours and goes out as written. A policy that is still valid is not re-asked for each message. |

A policy can set:
- the recipients (exact addresses or `@domain`) and channels allowed
- the workspaces allowed
- the topics allowed
- the actions allowed (`send`, `reply`, `edit`, `delete`)
- working hours (time zone, days, start and end times)
- rate limits per hour and per day
- the memory categories that may be shared
- the commitments Lucid may make (`meeting`, `deadline`, `promise`, `approval`, `purchase`, `contract`)
- words that always go to you, such as contract, price or legal
- attachment size and file types
- the disclosure text

Automatic mode requires:
- an explicit list of recipients or channels
- a rate limit

## What Lucid will not do

- **Send as anyone else.** It sends only as the connected identity. It refuses a different From address, and it does not set up send-as aliases.
- **Claim you did something without evidence.** It won't say you personally reviewed, attended, agreed to or said something unless you pass evidence for that claim. Without evidence, the message is escalated to you.
- **Make commitments outside the policy.** Promises, approvals, purchases, contracts or deadlines that the policy doesn't allow are escalated to you.
- **Send credentials.** Messages and text attachments that contain credentials are blocked.
- **Share the wrong memory.** Private memory is shared only when the user alone is the audience. Team memory goes only to that team's channels, and only in categories the policy allows.
- **Follow instructions inside incoming messages.** Incoming text is stored as untrusted data and flagged when it contains instructions. It cannot add recipients, change the policy or unlock memory. A reply always goes back to the same conversation.

## Reliable sending

These safeguards keep a message from being lost or sent twice:

- **Durable queue.** Each message has an op id, so composing the same thing twice gives back the same message.
- **One sender at a time.** A message is claimed atomically before sending, so two workers cannot both send it.
- **Rate limits.** Lucid honours the provider's `Retry-After`, plus your own hourly and daily limits.
- **Checks at send time.** Just before sending, Lucid checks again that:
  - the policy is still valid;
  - each attachment still has the hash it had when checked (a changed file blocks the send);
  - the text still matches what you approved;
  - the account is still active.
- **Ambiguous sends.** If a send times out after leaving Lucid, the outcome is unknown. Lucid looks for the message before it ever sends again, and retries only after two lookups found nothing.
  - Slack user tokens cannot be looked up this way. Those messages wait for you to check (`lucid comms mark <id> --sent|--not-sent`).
- **Credentials.** A revoked or expired credential marks the account `expired` and holds its messages until you reconnect. Revoking an account in Lucid:
  - deletes the stored token;
  - asks the provider to revoke it where the API allows (Slack `auth.revoke`);
  - cancels unsent messages.
- **Audit log.** Approvals, sends, edits and deletions are recorded in `comm_audit`.
- **Token storage.** Tokens live in their own 0600 files next to the database. They are never stored in the database, memory, logs or the dashboard. On the command line, pass a token with `--token-env NAME`, never as an argument.

## Provider details (checked 2026-10-28)

| | Slack | Gmail | Outlook (Graph) | Teams (Graph) |
|---|---|---|---|---|
| Send | `chat.postMessage` (`chat:write`) | `users.messages.send` | create draft, then `POST /me/messages/{id}/send` (`Mail.Send`) | `POST /chats/{id}/messages` or the channel equivalent (`ChatMessage.Send` / `ChannelMessage.Send`) |
| Threads | `thread_ts` | `threadId` + `In-Reply-To`/`References` | `createReply` | `.../messages/{id}/replies` |
| Drafts in the product | none (no API) | `users.drafts.create` | the draft from step 1 | none |
| Finding an ambiguous send | message metadata, read with `include_all_metadata` (bot tokens only) | `rfc822msgid:` search on a Message-ID made from the op id | the draft's state, then the `x-lucid-op-id` header in Sent Items | heuristic: same sender, same text, recent |
| Rate limits | about 1 msg/s per channel; HTTP 429 + `Retry-After` | 429, or 403 `rateLimitExceeded`; back off | 429/503 + `Retry-After` | 429 + `Retry-After` |
| Edit or delete after sending | `chat.update`, `chat.delete` | not possible | not supported here | not supported here |
| App identity | bot user | your own address or a verified alias | the signed-in user, or a shared mailbox with Send As / Send on Behalf | **delegated user only**: Graph app-only sending is for migration. A bot would need Bot Framework, which is not implemented. |

Two more caveats:

- **Gmail:** Google warns that a 200 reply does not prove delivery, so Lucid's receipt says "accepted", not "delivered".
- **Slack:** message metadata is visible to every app and user in the workspace. Lucid's metadata holds only the op id.

**Sources:**
- [Slack chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage.md)
- [Slack conversations.history](https://docs.slack.dev/reference/methods/conversations.history.md)
- [Slack rate limits](https://docs.slack.dev/apis/web-api/rate-limits.md)
- [Slack auth.revoke](https://docs.slack.dev/reference/methods/auth.revoke)
- [Gmail send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
- [Gmail drafts](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create)
- [Gmail threads](https://developers.google.com/workspace/gmail/api/guides/threads)
- [Gmail quota](https://developers.google.com/workspace/gmail/api/reference/quota)
- [Gmail errors](https://developers.google.com/workspace/gmail/api/guides/handle-errors)
- [Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)
- [Graph message send](https://learn.microsoft.com/en-us/graph/api/message-send?view=graph-rest-1.0)
- [Send from another user](https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user)
- [Graph throttling](https://learn.microsoft.com/en-us/graph/throttling)
- [Teams chatMessage post](https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0)
- [Teams proactive bot messages](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages)

## Other team tools

A package can supply a messaging provider. It does this by subclassing `lucid.comms_providers.Provider` and implementing `send`, plus `lookup` if the provider supports it. The package registers the provider with `Comms.register_provider`, or declares the `messaging` operation in a connector manifest. All of the policy, queue and audit rules above still apply.

## Tests

`tests/test_comms.py` covers:
- each mode
- incorrect and look-alike recipients
- unauthorized channels
- spoofed senders
- commitments, personal claims and escalation words
- credentials in messages and attachments
- attachment changes after checking
- duplicate composes and duplicate processing
- ambiguous sends: found, not found, or not checkable
- the Outlook two-step send
- provider and local rate limits
- working hours
- expired and revoked credentials
- thread replies and permanent errors
- Gmail drafts
- audited edits and deletions
- duplicate and malicious incoming messages
- keeping personal and team memory apart
- authorization that ends after a message was queued
- tokens kept out of the database

## Not done

- Live testing against real accounts.
- OAuth sign-in flows: bring tokens from your own app registration.
- Slack drafts: there is no API.
- Teams bot identity: needs Bot Framework.
- Teams message edit or delete.
- Reading inbox history: Lucid only ingests the events it is given.
