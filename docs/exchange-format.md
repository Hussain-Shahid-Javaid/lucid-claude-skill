# Lucid conversation exchange format — `lucid.conversation.v1`

Use this format when exporting conversations from your own tools or converting other exports. It comes in two forms.

**JSON document:**

```json
{
  "format": "lucid.conversation.v1",
  "conversations": [
    {
      "provider": "my-tool",            // required; namespace (never merged across providers)
      "account": "work",                // optional; default "default" (never merged across accounts)
      "id": "conv-123",                 // required; stable id within provider+account
      "title": "Storage design",
      "created_at": "2026-10-01T09:00:00Z",
      "updated_at": "2026-10-01T10:00:00Z",
      "current_leaf": "m4",             // optional; active branch tip
      "coverage": "full",               // full | partial | reconstructed
      "messages": [
        {"id": "m1", "parent": null, "role": "user", "content": "Pick a DB", "created_at": "…"},
        {"id": "m2", "parent": "m1", "role": "assistant", "content": "Postgres"},
        {"id": "m3", "parent": "m1", "role": "assistant", "content": "SQLite for local mode"},
        {"id": "m4", "parent": "m3", "role": "user", "content": "Go with SQLite",
         "attachments": [{"name": "diagram.png", "uri": "file:///…", "mime": "image/png"}]}
      ]
    }
  ]
}
```

**JSONL:** one conversation object per line, in the same shape as an element of `conversations`.

## Rules

- `role` is one of `user`, `assistant`, `system` or `tool`. Unknown roles are kept in `origin.meta.original_role`.
- `parent` builds the tree. If **no** message has a `parent` key, messages are linked in listed order.
- `content` may be a string or a list of `{ "text": … }` parts.
- Attachments are stored as references only; binary content is not imported.
- Re-importing is idempotent:
  - The same message id with the same content does nothing.
  - The same id with changed content is stored as a new revision, and the old one is kept with status `revised`.
  - Identical text under different ids is kept as separate messages.
- Deleted conversations and messages, which are tombstoned, are skipped on reimport.
- Text containing `lucid-handoff:hnd_…` is flagged as a Lucid echo. It is never treated as independent evidence.

## Markdown / text

The importer recognises headings or prefixes such as `## User`, `**Assistant:**`, `Human:`, `Claude:`, `ChatGPT:` and `Gemini:`. The result is linear (`coverage=reconstructed`), with no ids, branches, attachments or reliable timestamps.
