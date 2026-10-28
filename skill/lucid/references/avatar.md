# Optional avatar: an AI representative

This module lets you make an avatar from **your own** photos and video, and from your voice if you authorize that separately. The avatar always appears as **"Lucid — AI representative for <you>"**. It never pretends you are there in person.

## What has been tested, and how

| Level | What it covers | Status |
|---|---|---|
| Simulated | Every session rule, run with a scripted meeting transport, recogniser and speech queue (`tests/test_avatar.py`) | 17 tests pass |
| Controlled WebRTC demonstration | A real `RTCPeerConnection` loopback in headless Chromium, covering video, a disclosure banner, captions, stop, takeover, a late participant and leaving (`tests/test_avatar_demo.py`) | passes |
| Provider sandbox (HeyGen, Tavus) | — | **not done**: there is no adapter |
| Live meeting platform (Teams, Zoom, Meet, Slack) | — | **not done**: there is no adapter |

The demo picture is a labelled card, not a lip-synced face. Do not describe this module as working in any meeting platform until that platform has been tested end to end.

## Enrollment

```bash
lucid avatar enroll "Hussain"
lucid avatar consent <id> likeness "I, Hussain, consent to Lucid using my likeness to create an AI representative of me."
lucid avatar add-media <id> photo me.jpg --mine
lucid avatar preview <id>                    # look at it, then:
lucid avatar activate <id> --reviewed <sha256 of that preview> [--voice]
```

These rules apply:

- **Only you can enroll yourself.** Owning someone's photos or recordings is not their permission. Consent must be signed by the account holder, and media must be confirmed as showing you.
- **Two separate consents.** Likeness and voice each need their own consent, with their own on/off switch. Voice also needs a voice sample. Without voice consent, the avatar uses captions and chat only.
- **Vendor rules apply on top.** A vendor that requires a consent video (HeyGen does) cannot be enrolled with typed consent.
- **Media stays in its vault.** Enrollment media sits in its own folder, with the folder set to 0700 and each file to 0600. It is never stored in the database, memory, handoffs or exports.
- **Media is appearance only.** Photos and video give the avatar its look and delivery. They say nothing about what you know, think or have authority to do.
- **Preview first.** You must look at the preview before activating. Activation checks the preview's hash, so a changed preview has to be reviewed again.
- **Revoking.** `lucid avatar revoke <id> [--kind voice]` stops a live session at its next step and revokes its meeting approvals.
- **Deleting.** `lucid avatar delete <id>` removes local media, asks the vendor to delete each kind of data it may hold, and reports the result for each item. Avatars, voices, source footage and generated videos are deleted separately at HeyGen. A failed vendor deletion is reported, never hidden.

## Meeting approval

```bash
lucid avatar authorize <id> --rules '{"categories": ["weekly-sync"], "hosts": ["sam@acme.com"],
  "knowledge": [{"id": "k1", "text": "The migration finished on Monday.", "audience": ["@acme.com"]}],
  "commitments": ["meeting"], "max_duration_s": 1800, "max_cost_usd": 2, "recording": false}'
```

The avatar joins only meetings that match an approved meeting id or category **and** an approved host. A calendar invitation is not approval. Timed default decisions cannot create this approval either.

## In the meeting

