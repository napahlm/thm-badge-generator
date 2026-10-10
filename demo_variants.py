#!/usr/bin/env python3
"""
demo_variants.py - render edge-case profiles to check the layout never
overlaps or crashes. Writes badges/_variants.png. No network.

    python demo_variants.py [--theme adventure] [--scale 2]
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import thm_badge as tb

VARIANTS = [
    # label, username, level, points, rooms, badges, streak, rank, topPercentage
    ("baseline",         "napahlm",              13, 33010,   329,  42,  675,     11958, 1),
    ("brand new",        "x",                     1,     0,     0,   0,    0,   2345678, None),
    ("one day",          "newbie42",              2,   210,     3,   1,    1,   1987654, 87),
    ("long name",        "averyveryverylongname20", 8,  4100,    60,  12,   45,    400321, 23),
    ("huge streak",      "grinder",              21, 1234567, 4821, 999, 9999,         1, 1),
    ("grandmaster",      "ghost",                21, 150000,  2500, 300, 1500,         7, 1),
    ("guardian",         "wardens",              14,  35200,   410,  60,  365,      5021, 2),
    ("titan",            "atlas",                15,  52000,   700,  80,  730,      2210, 1),
    ("apprentice",       "mid_user",              2,   350,     8,   2,   12,    950000, 48),
    ("pathfinder",       "pf",                    3,   600,    15,   4,   30,    800000, 40),
    ("vanguard",         "vanguard_1",           17,  81000,  1200, 150,  800,       900, 1),
    ("unknown level",    "future",               42, 999999,  9999, 999,  999,        42, 1),
]


def stats_for(row):
    label, user, level, pts, rooms, badges, streak, rank, top = row
    return tb.Stats(username=user, level=level, points=pts, rooms=rooms, badges=badges,
                    streak=streak, largest_streak=streak, rank=rank, top_percentage=top,
                    league=None, avatar_url=None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="adventure")
    ap.add_argument("--mode", default="light", choices=["light", "dark"])
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--out", type=Path, default=tb.HERE / "badges" / "_variants.png")
    args = ap.parse_args()

    themes = json.loads(tb.THEMES_PATH.read_text())
    t = themes[args.theme][args.mode]
    fire = tb.load_fire_frames()
    fire0 = max(fire, key=lambda f: sum(1 for px in f.get_flattened_data() if px[3] > 0))

    cards = [(row[0], tb.render_badge(stats_for(row), t, None, fire0, "risen").img) for row in VARIANTS]
    gutter, label_h = 8, 14
    cw = max(im.size[0] for _, im in cards)
    ch = max(im.size[1] for _, im in cards)
    sheet = Image.new("RGBA", (cw + gutter * 2, (ch + label_h + gutter) * len(cards) + gutter),
                      (246, 248, 250, 255) if args.mode == "light" else (13, 17, 23, 255))
    d = ImageDraw.Draw(sheet)
    d.fontmode = "1"
    font = ImageFont.truetype(str(tb.FONT_PATH), tb.FONT_PX)
    y = gutter
    for label, im in cards:
        d.text((gutter, y - 2), f"{label}  ({im.size[0]}x{im.size[1]})", font=font,
               fill=(60, 60, 60, 255) if args.mode == "light" else (160, 160, 160, 255))
        sheet.alpha_composite(im, (gutter, y + label_h))
        y += ch + label_h + gutter
    w, h = sheet.size
    sheet.resize((w * args.scale, h * args.scale), Image.NEAREST).save(args.out, optimize=True)
    print(args.out)


if __name__ == "__main__":
    main()
