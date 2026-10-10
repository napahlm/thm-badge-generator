# thm-badge-generator

A pixel-art TryHackMe profile badge for your GitHub README, drawn in the [Gribble](https://gribble.app) pixel editor style and refreshed every day by a GitHub Action.

<a href="https://tryhackme.com/p/napahlm">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/napahlm/thm-badge-generator/main/badges/thm-adventure-dark.gif">
    <img alt="TryHackMe badge for napahlm" src="https://raw.githubusercontent.com/napahlm/thm-badge-generator/main/badges/thm-adventure-light.gif" width="644">
  </picture>
</a>

Level, points, rooms, badges, global rank and streak come from your public TryHackMe profile.
The card uses [Departure Mono](https://departuremono.com), Gribble's PixelFrame bevels and ten Gribble themes, each in light and dark.
The streak fire is animated.

![all themes](badges/_themes.png)

## Setup

You only touch one repository: your **profile repository**, the one named after your GitHub user (`github.com/YOUR-GITHUB-USERNAME/YOUR-GITHUB-USERNAME`).
You never need to fork or clone this one.

Two values are yours to fill in, and they appear in upper case below:

- `YOUR-THM-USERNAME`: your TryHackMe username, the last part of `https://tryhackme.com/p/...`
- `YOUR-GITHUB-USERNAME`: your GitHub username

### 1. Add the workflow

In your profile repository, create `.github/workflows/thm-badge.yml`:

```yaml
name: TryHackMe badge
on:
  schedule:
    - cron: "23 4 * * *"   # pick any minute and hour you like, in UTC
  workflow_dispatch:
permissions:
  contents: write
jobs:
  badge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: napahlm/thm-badge-generator@v1
        with:
          username: YOUR-THM-USERNAME
          theme: adventure          # see the theme list below
```

### 2. Run it once

Open the Actions tab of your profile repository, pick "TryHackMe badge", and click "Run workflow".
After about a minute the action commits a `badges/` folder to your repository with the rendered files.
From then on it runs by itself on the schedule.
Every run that changes the badge adds a commit by `thm-badge-bot`; runs with no change add nothing.

### 3. Put it in your README

Open `badges/embed.html` in your profile repository and paste its contents into your `README.md`.
It is generated on every run with your username, your theme, the right file names and the right width, so it is always correct for your setup.

For reference, it looks like this:

```html
<a href="https://tryhackme.com/p/YOUR-THM-USERNAME">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/YOUR-GITHUB-USERNAME/main/badges/thm-THEME-dark.gif">
    <img alt="TryHackMe badge" src="https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/YOUR-GITHUB-USERNAME/main/badges/thm-THEME-light.gif" width="644">
  </picture>
</a>
```

Two things to know if you write it by hand instead:

- `THEME` must match the `theme:` in your workflow exactly; the files are named after it (`thm-famicom-dark.gif` for `famicom`), and a mismatch gives a broken image.
- Use the full `raw.githubusercontent.com` URLs, not relative paths. GitHub rewrites a relative `src` on `<img>` but not a relative `srcset` on `<source>`, so a relative version breaks in dark mode.

The `<a>` makes the whole badge a link to your profile.
The `<picture>` switches between light and dark to match the viewer's GitHub theme.
Use `.png` instead of `.gif` for a still badge.

## Options

All of these go under `with:` in the workflow.

| input | default | what it does |
| --- | --- | --- |
| `username` | required | TryHackMe username |
| `theme` | `adventure` | `adventure`, `famicom`, `bloodmoon`, `nord`, `retrowave`, `retropc`, `gameboy`, `commodore`, `dungeon`, `ocean` |
| `mode` | `both` | `light`, `dark` or `both` |
| `scale` | `2` | integer upscale; the native card is about 322x53 px |
| `animate` | `true` | also write the GIF, the spritesheet and its CSS |
| `frame` | `risen` | outer frame style: `risen`, `sunken`, `normal`, `blackBorder` |
| `out_dir` | `badges` | where the files go in your repository |
| `extra_args` | | extra flags, for example `--no-avatar` |
| `commit` | `true` | set to `false` to handle the commit yourself |

If you change `theme` later, copy the snippet from `badges/embed.html` again after the next run.

## On your own site

Next to each GIF the action writes `thm-THEME-MODE-sprite.png` (one row of 20 frames, transparent background) and a matching `.css` file with the absolute URL already filled in.
Include the CSS and add `<a class="thm-badge" href="https://tryhackme.com/p/YOUR-THM-USERNAME"></a>`.
The animation runs with `steps(20)`, keeps the pixels sharp, and stops under `prefers-reduced-motion`.

## Run it locally

```sh
pip install pillow
python thm_badge.py --username YOUR-THM-USERNAME
python thm_badge.py --username YOUR-THM-USERNAME --theme gameboy --mode dark --scale 3 --animate
python thm_badge.py --username YOUR-THM-USERNAME --all-themes --sheet
python demo_variants.py
```

`demo_variants.py` renders a sheet of edge cases (long names, 0 and 9999 streaks, every title) so layout changes can be checked without fetching anything.

## How it works

- Stats come from `https://tryhackme.com/api/v2/public-profile?username=<name>`, the JSON behind TryHackMe's own badge.
- The card is drawn at native pixel resolution with hard, unantialiased text and scaled up with nearest-neighbour.
- Frames are Gribble's PixelFrame pixel maps, 9-sliced the same way the app does it.
- Themes are generated from Gribble's theme CSS with `themes_from_css.py`.
- The avatar is downscaled to 22x22 and reduced to 48 colours so it reads as a sprite.
- If the fetch fails outright, the action renders from the previous run's `badges/stats.json` when there is one, and otherwise exits non-zero without committing, so your README keeps the last good badge.

## Credits

- Departure Mono by Helena Zhang and Tobias Fried, MIT, see `fonts/LICENSE-DepartureMono.txt`.
- Theme palettes and PixelFrame from [Gribble](https://gribble.app).
- Fire sprite from Minecraft, property of Mojang; used here as a decorative asset.

## Licence

MIT, except the files credited above, which keep their own terms.