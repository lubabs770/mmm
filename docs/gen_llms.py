#!/usr/bin/env python3
"""Generates llms.txt and llms-full.txt for the docs site.

`llms.txt` is an index an AI assistant can fetch to find its way around;
`llms-full.txt` is every page in one file, so a whole API can be read in one go.
Both come from the same Markdown sources as the site, in the order mkdocs.yml lists
them, and the build fails if a page is missing from the index.

Usage:
    gen_llms.py <mkdocs.yml> <site_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def nav_pages(mkdocs: dict) -> list[tuple[str, str]]:
    """[(title, filename)] in nav order."""
    pages: list[tuple[str, str]] = []

    def walk(entry: object) -> None:
        if isinstance(entry, list):
            for item in entry:
                walk(item)
        elif isinstance(entry, dict):
            for title, target in entry.items():
                if isinstance(target, str):
                    pages.append((str(title), target))
                else:
                    walk(target)
        elif isinstance(entry, str):
            pages.append((entry, entry))

    walk(mkdocs.get("nav") or [])
    return pages


def summary(markdown: str) -> str:
    """The first real paragraph, flattened to one line.

    Headings, HTML comments and code blocks are skipped: a page that opens with an
    example should still describe itself in the index.
    """
    lines: list[str] = []
    in_fence = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            if lines:
                break
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("<!--") or line.startswith("#") or not line:
            if lines:
                break
            continue
        lines.append(line)
    return " ".join(lines)[:300]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    config_path = Path(argv[0])
    site = Path(argv[1])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    docs_dir = config_path.parent / config.get("docs_dir", "docs")
    base = (config.get("site_url") or "").rstrip("/")

    pages = nav_pages(config)
    present = sorted(path.name for path in docs_dir.glob("*.md"))
    listed = sorted(filename for _, filename in pages)
    missing = [name for name in present if name not in listed]
    if missing:
        print(f"these pages are not in the nav, so llms.txt would miss them: {missing}")
        return 1

    index = [
        f"# {config.get('site_name', 'MMM')}",
        "",
        f"> {config.get('site_description', '')}",
        "",
        "## Docs",
        "",
    ]
    full = [f"# {config.get('site_name', 'MMM')} — complete documentation", ""]

    for title, filename in pages:
        source = docs_dir / filename
        if not source.exists():
            print(f"nav lists {filename}, which does not exist")
            return 1
        markdown = source.read_text(encoding="utf-8")
        if not base:
            url = filename
        elif filename == "index.md":
            url = f"{base}/"
        else:
            url = f"{base}/{filename[:-3]}/"
        index.append(f"- [{title}]({url}): {summary(markdown)}")
        full += [f"\n\n---\n\n## {title} ({filename})\n", markdown]

    site.mkdir(parents=True, exist_ok=True)
    (site / "llms.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    (site / "llms-full.txt").write_text("\n".join(full) + "\n", encoding="utf-8")
    print(f"wrote {site / 'llms.txt'} with {len(pages)} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
