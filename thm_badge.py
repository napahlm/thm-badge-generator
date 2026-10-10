#!/usr/bin/env python3
"""
thm_badge.py - render a pixel-art TryHackMe profile badge (Gribble style).

Pipeline: fetch public stats -> render at native pixel resolution with
Departure Mono and Gribble's PixelFrame 9-slice -> nearest-neighbour upscale
-> PNG (+ GIF and CSS spritesheet with --animate).

Typical use:
    python thm_badge.py --username napahlm
    python thm_badge.py --username napahlm --theme nord --mode dark --animate
    python thm_badge.py --username napahlm --all-themes --sheet
    python thm_badge.py --stats badges/stats.json --theme gameboy   # offline

Dependencies: Pillow. Optional: playwright (only used when tryhackme.com
serves its Vercel browser challenge, which it does to GitHub-hosted runners).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
import random
import time

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

HERE = Path(__file__).resolve().parent
FONT_PATH = HERE / "fonts" / "DepartureMono-Regular.otf"
THEMES_PATH = HERE / "themes.json"
FIRE_SHEET = HERE / "sprites" / "fire.png"   # 4x5 grid of 32x32 frames, row-major

API_URL = "https://tryhackme.com/api/v2/public-profile?username={username}"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/129.0 Safari/537.36 thm-badge-generator/1.0 (+https://github.com/napahlm/thm-badge-generator)")

# Departure Mono is pixel-perfect at multiples of 11px.
FONT_PX = 11
CHAR_W = 7          # advance width at 11px
CAP_TOP = 3         # first ink row of a capital/digit, relative to draw y, at 11px
CAP_H = 8           # cap/digit height at 11px
BIG = 3             # streak digits at 33px: 24px tall, centred on the 32px fire

FIRE = 32           # fire frame size
FRAME_MS = 50       # per animation frame, one Minecraft tick

# Level number -> (hex label, title). Source: help.tryhackme.com "What are levels".
LEVELS = {
    1: ("0x1", "Neophyte"), 2: ("0x2", "Apprentice"), 3: ("0x3", "Pathfinder"),
    4: ("0x4", "Seeker"), 5: ("0x5", "Visionary"), 6: ("0x6", "Voyager"),
    7: ("0x7", "Adept"), 8: ("0x8", "Hacker"), 9: ("0x9", "Mage"),
    10: ("0xA", "Wizard"), 11: ("0xB", "Master"), 12: ("0xC", "Guru"),
    13: ("0xD", "Legend"), 14: ("0xE", "Guardian"), 15: ("0xF", "Titan"),
    16: ("0x10", "Sage"), 17: ("0x11", "Vanguard"), 18: ("0x12", "Shogun"),
    19: ("0x13", "Ascended"), 20: ("0x14", "Mythic"), 21: ("0x15", "Grandmaster"),
}

RETRY_STATUSES = {429, 500, 502, 503, 504}

@dataclass
class Stats:
    username: str
    level: int
    points: int
    rooms: int
    badges: int
    streak: int
    largest_streak: int
    rank: int
    top_percentage: int | None
    league: str | None
    avatar_url: str | None

    @property
    def level_hex(self) -> str:
        return LEVELS.get(self.level, (f"0x{self.level:X}", "?"))[0]

    @property
    def level_title(self) -> str:
        return LEVELS.get(self.level, (None, f"Level {self.level}"))[1]

    @classmethod
    def from_api(cls, data: dict) -> "Stats":
        return cls(
            username=data["username"],
            level=int(data.get("level", 1)),
            points=int(data.get("totalPoints", 0)),
            rooms=int(data.get("completedRoomsNumber", 0)),
            badges=int(data.get("badgesNumber", 0)),
            streak=int(data.get("streak", 0)),
            largest_streak=int(data.get("largestStreak", 0)),
            rank=int(data.get("rank", 0)),
            top_percentage=data.get("topPercentage"),
            league=data.get("leagueTier"),
            avatar_url=data.get("avatar"),
        )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

CHALLENGE_MARKERS = ("Security Checkpoint", "_vcrcs", "Attention Required")


class ChallengeError(RuntimeError):
    """tryhackme.com answered with a JavaScript challenge page instead of data."""


def looks_like_challenge(status: int, body: str) -> bool:
    return status in (403, 429, 503) and any(m in body for m in CHALLENGE_MARKERS)


def http_get(url: str, timeout: int = 20, attempts: int = 2) -> bytes:
    """Plain GET with a short retry for transient 5xx/429. A Vercel/Cloudflare
    challenge page is raised as ChallengeError at once: retrying cannot
    solve it, only a browser can (see fetch_via_browser)."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, image/*;q=0.9, */*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://tryhackme.com/",
    }
    delay = 5.0
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            code, hdrs = exc.code, dict(exc.headers)
            body = exc.read(400).decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            code, hdrs, body = 0, {}, str(exc)
        if looks_like_challenge(code, body):
            raise ChallengeError(f"HTTP {code} from {url.split('?')[0]}: browser challenge served")
        if code not in RETRY_STATUSES or attempt == attempts:
            info = {k: v for k, v in hdrs.items() if k.lower() in ("server", "retry-after", "cf-mitigated")}
            raise RuntimeError(f"HTTP {code} from {url.split('?')[0]} after {attempt} attempts; {info}; body: {body!r}")
        wait = delay + random.uniform(0, 3)
        print(f"warning: HTTP {code}, retry {attempt}/{attempts - 1} in {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
        delay = min(delay * 3, 60)
    raise RuntimeError("unreachable")


def _check_schema(data: dict) -> dict:
    for key in ("username", "level", "totalPoints", "streak"):
        if key not in data:
            raise RuntimeError(f"API response missing '{key}', schema may have changed")
    return data


def fetch_stats(username: str) -> dict:
    """Fetch the public profile JSON directly. Raises ChallengeError when the
    site wants a browser, any other failure as RuntimeError."""
    raw = http_get(API_URL.format(username=username))
    payload = json.loads(raw)
    if payload.get("status") != "success" or "data" not in payload:
        raise RuntimeError(f"unexpected API response: {raw[:200]!r}")
    return _check_schema(payload["data"])


def fetch_via_browser(username: str) -> tuple[dict, bytes | None]:
    """Open the profile in headless Chromium so the challenge can solve
    itself, then call the JSON API from inside that page (same origin, the
    challenge cookie attached). Returns (stats data, avatar bytes or None)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("tryhackme.com served a browser challenge and playwright is not installed: "
                           "pip install playwright && python -m playwright install --with-deps chromium")
    api = API_URL.format(username=username)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(locale="en-GB", viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(f"https://tryhackme.com/p/{username}", wait_until="domcontentloaded", timeout=60_000)
            # The checkpoint page reloads itself once its cookie is set.
            page.wait_for_function("!document.title.includes('Security Checkpoint')", timeout=60_000)
            page.wait_for_timeout(1500)
            res = page.evaluate(
                """async (url) => {
                     const r = await fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}});
                     return {status: r.status, text: await r.text()};
                   }""", api)
            if res["status"] != 200:
                raise RuntimeError(f"API returned {res['status']} inside the browser: {res['text'][:200]!r}")
            payload = json.loads(res["text"])
            if payload.get("status") != "success" or "data" not in payload:
                raise RuntimeError(f"unexpected API response in browser: {res['text'][:200]!r}")
            data = _check_schema(payload["data"])
            avatar = None
            if data.get("avatar"):
                try:
                    avatar = ctx.request.get(data["avatar"], timeout=30_000).body()
                except Exception as exc:  # cosmetic, never fatal
                    print(f"warning: avatar fetch in browser failed ({exc})", file=sys.stderr)
        finally:
            browser.close()
    return data, avatar


def fetch_avatar(url: str) -> Image.Image | None:
    try:
        return Image.open(io.BytesIO(http_get(url))).convert("RGBA")
    except Exception as exc:  # avatar is cosmetic, never fatal
        print(f"warning: avatar fetch failed ({exc}), rendering without avatar", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def hex_to_rgba(h: str) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def mix(a: str, b: str, t: float) -> str:
    """Linear blend of two hex colours, t=0 -> a, t=1 -> b."""
    ra, ga, ba, _ = hex_to_rgba(a)
    rb, gb, bb, _ = hex_to_rgba(b)
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def _lum(h: str) -> float:
    def ch(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b, _ = hex_to_rgba(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def pick(t: dict, keys: list[str], on: str = "surface", min_ratio: float = 3.0) -> str:
    """First theme colour in `keys` readable on t[on]; falls back to text."""
    for k in keys:
        if contrast(t[k], t[on]) >= min_ratio:
            return t[k]
    return t["text"]


# ---------------------------------------------------------------------------
# Gribble PixelFrame, ported from src/components/PixelFrame.vue
#
# The app draws its frames as a tiny pixel map (5x5 for sunken/risen at
# borderWidth 2, 7x7 for "normal", 3x3 for "blackBorder") and 9-slices it
# with CSS border-image. Same maps here, same slicing, drawn directly.
# D = --g-bevel-dark, L = --g-bevel-light, B = black. Risen swaps D and L.
# ---------------------------------------------------------------------------

def _frame_map(style: str, D: str, L: str) -> tuple[list[tuple[int, int, str]], int]:
    B = "#000000"
    if style == "risen":
        D, L = L, D
    if style in ("sunken", "risen"):
        return [
            (2, 0, D),
            (1, 1, D), (2, 1, B), (3, 1, D),
            (0, 2, L), (1, 2, B), (3, 2, B), (4, 2, L),
            (1, 3, L), (2, 3, B), (3, 3, L),
            (2, 4, L),
        ], 2
    if style == "normal":
        return [
            (2, 0, D), (3, 0, D), (4, 0, D),
            (1, 1, D), (2, 1, L), (3, 1, L), (4, 1, L), (5, 1, D),
            (0, 2, D), (1, 2, L), (2, 2, B), (3, 2, B), (4, 2, B), (5, 2, L), (6, 2, D),
            (0, 3, D), (1, 3, L), (2, 3, B), (4, 3, B), (5, 3, L), (6, 3, D),
            (0, 4, D), (1, 4, L), (2, 4, B), (3, 4, B), (4, 4, B), (5, 4, L), (6, 4, D),
            (1, 5, D), (2, 5, L), (3, 5, L), (4, 5, L), (5, 5, D),
            (2, 6, D), (3, 6, D), (4, 6, D),
        ], 3
    if style == "blackBorder":
        return [(1, 0, B), (0, 1, B), (2, 1, B), (1, 2, B)], 1
    raise ValueError(f"unknown frame style {style}")


FRAME_STYLES = ("sunken", "risen", "normal", "blackBorder")


class Canvas:
    """Native-resolution RGBA canvas with aliased (no anti-aliasing) text."""

    def __init__(self, w: int, h: int):
        self.img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.img)
        self.draw.fontmode = "1"  # hard pixels, no AA
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}

    def font(self, mult: int = 1) -> ImageFont.FreeTypeFont:
        if mult not in self._fonts:
            self._fonts[mult] = ImageFont.truetype(str(FONT_PATH), FONT_PX * mult)
        return self._fonts[mult]

    def text(self, x: int, y: int, s: str, color: str, mult: int = 1) -> int:
        """Draw text with its cap-top at y. Returns the x after the text."""
        self.draw.text((x, y - CAP_TOP * mult), s, font=self.font(mult), fill=hex_to_rgba(color))
        return x + len(s) * CHAR_W * mult

    def px(self, x: int, y: int, color: str):
        self.draw.point((x, y), fill=hex_to_rgba(color))

    def hline(self, x0: int, x1: int, y: int, color: str):
        self.draw.line([(x0, y), (x1, y)], fill=hex_to_rgba(color))

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: str):
        self.draw.rectangle([x0, y0, x1, y1], fill=hex_to_rgba(color))

    def blit_image(self, x: int, y: int, im: Image.Image):
        self.img.alpha_composite(im, (x, y))

    def pixel_frame(self, x0: int, y0: int, x1: int, y1: int, t: dict,
                    style: str = "sunken", fill: str | None = None) -> int:
        """Draw a Gribble PixelFrame around the box (x0,y0)-(x1,y1) inclusive.
        Returns the border thickness so callers can place content inside."""
        pmap, bw = _frame_map(style, t["bevel_dark"], t["bevel_light"])
        grid = bw * 2 + 1
        cells = {(x, y): c for x, y, c in pmap}
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if fill:
            self.rect(x0 + bw, y0 + bw, x1 - bw, y1 - bw, fill)

        def src(x: int, y: int) -> tuple[int, int]:
            # 9-slice: corners map 1:1, edges repeat the middle row/column
            sx = x if x < bw else (grid - (w - x)) if x >= w - bw else bw
            sy = y if y < bw else (grid - (h - y)) if y >= h - bw else bw
            return sx, sy

        for y in range(h):
            for x in range(w):
                if bw <= x < w - bw and bw <= y < h - bw:
                    continue  # centre: left to the fill
                c = cells.get(src(x, y))
                if c:
                    self.px(x0 + x, y0 + y, c)
        return bw

    def scaled(self, scale: int) -> Image.Image:
        w, h = self.img.size
        return self.img.resize((w * scale, h * scale), Image.NEAREST)


# ---------------------------------------------------------------------------
# Sprites
# ---------------------------------------------------------------------------

def load_fire_frames() -> list[Image.Image]:
    sheet = Image.open(FIRE_SHEET).convert("RGBA")
    cols, rows = sheet.size[0] // FIRE, sheet.size[1] // FIRE
    return [sheet.crop((c * FIRE, r * FIRE, (c + 1) * FIRE, (r + 1) * FIRE))
            for r in range(rows) for c in range(cols)]


def pixelate_avatar(im: Image.Image, size: int, t: dict, colors: int = 48) -> Image.Image:
    """Square crop, sharp downscale, then a saturation/contrast lift so the
    bright bits that a box filter would average into mud stay vivid."""
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    bg = Image.new("RGBA", im.size, hex_to_rgba(t["surface_alt"]))
    im = Image.alpha_composite(bg, im).convert("RGB")
    im = im.resize((size, size), Image.LANCZOS)
    im = ImageEnhance.Color(im).enhance(1.35)
    im = ImageEnhance.Contrast(im).enhance(1.15)
    if colors:
        im = im.quantize(colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    return im.convert("RGBA")


# ---------------------------------------------------------------------------
# Badge layout
# ---------------------------------------------------------------------------

def render_badge(stats: Stats, t: dict, avatar: Image.Image | None = None,
                 fire_frame: Image.Image | None = None, frame_style: str = "risen") -> Canvas:
    pad = 5                                           # horizontal padding of the card
    well_pad = 3                                      # equal gap from the well to the top, left and bottom edges
    gap = 6
    bw_outer = _frame_map(frame_style, "#000", "#fff")[1]

    name = stats.username
    brand = "@tryhackme"
    level_line = f"{stats.level_hex} [{stats.level_title}]"
    top_line = f"top {stats.top_percentage}%" if stats.top_percentage else ""
    streak = f"{stats.streak}"

    # Column widths (native px)
    well = FIRE - well_pad * 2                        # avatar well sits inside the fire-high section
    text_w = max(len(name) * CHAR_W + CHAR_W + len(brand) * CHAR_W,
                 len(level_line) * CHAR_W + (CHAR_W * 2 + len(top_line) * CHAR_W if top_line else 0))
    streak_w = FIRE + 2 + len(streak) * CHAR_W * BIG

    row = [(f"{stats.points}", "pts"), (f"{stats.rooms}", "rooms"),
           (f"{stats.badges}", "badges"), (f"#{stats.rank}", "rank")]
    sep = 2
    band_w = (sum(len(v) + 1 + len(l) for v, l in row) + sep * (len(row) - 1)) * CHAR_W

    left_w = (well_pad + well + gap) if avatar is not None else pad   # no avatar: no well at all
    inner_w = max(left_w + text_w + gap * 2 + streak_w, band_w) + pad * 2
    width = inner_w + bw_outer * 2

    top_h = FIRE                                      # top section is exactly one fire frame tall
    band_h = 4 + CAP_H + 4                            # stats band, accent colour
    height = bw_outer + top_h + 1 + band_h + bw_outer # +1 for the divider line

    # Roles, in the app's own terms: --g-primary is the action colour the app
    # exposes as --color-accent (Famicom's #a71c1c red), --g-accent is the
    # secondary "personality" colour (--color-secondary).
    name_col = pick(t, ["primary", "primary_hover", "accent"])
    level_col = pick(t, ["accent", "primary_hover", "primary"])
    muted_col = pick(t, ["text_muted"])
    streak_col = pick(t, ["primary", "primary_hover", "accent"])
    band_bg = t["primary"]
    band_val = t["primary_text"]
    band_lbl = mix(t["primary_text"], band_bg, 0.35)

    c = Canvas(width, height)
    # Top section fill first, then band, then the frame over both.
    x0, y0 = 0, 0
    c.rect(bw_outer, bw_outer, width - 1 - bw_outer, bw_outer + top_h - 1, t["surface"])
    y_div = bw_outer + top_h
    c.hline(bw_outer, width - 1 - bw_outer, y_div, "#000000")
    c.rect(bw_outer, y_div + 1, width - 1 - bw_outer, height - 1 - bw_outer, band_bg)
    c.pixel_frame(x0, y0, width - 1, height - 1, t, frame_style)

    # Avatar well: sunken PixelFrame with the same gap to the top, left and bottom.
    # Without an avatar there is no well and the text starts at the left pad.
    if avatar is not None:
        wx, wy = bw_outer + well_pad, bw_outer + well_pad
        bw_well = c.pixel_frame(wx, wy, wx + well - 1, wy + well - 1, t, "sunken", fill=t["surface_alt"])
        c.blit_image(wx + bw_well, wy + bw_well, pixelate_avatar(avatar, well - bw_well * 2, t))

    # Name / level: two 11px rows at a 13px pitch. Together they span exactly
    # the avatar well's height (26px); descenders of row 1 clear the brackets
    # of row 2 by one pixel. Departure Mono is only crisp at multiples of
    # 11px, so a smaller second row is not an option with this font.
    tx = bw_outer + left_w
    y1 = bw_outer + well_pad
    y2 = y1 + 13
    x = c.text(tx, y1, name, name_col)
    c.text(x + CHAR_W, y1 + 1, brand, muted_col)
    x = c.text(tx, y2, level_line, level_col)
    if top_line:
        c.text(x + CHAR_W * 2, y2, top_line, muted_col)

    # Streak: fire sprite spanning the whole section height, digits centred on it
    sx = width - bw_outer - pad - streak_w
    fy = bw_outer
    if fire_frame is not None:
        c.blit_image(sx, fy, fire_frame)
    c.text(sx + FIRE + 2, fy + (FIRE - CAP_H * BIG) // 2, streak, streak_col, mult=BIG)

    # Stats band
    x = bw_outer + pad
    yb = y_div + 1 + 4
    for i, (val, lbl) in enumerate(row):
        x = c.text(x, yb, val, band_val)
        x = c.text(x + CHAR_W, yb, lbl, band_lbl)
        if i < len(row) - 1:
            x += sep * CHAR_W
    return c


# ---------------------------------------------------------------------------
# Animation: looping GIF + spritesheet with a CSS steps() snippet
# ---------------------------------------------------------------------------

def render_frames(stats: Stats, t: dict, avatar, fire: list[Image.Image],
                  scale: int, frame_style: str) -> list[Image.Image]:
    return [render_badge(stats, t, avatar, f, frame_style).scaled(scale) for f in fire]


def save_gif(frames: list[Image.Image], path: Path, t: dict):
    """GIF with 1-bit transparency: the frame's notched corners stay see-through.
    Every pixel in the badge is either fully opaque or fully transparent, so
    nothing is lost. Palette index 255 is reserved for transparency."""
    # One global palette built from every frame stacked, so Pillow can store
    # each frame as the small rectangle that changed (the fire) instead of a
    # full redraw.
    w, h = frames[0].size
    stack = Image.new("RGBA", (w, h * len(frames)), (0, 0, 0, 0))
    for i, fr in enumerate(frames):
        stack.alpha_composite(fr, (0, i * h))
    rgb_stack = Image.alpha_composite(Image.new("RGBA", stack.size, (0, 0, 0, 255)), stack).convert("RGB")
    master = rgb_stack.quantize(255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    palette = (master.getpalette() + [0] * 768)[:765] + [0, 0, 0]
    flat = []
    for fr in frames:
        alpha = fr.getchannel("A")
        rgb = Image.alpha_composite(Image.new("RGBA", fr.size, (0, 0, 0, 255)), fr).convert("RGB")
        pal = rgb.quantize(palette=master, dither=Image.Dither.NONE)
        pal.putpalette(palette)
        pal.paste(255, mask=alpha.point(lambda a: 255 if a == 0 else 0))
        flat.append(pal)
    flat[0].save(path, save_all=True, append_images=flat[1:], duration=FRAME_MS, loop=0,
                 disposal=1, transparency=255, optimize=False)


def save_spritesheet(frames: list[Image.Image], path: Path, css_path: Path, url_hint: str):
    """Horizontal strip, one frame per column, alpha preserved. The CSS file
    animates it with steps() so a site can use it without a GIF."""
    w, h = frames[0].size
    sheet = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, fr in enumerate(frames):
        sheet.alpha_composite(fr, (w * i, 0))
    sheet.save(path, optimize=True)
    n = len(frames)
    css = f"""/* thm-badge-generator spritesheet: {n} frames, {w}x{h} each, {FRAME_MS} ms per frame.
   Markup: <a class="thm-badge" href="https://tryhackme.com/p/USERNAME" aria-label="TryHackMe profile"></a> */
