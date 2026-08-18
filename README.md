[README.md](https://github.com/user-attachments/files/31176694/README.md)
# merge_manga_spreads

Turn a folder (or a whole shelf) of individual manga page images into landscape two-page spreads — reviewed and adjusted in your browser, packaged straight into `.cbz` files.

Built for the common case where a scanlated or digital chapter is a folder of `page1.png, page2.png, ...` and you want it readable in landscape/double-page mode, without every page blindly getting glued to its neighbor — covers, ads, and splash pages need to stay solo, and pages that are *already* a merged spread need to be left alone.

---

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Two ways to run it](#two-ways-to-run-it)
  - [Single folder](#single-folder)
  - [Batch (multiple chapters at once)](#batch-multiple-chapters-at-once)
- [The interactive review tool](#the-interactive-review-tool)
  - [The folder checklist (batch only)](#the-folder-checklist-batch-only)
  - [Reading a review card](#reading-a-review-card)
  - [The toolbar controls](#the-toolbar-controls)
  - [Solo vs. Exclude](#solo-vs-exclude)
  - [Finishing up](#finishing-up)
- [Working with existing .cbz / .zip files](#working-with-existing-cbz--zip-files)
- [How the pairing logic works](#how-the-pairing-logic-works)
- [Where output files go](#where-output-files-go)
- [Full CLI reference](#full-cli-reference)
- [Recipes](#recipes)
- [Tips](#tips)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure-for-contributors)

---

## What it does

1. Reads a folder of page images (or an existing `.cbz`/`.zip`), sorted in natural order (`page2` before `page10`).
2. Pairs them two at a time into landscape spreads, right-to-left (page *N* on the right, page *N+1* on the left) — matching traditional manga reading order.
3. Automatically detects pages that are **already** a merged two-page spread (by aspect ratio) and leaves them alone instead of pairing them with a neighbor.
4. Optionally opens a browser tab so you can **see** the pairing it's about to produce and fix anything by hand — force a page to stay solo, drop a page entirely (ads, duplicate scans), or just look closer at anything ambiguous.
5. Writes the result as image files and/or a compressed `.cbz`, one per chapter, without ever touching your source files.
6. Can do this for one chapter, or for an entire folder of chapters (and/or existing `.cbz` volumes) in one run.

---

## Requirements

- Python 3.9+
- [Pillow](https://python-pillow.org/) (`pip install pillow`)

Everything else (the browser review tool, the local server, `.cbz` handling) is the Python standard library — no other dependencies, no build step.

## Installation

```bash
pip install pillow
```

Download `merge_manga_spreads.py` and run it with `python` / `python3`. That's the whole install.

---

## Quick start

Review one chapter and get a `.cbz` out of it:

```bash
python merge_manga_spreads.py -i "path/to/chapter/pages" -o "path/to/output" --review --cbz
```

This opens your browser, shows you the spreads it's about to make, lets you fix anything, and finishes automatically in the terminal once you hit **Continue**.

Process an entire folder of chapters (subfolders and/or `.cbz` files) in one go:

```bash
python merge_manga_spreads.py --batch "path/to/series/folder" --review --cbz
```

---

## Two ways to run it

### Single folder

```bash
python merge_manga_spreads.py -i INPUT -o OUTPUT [options]
```

`INPUT` can be:
- a folder full of page images, or
- a `.cbz` / `.zip` file directly.

This processes exactly one chapter/volume.

### Batch (multiple chapters at once)

```bash
python merge_manga_spreads.py --batch PARENT_DIR [options]
```

`PARENT_DIR` is a folder that contains, directly inside it, any mix of:
- subfolders full of page images (one subfolder per chapter), and/or
- `.cbz` / `.zip` files (one per volume/chapter).

The script scans `PARENT_DIR`, and:

- **With `--review`:** opens one browser tab. If there's more than one chapter, you first get a checklist to pick which ones to include, then it walks you through reviewing each selected chapter in turn — one at a time, in the same tab, automatically advancing. A `.cbz` is generated for each chapter as soon as its review is confirmed, so earlier chapters are already safely written even if you stop partway through.
- **Without `--review`:** processes every discovered chapter immediately with plain sequential pairing (no browser, no per-page control) — useful for a quick batch conversion when you already know a series doesn't need manual fixes.
- **With `--dry-run`:** prints the pairing plan for every discovered chapter without writing anything or opening a browser.

`-i`/`--input` is ignored in batch mode (each chapter's own folder/archive is used instead).

---

## The interactive review tool

Passing `--review` starts a small local web server (bound to `127.0.0.1` only — nothing is exposed to your network) and opens it in your default browser. Everything happens on your machine; no images are uploaded anywhere.

### The folder checklist (batch only)

If `--batch` found more than one chapter, you land here first: every discovered subfolder and `.cbz`/`.zip` is listed with its page count, and a small `CBZ` badge marks archive-sourced entries. Everything is checked by default — **Select all** / **Select none** are there for convenience. Hit **Start Review →** to begin.

### Reading a review card

Each card in the review list represents one output image, in final reading order. There are three kinds:

| Card | Meaning |
|---|---|
| **Spread** (no colored left edge) | Two pages that will be merged side by side. |
| **Solo page** (red left edge) | A single page that will be output on its own — either because it was forced solo, or because it had no partner left after pairing (e.g. a trailing odd page). |
| **Already merged** (blue left edge, `AUTO SPREAD` badge) | A page whose width/height ratio already looks like a two-page spread; passed through untouched rather than paired with a neighbor. |

By default, the first few cards (see **Full preview: first**, below) show the whole page. Every card after that shows only a thin strip near where the two pages would meet — enough to judge whether the artwork actually continues across the seam, without spoiling the rest of the page. Click **Show full** on any cropped card to see that specific page in full; it stays expanded until the next chapter loads.

### The toolbar controls

All of these are live — the list re-renders instantly as you change them — and, in batch mode, whatever you land on carries over as the starting point for the *next* chapter, so you only tune them once per session.

| Control | What it does |
|---|---|
| **Thumbnail size** | How tall each preview image renders (150–700px). Turn this up on a large/high-DPI monitor. Display-only — never affects the generated files. |
| **Auto-spread ratio ≥** | The width/height ratio at which a page is treated as an already-merged spread (default `1.15`). Raise it if normal pages are being wrongly flagged as spreads; lower it if genuine spreads aren't being detected. |
| **Seam preview width** | How much of each page (as a %) the cropped preview reveals near the seam (default `25%`). |
| **Full preview: first** | How many leading cards get the full, uncropped preview before cropping kicks in (default `3` — covers, ads, and title pages usually live in the first few spreads of a volume). |
| **Reset selections** | Clears every solo/exclude choice you've made *for the current chapter*. |

### Solo vs. Exclude

Every real page gets two buttons:

- **Keep solo** — this page will never be paired with a neighbor; it's output as its own landscape-canvas image, at its native size (not padded). Click a page's thumbnail (or its crop preview) as a shortcut for the same toggle.
- **Exclude** — this page is dropped from the output *entirely*: not paired, not shown solo, just skipped. Useful for ads, duplicate/blank scans, or anything you don't want in the final book. **This only removes it from this run's output — your original source file is never touched, moved, or deleted.**

Marking a page Exclude automatically clears any Solo flag on it (and vice versa) — the two are mutually exclusive per page.

`--force-single` (and `--force-single-file`) works alongside `--review`, not just as an alternative to it: anything they specify shows up already checked in the browser, still fully editable. In `--batch`, it's re-resolved fresh against every chapter's own pages as you reach them — so `--force-single 1` acts as a standing "page 1 is always the cover" preference across an entire series, the same way `--cover-alone` already does, without one chapter's manual clicks leaking into the next.

### Finishing up

**Continue →** locks in your choices for the current chapter and either advances to the next one (batch mode) or finishes the run. **Cancel** stops the whole run; any chapters already completed before you cancelled are kept, nothing after that point is generated.

---

## Working with existing .cbz / .zip files

Both single-folder and batch mode accept `.cbz`/`.zip` files directly, wherever a folder of pages would otherwise go:

```bash
# a single existing volume
python merge_manga_spreads.py -i "Hirayasumi v01.cbz" -o out --review --cbz

# a folder full of them (or a mix of folders and .cbz files)
python merge_manga_spreads.py --batch "Hirayasumi/" --review --cbz
```

What happens under the hood: the archive is extracted to a temporary folder, processed exactly like any other folder of pages, and that temp folder is deleted again once its output is written (in batch mode, only one chapter's worth of extracted images ever exists on disk at a time). Any internal folder structure inside the archive is flattened, and non-image entries (like a `ComicInfo.xml` metadata file) are ignored.

The output filename always gets `" [spreads]"` appended before `.cbz` when the source was an archive — e.g. `Hirayasumi v01.cbz` → `Hirayasumi v01 [spreads].cbz` — specifically so the output can never silently overwrite your original file, and so it's obvious at a glance which one is the converted version.

`.cbr` (RAR-based comic archives) is **not** supported — only `.cbz`/`.zip`.

---

## How the pairing logic works

1. **Sort.** Pages are sorted in natural order by filename (`page2.png` before `page10.png`, not string order).
2. **Exclude.** Any page you've marked Exclude is filtered out completely, as if it didn't exist for the rest of this process.
3. **Classify what's left** into two groups:
   - **Specials** — pages that won't be paired: anything auto-detected as an already-merged spread (wide aspect ratio), anything you forced solo, and the first page if `--cover-alone` is set.
   - **Normals** — everything else.
4. **Pair the normals**, two at a time, in the order they appear — completely ignoring where the specials sit. The first page of each pair is the one encountered first (shown on the **right**, since that's read first in right-to-left order); the second is shown on the **left**.
5. **Re-merge** everything back into final reading order by original position. This means a normal page can "reach across" a solo'd/already-merged page to find its actual next partner, instead of being stranded alone just because something else nearby was pulled out of the pairing rotation.

A worked example — pages `1..6`, with page `3` forced solo and page `5` already a merged spread:

```
Input:   1  2  3(solo)  4  5(already merged)  6
Normals (specials removed): 1, 2, 4, 6  →  paired as (1,2) and (4,6)
Specials, kept at their own position:     3(solo),  5(passthrough)

Output, in final order:
  spread_001: pages 2+1 (a real spread)
  spread_002: page 3, alone
  spread_003: page 4 + page 6 (paired across the gap left by page 5)
  spread_004: page 5, passed through untouched
```

Solo pages are written at their **native size** (not padded to a landscape canvas) — a forced-solo page keeps whatever aspect ratio it already had.

---

## Where output files go

| Mode | Spread images | `.cbz` |
|---|---|---|
| Single folder | `<OUTPUT>/` | `<basename of OUTPUT>.cbz`, written in the current directory |
| Single archive (`-i some.cbz`) | `<OUTPUT>/` | `<basename of OUTPUT> [spreads].cbz` |
| Batch, no `-o` | `<chapter_folder>/_spreads/` | `<batch_parent>/<chapter_name>.cbz` |
| Batch, archive-sourced, no `-o` | temp folder (auto-deleted) | `<batch_parent>/<chapter_name> [spreads].cbz` |
| Batch, with `-o OUTPUT` | `<OUTPUT>/<chapter_name>/` | `<OUTPUT>/<chapter_name>.cbz` (+ `[spreads]` if archive-sourced) |

Add `--cleanup-images` to delete the loose spread image files after a successful `.cbz` is built, leaving just the `.cbz`. This never touches your original source pages — only the images this script just generated.

---

## Full CLI reference

| Flag | Default | Description |
|---|---|---|
| `-i`, `--input` | — | Input folder of pages, or a `.cbz`/`.zip` file. Single-folder mode only. |
| `-o`, `--output` | — | Output folder (single-folder mode) or output parent folder (batch mode). |
| `--batch PARENT_DIR` | — | Batch mode: process every subfolder/archive inside `PARENT_DIR`. |
| `--review` | off | Open the browser review tool before generating anything. |
| `-g`, `--gap` | `0` | Pixel gap between the two pages of a spread. |
| `--gap-color` | `255,255,255` | Gap/background color as `R,G,B`. |
| `--cover-alone` | off | Keep the first page solo (as a cover); pairing starts from page 2+3. |
| `--aspect-threshold` | `1.15` | Width/height ratio at or above which a page is treated as an already-merged spread. |
| `--crop-percent` | `25` | % of each page shown near the seam in the cropped review preview. |
| `--full-preview-count` | `3` | Number of leading review cards shown at full size before cropping starts. |
| `--thumb-size` | `300` | Review thumbnail height in pixels. Display only. |
| `--force-single` | — | Comma-separated pages to force solo (filenames or 1-based page numbers). Also pre-populates `--review` (still editable in the browser); in `--batch`, re-applied fresh to every chapter, so it works as a standing preference across a whole series. |
| `--force-single-file` | — | Text file listing more forced-solo pages, one per line. |
| `--format` | `jpg` | Output image format: `jpg`, `png`, or `webp`. |
| `--quality` | `85` | JPEG/WebP quality, 1–100. |
| `--cbz` | off | Package the output into a `.cbz` archive. |
| `--cleanup-images` | off | Delete the loose spread images after a successful `.cbz` (keeps only the `.cbz`). |
| `--dry-run` | off | Print the pairing plan without writing any files. |

Supported input image formats: `.png .jpg .jpeg .webp .bmp .tiff`. Supported archive formats: `.cbz .zip`.

---

## Recipes

Review one chapter, force nothing manually beforehand, JPEG quality 90:

```bash
python merge_manga_spreads.py -i pages -o out --review --cbz --quality 90
```

Convert an entire series folder (mix of loose-page subfolders and `.cbz` volumes) with no review, PNG output, keeping only the final archives:

```bash
python merge_manga_spreads.py --batch "MySeries/" --cbz --format png --cleanup-images
```

Preview the pairing plan for a whole series without generating anything:

```bash
python merge_manga_spreads.py --batch "MySeries/" --dry-run
```

Keep page 1 as a solo cover, add a thin white gutter between paired pages:

```bash
python merge_manga_spreads.py -i pages -o out --cover-alone --gap 12 --cbz
```

Force specific pages solo without opening the browser (e.g. scripted/repeat runs):

```bash
python merge_manga_spreads.py -i pages -o out --force-single 1,14,27 --cbz
```

Review a whole series, with page 1 auto-checked as solo in every chapter (still adjustable per chapter in the browser):

```bash
python merge_manga_spreads.py --batch "MySeries/" --review --force-single 1 --cbz
```

---

## Tips

- **Cut the typing down to one word.** Add a shell alias/function so a full run is just one word plus a path:

  ```bash
  # ~/.bashrc or ~/.zshrc
  mangaspread() { python3 /path/to/merge_manga_spreads.py -i "$1" -o "${2:-out}" --review --cbz; }
  ```

  Then it's just `mangaspread ~/Downloads/chapter12`.

- **Large/high-DPI monitor?** Bake your preferred size into the same alias: `--thumb-size 420`.

- **Re-running a batch is safe.** Nothing here deletes or modifies source files; if you cancel partway through, just run the same command again and pick the remaining chapters from the checklist.

---

## Limitations

- `.cbr` (RAR-based comic archives) isn't supported — only `.cbz`/`.zip`.
- `ComicInfo.xml` or other embedded metadata inside a source `.cbz` is ignored, not carried over to the output (the page count alone would be wrong after merging, since spreads roughly halve it).
- Already-merged-spread detection is purely aspect-ratio based; it can't distinguish "genuinely a two-page spread" from "a portrait page that just happens to be unusually wide" — adjust `--aspect-threshold` if it's misfiring on a particular series.
- The review server binds to `127.0.0.1` only and is meant for local, single-user use — it isn't hardened for exposure beyond your own machine.

## Troubleshooting

**Browser doesn't open automatically.** The terminal always prints the URL (`http://127.0.0.1:PORT/`) — copy it into any browser manually.

**"No supported images found."** Check the folder actually contains files with one of the supported extensions, and that you're pointing at the folder *containing* the images, not a parent of it.

**A page's aspect ratio isn't being detected correctly.** Adjust the **Auto-spread ratio ≥** slider live in the review tool, or pass `--aspect-threshold` on the command line.

**Batch mode isn't finding my chapters.** `--batch` only looks *directly inside* the folder you point it at — chapters need to be immediate subfolders or `.cbz`/`.zip` files there, not nested further down.

---

## Project structure (for contributors)

Everything lives in the single `merge_manga_spreads.py` file:

- `collect_pages`, `natural_sort_key` — find and naturally sort page images in a folder.
- `discover_batch_folders` — find chapter subfolders and archives inside a `--batch` parent.
- `is_already_spread` — aspect-ratio check for already-merged spreads.
- `extract_archive`, `resolved_folder` — `.cbz`/`.zip` extraction and cleanup.
- `build_output_plan` — the core pairing algorithm (see [How the pairing logic works](#how-the-pairing-logic-works)); mirrored exactly in the browser-side JavaScript so the live preview always matches what generation will actually produce.
- `make_spread`, `generate_outputs` — render and save the actual spread images, and package them into a `.cbz`.
- `run_batch_review_server` — the local HTTP server and state machine behind `--review` (folder selection → per-chapter review → generation, looped).
- `PICKER_HTML` — the entire browser UI (HTML/CSS/JS), served directly from memory — no separate template files.
- `main` — CLI argument parsing and dispatch between single-folder, batch, interactive, and non-interactive modes.