- **Telling people who it is.** The disclosure is spoken (when voice is on), posted in chat and shown as a caption at the start. Everyone who joins later gets it again.
- **What it can say.** It answers only from approved knowledge whose audience covers every current participant, so the answers narrow when someone with less clearance joins. Private memory is never used.
- **Unclear speech.** Low-confidence speech recognition gets "could you say it again?", not a guess.
- **Commitments.** Requests for approvals, signatures, purchases, promises or deadlines are answered with "*<you>* will need to confirm" and recorded as escalations, unless you allowed that kind of commitment. Even then, the avatar only accepts **provisionally** and you confirm in the follow-up, because speech recognition and phrasing can be misread. Provisional acceptance is given only when the commitment is the action being requested from you, or from Lucid as your representative ("can Hussain meet Tuesday?", "please book a call"). It is not given to a question that merely mentions meeting or signing. It is never given to reported speech ("Sam asked me…") or to a negated request.
- **Opinions.** Questions about your opinion are answered only from opinions you have stated in the approved knowledge. Your name alone never selects a knowledge item.
- **Stopping and taking over.** An interruption, a takeover, mute, stop or leave cancels speech at once and drops queued audio, so stale audio never plays. Only you can hand the meeting back after a takeover.
- **Recording.** Recording starts only if you allowed it, and only after the meeting shows a recording notice. If the platform cannot show one, the avatar does not record.
- **Transcription.** Without transcription, participants' words are used to answer and then not kept. The summary keeps only the requests that need your decision.
- **Questions and requests only.** The avatar replies to questions and to requests addressed to you, such as "please approve…" or "Hussain needs to sign…". It listens to statements without replying. A statement is never taken as agreement, even when it contains words like "will" or "meet". Requests it can't settle are recorded as escalations. Without transcription, only the clause holding the request is kept; this trimming is best effort.
- **Approval changes.** If an approval changes mid-meeting, queued speech is dropped and the platform and recording rules are applied again.
- **Lost connection.** If the network drops, the avatar tries to rejoin three times and gives the disclosure again when it rejoins. If it can't rejoin, it leaves and records an escalation.
- **Limits.** Avatar, speech-in, speech-out and meeting costs are metered separately from model tokens. At the cost or time limit it says it must leave, and leaves.
- **Afterwards.** The summary uses only the transcript and recorded events: what Lucid said, decisions it heard (quoted, with times), unresolved questions, escalations, commitments and the information it used. Follow-ups are drafts, even on an account that sends automatically.

The dashboard's **Messaging & avatar** tab shows each session's:
- identity, state and verification level ("simulated" for test sessions)
- recording and transcription status
- information used, escalations and costs

## Platform facts (checked 2026-10-28)

- **Slack huddles:** there is no official API for an app to join a huddle or send media into one. `calls.add` only registers a call hosted elsewhere ("Slack doesn't make the call"). Lucid does not claim huddle support.
- **Microsoft Teams meeting bots:** app-hosted media bots must meet these requirements:
  - written in C#/.NET using the Graph Communications media library
  - hosted on Windows Server in Azure, with a public IP for each VM
  - application permissions with admin consent (`Calls.JoinGroupCall.All`, `Calls.AccessMedia.All`, …)
  - `updateRecordingStatus` called before anything is recorded

  This is a separate deployment that Lucid does not include.
- **HeyGen:** requires a consent video for every video-based digital twin. Its real-time product is LiveAvatar, which uses one LiveKit room per session and bills streaming in credits.
- **Tavus:** its real-time product is CVI, which runs on WebRTC through Daily.

**Sources:**
- [Slack calls.add](https://docs.slack.dev/reference/methods/calls.add)
- [Using the Calls API](https://docs.slack.dev/apis/web-api/using-the-calls-api)
- [Teams app-hosted media requirements](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/requirements-considerations-application-hosted-media-bots)
- [updateRecordingStatus](https://learn.microsoft.com/en-us/graph/api/call-updaterecordingstatus?view=graph-rest-1.0)
- [HeyGen consent video](https://help.heygen.com/en/articles/12092609-recording-your-consent-video)
- [LiveAvatar FAQ](https://help.heygen.com/en/articles/12758866-liveavatar-faq)
- [HeyGen avatar API](https://developers.heygen.com/docs/create-avatar)
- [Tavus CVI](https://docs.tavus.io/sections/conversational-video-interface/overview-cvi)

## Pieces you can swap

`lucid/avatar/components.py` defines each part as a separate interface:
- renderer
- speech recognition
- speech generation
- meeting transport

The session (`lucid/avatar/session.py`) handles conversation, authorized retrieval and human takeover. Swapping in a real vendor or platform means implementing those interfaces, and then testing that integration end to end before calling it supported.