.thm-badge {{
  display: block;
  width: {w}px;
  height: {h}px;
  background: url("{url_hint}") 0 0 / {w * n}px {h}px no-repeat;
  image-rendering: pixelated;
  animation: thm-badge-loop {FRAME_MS * n}ms steps({n}) infinite;
}}
@keyframes thm-badge-loop {{
  to {{ background-position: -{w * n}px 0; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .thm-badge {{ animation: none; }}
}}
"""
    css_path.write_text(css)


# ---------------------------------------------------------------------------
# Contact sheet of every theme (for the repo README)
# ---------------------------------------------------------------------------

def render_sheet(stats: Stats, themes: dict, avatar, fire0, scale: int, frame_style: str) -> Image.Image:
    names = [k for k in themes if not k.startswith("_")]
    cards = {(n, m): render_badge(stats, themes[n][m], avatar, fire0, frame_style).img
             for n in names for m in ("light", "dark")}
    cw = max(im.size[0] for im in cards.values())
    ch = max(im.size[1] for im in cards.values())
    gutter, label_h = 10, 14
    col_w = cw + gutter * 2
    row_h = ch + label_h + gutter
    sheet = Image.new("RGBA", (col_w * 2, row_h * len(names)), (0, 0, 0, 0))
    d = ImageDraw.Draw(sheet)
    d.rectangle([0, 0, col_w - 1, sheet.size[1]], fill=(246, 248, 250, 255))
    d.rectangle([col_w, 0, col_w * 2, sheet.size[1]], fill=(13, 17, 23, 255))
    d.fontmode = "1"
    font = ImageFont.truetype(str(FONT_PATH), FONT_PX)
    for r, n in enumerate(names):
        for ci, mode in enumerate(("light", "dark")):
            x, y = ci * col_w + gutter, r * row_h + gutter
            col = (60, 60, 60, 255) if mode == "light" else (160, 160, 160, 255)
            d.text((x, y - 2), f"{themes[n]['label']} / {mode}", font=font, fill=col)
            sheet.alpha_composite(cards[(n, mode)], (x, y + label_h))
    w, h = sheet.size
    return sheet.resize((w * scale, h * scale), Image.NEAREST)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--username", help="TryHackMe username to fetch")
    src.add_argument("--stats", type=Path, help="render from a saved stats JSON instead of fetching")
    ap.add_argument("--theme", default="adventure", help="theme name from themes.json (default: adventure, Gribble's default)")
    ap.add_argument("--all-themes", action="store_true", help="render every theme")
    ap.add_argument("--mode", choices=["light", "dark", "both"], default="both")
    ap.add_argument("--frame", choices=FRAME_STYLES, default="risen", help="outer PixelFrame style (default risen)")
    ap.add_argument("--scale", type=int, default=2, help="integer upscale factor (default 2)")
    ap.add_argument("--out", type=Path, default=HERE / "badges", help="output directory")
    ap.add_argument("--no-avatar", action="store_true", help="render without the avatar well")
    ap.add_argument("--avatar-file", type=Path, help="use this image as the avatar instead of fetching")
    ap.add_argument("--sheet", action="store_true", help="also render badges/_themes.png with every theme")
    ap.add_argument("--animate", action="store_true",
                    help="also write thm-<theme>-<mode>.gif, a -sprite.png strip and a .css snippet")
    ap.add_argument("--sprite-url", default="",
                    help="URL the generated CSS should point at (default: the sprite file name)")
    args = ap.parse_args(argv)

    themes = json.loads(THEMES_PATH.read_text())
    if not args.all_themes and args.theme not in themes:
        names = ", ".join(k for k in themes if not k.startswith("_"))
        ap.error(f"unknown theme '{args.theme}'. Available: {names}")

    args.out.mkdir(parents=True, exist_ok=True)

    avatar_bytes: bytes | None = None
    if args.username:
        cache = args.out / "stats.json"
        try:
            try:
                data = fetch_stats(args.username)
            except ChallengeError as exc:
                print(f"warning: {exc}; switching to headless browser", file=sys.stderr)
                data, avatar_bytes = fetch_via_browser(args.username)
            cache.write_text(json.dumps(data, indent=2) + "\n")
        except Exception as exc:
            if not cache.exists():
                raise
            print(f"warning: fetch failed ({exc}); rendering from cached {cache}", file=sys.stderr)
            data = json.loads(cache.read_text())
            data = data.get("data", data)
    else:
        data = json.loads(args.stats.read_text())
        data = data.get("data", data)
    stats = Stats.from_api(data)

    avatar = None
    if args.avatar_file:
        avatar = Image.open(args.avatar_file).convert("RGBA")
    elif args.no_avatar:
        pass
    elif avatar_bytes:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    elif stats.avatar_url:
        avatar = fetch_avatar(stats.avatar_url)
    fire = load_fire_frames()
    # static badge shows the fullest frame; the GIF/sheet loop through all of them
    fire0 = max(fire, key=lambda f: sum(1 for px in f.get_flattened_data() if px[3] > 0))

    theme_names = [k for k in themes if not k.startswith("_")] if args.all_themes else [args.theme]
    modes = ["light", "dark"] if args.mode == "both" else [args.mode]
    written = []
    for name in theme_names:
        for mode in modes:
            t = themes[name][mode]
            path = args.out / f"thm-{name}-{mode}.png"
            render_badge(stats, t, avatar, fire0, args.frame).scaled(args.scale).save(path, optimize=True)
            written.append(path)
            if args.animate:
                frames = render_frames(stats, t, avatar, fire, args.scale, args.frame)
                gif = args.out / f"thm-{name}-{mode}.gif"
                strip = args.out / f"thm-{name}-{mode}-sprite.png"
                css = args.out / f"thm-{name}-{mode}-sprite.css"
                save_gif(frames, gif, t)
                save_spritesheet(frames, strip, css, args.sprite_url or strip.name)
                written += [gif, strip, css]
    # Ready-to-paste embed: the image itself cannot carry a link, the <a> does.
    base = args.sprite_url.rsplit("/", 1)[0] + "/" if "/" in args.sprite_url else ""
    first = args.theme if args.theme in theme_names else theme_names[0]
    light, dark = f"thm-{first}-light", f"thm-{first}-dark"
    ext = ".gif" if args.animate else ".png"
    snippet = f"""<!-- thm-badge-generator: paste into your README. Light/dark follow the viewer's GitHub theme. -->
<a href="https://tryhackme.com/p/{stats.username}">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="{base}{dark}{ext}">
    <img alt="TryHackMe: {stats.username}, {stats.level_hex} {stats.level_title}, {stats.streak} day streak" src="{base}{light}{ext}" width="{render_badge(stats, themes[first]['light'], avatar, fire0, args.frame).img.size[0] * args.scale}">
  </picture>
</a>
"""
    (args.out / "embed.html").write_text(snippet)
    written.append(args.out / "embed.html")
    if args.sheet:
        path = args.out / "_themes.png"
        render_sheet(stats, themes, avatar, fire0, args.scale, args.frame).save(path, optimize=True)
        written.append(path)
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())