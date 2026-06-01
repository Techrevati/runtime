"""Clean generated documentation metadata."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

GENERATOR_META_RE = re.compile(
    r"\n?\s*<meta\s+name=\"generator\"\s+content=\"[^\"]+\">\s*",
    re.IGNORECASE,
)
GENERATED_BRANDING_COMMENT_RE = re.compile(
    r"\s*/\*[^*]*Material\s+for\s+MkDocs[^*]*\*/\s*",
    re.IGNORECASE,
)


def on_post_page(output: str, **_: object) -> str:
    """Strip toolchain generator metadata from rendered HTML pages."""
    return GENERATOR_META_RE.sub("\n", output)


def on_post_build(config: Any, **_: object) -> None:
    """Strip generated metadata from pages and generated support files."""
    site_dir = Path(config.site_dir)
    for path in site_dir.rglob("*.html"):
        content = path.read_text(encoding="utf-8")
        cleaned = GENERATOR_META_RE.sub("\n", content)
        if cleaned != content:
            path.write_text(cleaned, encoding="utf-8")

    for path in site_dir.rglob("*.css"):
        content = path.read_text(encoding="utf-8")
        cleaned = GENERATED_BRANDING_COMMENT_RE.sub("\n", content)
        if cleaned != content:
            path.write_text(cleaned, encoding="utf-8")
