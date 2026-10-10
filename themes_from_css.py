#!/usr/bin/env python3
"""
themes_from_css.py - regenerate themes.json from Gribble's theme CSS.

    python themes_from_css.py path/to/gribble-themes.css > themes.json

Reads every `:root.theme-<id>` / `:root.theme-<id>.dark` block (plus the bare
`:root` / `:root.dark` default) and turns `--g-foo-bar: #hex;` into
`{"foo_bar": "#hex"}`. Keeps the badge's palette in lockstep with the app
without hand-copying hex codes.
"""
import json
import re
import sys
from pathlib import Path

BLOCK = re.compile(r"((?::root[^{]*,?\s*)+)\{([^}]*)\}", re.S)
VAR = re.compile(r"--g-([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})")
LABEL = re.compile(r"/\*\s*=+\s*(?:NEW\s+)?THEME:\s*([^=\n]+?)\s*(?:\(DEFAULT\))?\s*=+", re.I)


def parse(css: str, default_id: str = "adventure") -> dict:
    themes: dict = {}
    labels = {}
    # label comments precede their block; remember the last one seen
    pos_labels = [(m.start(), m.group(1).strip()) for m in LABEL.finditer(css)]
    for m in BLOCK.finditer(css):
        selectors, body = m.group(1), m.group(2)
        colours = {k.replace("-", "_"): v.lower() for k, v in VAR.findall(body)}
        if not colours:
            continue
        label = next((l for p, l in reversed(pos_labels) if p < m.start()), None)
        sels = [x.strip() for x in selectors.split(",") if x.strip()]
        named = [x for x in sels if "theme-" in x]
        # A bare `:root` grouped with a `:root.theme-x` selector is just the
        # default alias of that theme, not a theme of its own.
        for sel in (named or sels):
            dark = sel.endswith(".dark")
            tm = re.search(r"theme-([a-z0-9]+)", sel)
            tid = tm.group(1) if tm else default_id
            themes.setdefault(tid, {})[("dark" if dark else "light")] = colours
            if label and tid not in labels:
                labels[tid] = label
    out = {"_comment": "Generated from gribble-themes.css by themes_from_css.py. Keys mirror the --g-* CSS variables."}
    for tid, modes in themes.items():
        if "light" in modes and "dark" in modes:
            out[tid] = {"label": labels.get(tid, tid.title()), **modes}
        else:
            print(f"warning: theme '{tid}' is missing a mode, skipped", file=sys.stderr)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(parse(Path(sys.argv[1]).read_text()), indent=2))
