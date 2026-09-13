#!/usr/bin/env python3
"""Compare two directories of generator distribution ZIPs.

Two builds of identical sources never produce byte-identical ZIPs: entry
timestamps change, and some formats embed the build time or a random
identifier. This script unpacks every ZIP (including nested .docx/.epub
archives), neutralises exactly that noise, and reports what is left.

Usage:
    compare-dist.py OLD_DIR NEW_DIR [--diff N]

OLD_DIR and NEW_DIR contain the ZIPs, or are downloaded workflow artifacts
with a dist/ subdirectory. Exit code is 0 if both sides are equivalent,
1 otherwise.
"""

import argparse
import difflib
import fnmatch
import io
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

NESTED_ARCHIVES = (".docx", ".epub")

# Each rule: description, entry globs it applies to, pattern, replacement.
# Keep these narrow - anything not listed here counts as a real difference.
NOISE_RULES = [
    ("html: asciidoctor 'Last updated' footer", ("*.html",),
     re.compile(rb"Last updated \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z0-9:+-]+"),
     rb"Last updated <TIMESTAMP>"),
    ("docx: created/modified date", ("docProps/core.xml",),
     re.compile(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*"),
     rb"\1<TIMESTAMP>"),
    ("epub: modified date", ("*.opf",),
     re.compile(rb'(<meta property="dcterms:modified">)[^<]*'),
     rb"\1<TIMESTAMP>"),
    ("epub: random book identifier", ("*.opf", "*.ncx"),
     re.compile(rb"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
     rb"urn:uuid:<UUID>"),
]


def normalise(entry_name, content, hits):
    for description, globs, pattern, replacement in NOISE_RULES:
        if any(fnmatch.fnmatch(entry_name, g) for g in globs):
            content, count = pattern.subn(replacement, content)
            hits[description] += count
    return content


def read_archive(data, hits, prefix=""):
    """Return {entry path: normalised content}; nested archives are flattened as 'outer!inner'."""
    entries = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            content = archive.read(info)
            path = prefix + info.filename
            if info.filename.endswith(NESTED_ARCHIVES):
                entries.update(read_archive(content, hits, path + "!"))
            else:
                entries[path] = normalise(info.filename, content, hits)
    return entries


def zip_dir(path):
    path = Path(path)
    if (path / "dist").is_dir():
        path = path / "dist"
    if not path.is_dir():
        sys.exit(f"not a directory: {path}")
    return {p.name: p for p in path.glob("*.zip")}


def first_difference(old, new, width=60):
    """Show a window around the first differing byte - for minified XML and similar."""
    pos = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
    start = max(0, pos - width)
    window = lambda data: data[start:pos + width].decode("utf-8", "replace").replace("\n", "\\n")
    return [f"    first difference at byte {pos}:",
            f"    - ...{window(old)}...",
            f"    + ...{window(new)}..."]


def text_diff(name, old, new, max_lines, max_width=200):
    if b"\0" in old or b"\0" in new:
        return [f"    (binary content differs: {len(old)} -> {len(new)} bytes)"]
    lines = difflib.unified_diff(
        old.decode("utf-8", "replace").splitlines(),
        new.decode("utf-8", "replace").splitlines(),
        f"old/{name}", f"new/{name}", lineterm="", n=1)
    lines = list(lines)
    if any(len(line) > max_width for line in lines):
        return first_difference(old, new)
    shown = ["    " + line for line in lines[:max_lines]]
    if len(lines) > max_lines:
        shown.append(f"    ... {len(lines) - max_lines} more diff lines")
    return shown


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("old_dir")
    parser.add_argument("new_dir")
    parser.add_argument("--diff", type=int, default=0, metavar="N",
                        help="show up to N unified diff lines per changed entry")
    args = parser.parse_args()

    old_zips, new_zips = zip_dir(args.old_dir), zip_dir(args.new_dir)
    only_old = sorted(old_zips.keys() - new_zips.keys())
    only_new = sorted(new_zips.keys() - old_zips.keys())
    common = sorted(old_zips.keys() & new_zips.keys())

    old_hits, new_hits = Counter(), Counter()
    different = []
    for name in common:
        old = read_archive(old_zips[name].read_bytes(), old_hits)
        new = read_archive(new_zips[name].read_bytes(), new_hits)
        changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
        removed = sorted(old.keys() - new.keys())
        added = sorted(new.keys() - old.keys())
        if changed or removed or added:
            different.append((name, old, new, changed, removed, added))

    for name, old, new, changed, removed, added in different:
        print(f"DIFFERENT {name}")
        for entry in changed:
            print(f"  ~ {entry}")
            if args.diff:
                print("\n".join(text_diff(entry, old[entry], new[entry], args.diff)))
        for entry in removed:
            print(f"  - {entry}")
        for entry in added:
            print(f"  + {entry}")
    for name in only_old:
        print(f"ONLY IN OLD {name}")
    for name in only_new:
        print(f"ONLY IN NEW {name}")

    print()
    print(f"Compared {len(common)} ZIPs: {len(common) - len(different)} equivalent, "
          f"{len(different)} different, {len(only_old)} only in old, {len(only_new)} only in new")
    print("Neutralised noise (old / new):")
    for description, *_ in NOISE_RULES:
        print(f"  {description}: {old_hits[description]} / {new_hits[description]}")

    return 1 if different or only_old or only_new else 0


if __name__ == "__main__":
    sys.exit(main())
