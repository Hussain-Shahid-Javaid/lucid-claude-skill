"""Built-in connector: watch a local folder of exports (Lucid JSON/JSONL or Markdown)."""
from __future__ import annotations

import os

from ..importers import ADAPTERS
from .sdk import Connector, ConnectorManifest


class FolderConnector(Connector):
    manifest = ConnectorManifest(
        id="folder", name="Local export folder", product_account_types=["any tool that can export files"],
        authentication="local", operations=["import"], conversation_coverage="whatever the files contain",
        branches="preserved for Lucid JSON; reconstructed for Markdown", attachments="references only",
        formats=["json", "jsonl", "md", "txt"], incremental_sync=True,
        deletion="files removed from the folder are not deleted from Lucid (delete in dashboard)",
        rate_limits="n/a", permissions=["read access to the configured folder"],
        verification="fixture-tested 2026-10-27")

    def health(self):
        d = self.config.get("path")
        return {"status": "configured" if d and os.path.isdir(d) else "unconfigured"}

    def fetch(self, since):
        d = self.config["path"]
        files = sorted(f for f in os.listdir(d) if f.rsplit(".", 1)[-1] in ("json", "jsonl", "md", "txt"))
        for f in files:
            p = os.path.join(d, f)
            pos = f"{os.path.getmtime(p):.6f}|{f}"
            if since and pos <= since:
                continue
            data = open(p, "rb").read()
            parser = ADAPTERS["markdown" if f.endswith((".md", ".txt")) else "generic"][1]
            for conv in parser(data):
                yield pos, conv
