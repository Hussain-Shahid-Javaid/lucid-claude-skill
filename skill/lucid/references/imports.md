# Imports, handoffs and synchronization

Three different operations — never present one as another:

1. **Transcript import** – copy an exported conversation into Lucid's archive.
2. **Context handoff** – a document with selected memories/task state for another tool to read.
3. **Native-history sync** – writing into a provider's own chat history. No supported public interface
   was found for ChatGPT, Claude.ai or the Gemini app (checked 2026-10-27), so Lucid does not do this.

| adapter | evidence for the schema | branches | notes |
|---|---|---|---|
| `generic` | Lucid-defined (docs/exchange-format.md) | preserved | recommended interchange format |
| `markdown` | Lucid-defined | linear reconstruction | ids/timestamps/attachments lost |
| `chatgpt` | community-documented (`mapping` tree) | preserved | not validated on a live export |
| `claude` | community-documented (`chat_messages`, `sender`) | only if parent ids exist | some fields unverified |
| `gemini` | unverified (Takeout activity JSON) | none (per prompt) | experimental |

Re-importing is idempotent; deleted conversations stay deleted (tombstones); edited messages are kept as
revisions; identical text is never merged; handoff text pasted into another chat is recognised on
re-import (`lucid-handoff:` marker) and never counted as independent evidence.
