#!/usr/bin/env python3
"""
merge_manga_spreads.py

Merges individual manga page images into landscape two-page spreads,
in traditional right-to-left reading order (page N+1 goes on the LEFT,
page N goes on the RIGHT, since the reader encounters the "right" page first).
Automatically creates a compressed .cbz archive if requested.

SINGLE FOLDER:
    python merge_manga_spreads.py -i INPUT_DIR -o OUTPUT_DIR --review --cbz

MULTIPLE FOLDERS (batch):
    python merge_manga_spreads.py --batch PARENT_DIR --cbz

    Point --batch at a parent folder containing one subfolder per chapter
    (each full of that chapter's page images), OR one .cbz/.zip per chapter
    (already-packaged volumes), OR a mix of both. The script finds them,
    shows a checklist in the browser so you can pick which to include, then
    walks through each one's review in sequence -- one browser tab, no shell
    loops, no repeated commands. A .cbz is generated for each chapter as its
    review completes. Archive-sourced chapters are extracted to a temp
    folder for processing and cleaned up automatically -- your original
    .cbz/.zip files are never modified, and the output is always written
    under a distinct name (" [spreads].cbz") so nothing gets overwritten.

Either way, --review opens a browser tab pre-loaded with your actual pages,
showing the pairing this script would currently produce. Click any page to
force it solo -- the preview updates live -- then hit Continue. The terminal
picks up automatically and finishes the job.

Options:
    -i, --input      Folder OR a .cbz/.zip file of individual page images
                      (single-folder mode)
    -o, --output     Output folder (single-folder mode) / output parent (batch mode)
    --batch PARENT   Batch mode: process every subfolder of PARENT that
                      contains images, each into its own .cbz
    --review         Open a browser tab to visually review/adjust pairing
                      before generating anything. Recommended.
    -g, --gap        Pixel gap between the two pages in a spread (default: 0)
    --gap-color      Gap/background color as R,G,B (default: 255,255,255)
    --cover-alone    Treat the first image as a solo cover page, not paired
                      with page 2 (common in manga volumes)
    --aspect-threshold  Width/height ratio at or above which an image is
                      considered an ALREADY-MERGED spread and is passed
                      through untouched (default: 1.15). Live-adjustable
                      in the browser too.
    --crop-percent    In review mode, how much of each page's width (as a %)
                      to show near the seam once past --full-preview-count,
                      for spoiler-light review. Live-adjustable. (default: 25)
    --full-preview-count  Number of leading output items shown at full size
                      in review mode -- covers, ads, etc. usually live here.
                      Everything after gets the cropped seam-only preview.
                      Live-adjustable. (default: 3)
    --thumb-size      Thumbnail height in pixels in review mode -- display
                      only, never affects generated output. Bump this up
                      for large/high-DPI monitors. Live-adjustable. (default: 300)
    --force-single    Comma-separated pages to force as solo (never paired
                      with a neighbor), even if they're portrait. Accepts
                      filenames (with or without extension) or 1-based page
                      numbers based on sorted order. Skip this if using
                      --review -- you can just click pages instead.
    --force-single-file  Path to a text file with additional forced-single
                      pages, one per line (or comma-separated), '#' comments
                      allowed. Combines with --force-single.
    --format         Output image format: 'jpg', 'png', or 'webp' (default: jpg)
    --quality        Image quality (1-100) if saving as jpg/webp (default: 85)
    --cbz            Package output into a compressed .cbz file
    --cleanup-images  After a successful .cbz, delete the loose spread image
                      files this script generated (keeps just the .cbz).
                      Never touches your original source pages.
    --dry-run        Just print the pairing plan without writing files
"""

import argparse
import http.server
import json
import os
import queue
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from contextlib import contextmanager

from PIL import Image

VALID_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
ARCHIVE_EXTS = {".cbz", ".zip"}
MIME_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".bmp": "image/bmp", ".tiff": "image/tiff",
}


def natural_sort_key(filename):
    """Split filename into text/number chunks so numbers sort numerically."""
    return [
        int(chunk) if chunk.isdigit() else chunk.lower()
        for chunk in re.split(r"(\d+)", filename)
    ]


def collect_pages(input_dir):
    files = [
        f for f in os.listdir(input_dir)
        if os.path.splitext(f)[1].lower() in VALID_EXTS
    ]
    files.sort(key=natural_sort_key)
    return files


def is_archive_file(path):
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in ARCHIVE_EXTS


def count_archive_pages(archive_path):
    """Peek inside a .cbz/.zip WITHOUT extracting, just to count valid image
    entries (used for the batch-discovery checklist). Returns 0 for a
    corrupt/unreadable archive rather than raising, so one bad file doesn't
    block discovery of everything else."""
    try:
        with zipfile.ZipFile(archive_path) as zf:
            return sum(
                1 for n in zf.namelist()
                if not n.endswith("/") and os.path.splitext(n)[1].lower() in VALID_EXTS
            )
    except (zipfile.BadZipFile, OSError):
        return 0


def extract_archive(archive_path, dest_dir):
    """
    Extract every valid page image inside a .cbz/.zip into dest_dir, flat
    (any internal folder structure in the archive is ignored -- only the
    filename is kept, matching how a plain folder of pages is expected to
    look). Non-image entries (ComicInfo.xml, thumbnails, etc.) are skipped.
    Returns the number of images extracted.
    """
    os.makedirs(dest_dir, exist_ok=True)
    seen_names = set()
    count = 0
    with zipfile.ZipFile(archive_path) as zf:
        for entry in zf.namelist():
            if entry.endswith("/"):
                continue
            ext = os.path.splitext(entry)[1].lower()
            if ext not in VALID_EXTS:
                continue
            base = os.path.basename(entry)
            if not base:
                continue
            out_name = base
            n = 1
            while out_name in seen_names:
                stem, e = os.path.splitext(base)
                out_name = f"{stem}_{n}{e}"
                n += 1
            seen_names.add(out_name)
            with zf.open(entry) as src, open(os.path.join(dest_dir, out_name), "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


@contextmanager
def resolved_folder(folder):
    """
    Yields a folder dict whose "path" is guaranteed to be a real, readable
    directory of images -- extracting to a fresh temp dir first if the
    source is a .cbz/.zip archive, and always cleaning that temp dir back
    up on exit (success, error, whatever). For a plain folder, just yields
    it unchanged (no extraction, no temp dir, nothing to clean up).

    Used by the non-interactive batch path and --dry-run, which each
    process one folder fully before moving to the next (no waiting period
    for browser review), so extract-use-cleanup can happen in one place.
    """
    if not folder.get("is_archive"):
        yield folder
        return
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", folder["name"])[:80] or "archive"
    temp_dir = tempfile.mkdtemp(prefix="mangaspread_" + safe_name + "_")
    try:
        print(f"  Extracting {os.path.basename(folder['path'])}...")
        extract_archive(folder["path"], temp_dir)
        working = dict(folder)
        working["path"] = temp_dir
        yield working
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def discover_batch_folders(parent_dir):
    """
    Find every chapter-like entry directly inside parent_dir:
      - subdirectories containing at least one valid page image, and/or
      - .cbz/.zip files containing at least one valid page image
    Returns a list of dicts: {"name", "path", "count", "is_archive"}.
    For archives, "path" is the archive FILE itself (not yet extracted --
    extraction happens lazily, only for chapters actually selected).
    """
    if not os.path.isdir(parent_dir):
        sys.exit(f"Error: --batch folder not found: {parent_dir}")

    entries = os.listdir(parent_dir)
    entries.sort(key=natural_sort_key)

    candidates = []
    for entry in entries:
        full_path = os.path.join(parent_dir, entry)
        if os.path.isdir(full_path):
            imgs = collect_pages(full_path)
            if imgs:
                candidates.append({"name": entry, "path": full_path, "count": len(imgs), "is_archive": False})
        elif is_archive_file(full_path):
            count = count_archive_pages(full_path)
            if count:
                name = os.path.splitext(entry)[0]
                candidates.append({"name": name, "path": full_path, "count": count, "is_archive": True})
    return candidates


def is_already_spread(filepath, aspect_threshold):
    """Return True if the image is wide enough to already be a merged spread."""
    with Image.open(filepath) as im:
        width, height = im.size
    return height > 0 and (width / height) >= aspect_threshold


def parse_force_single(raw_value, raw_file, pages):
    """
    Resolve --force-single / --force-single-file tokens into a set of
    exact filenames (matched against `pages`). Tokens may be:
      - an exact filename ("page5.png")
      - a filename without extension ("page5")
      - a 1-based page number based on sorted order ("5")
    Unrecognized tokens produce a warning but don't stop execution.
    """
    tokens = []
    if raw_value:
        tokens.extend([t.strip() for t in raw_value.split(",") if t.strip()])
    if raw_file:
        if not os.path.isfile(raw_file):
            sys.exit(f"Error: --force-single-file not found: {raw_file}")
        with open(raw_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens.extend([t.strip() for t in line.split(",") if t.strip()])

    forced = set()
    for tok in tokens:
        if tok.isdigit():
            page_num = int(tok)
            if 1 <= page_num <= len(pages):
                forced.add(pages[page_num - 1])
            else:
                print(f"  Warning: page number {page_num} out of range (1-{len(pages)}), skipping.")
        else:
            matches = [p for p in pages if p == tok or os.path.splitext(p)[0] == tok]
            if matches:
                forced.update(matches)
            else:
                print(f"  Warning: '{tok}' not found among input pages, skipping.")
    return forced


def make_spread(left_path, right_path, gap, gap_color):
    """
    Build one landscape spread image.
    left_path may be None (blank filler) for a solo page.
    right_path may be None (blank filler) for a solo page.
    """
    left_img = Image.open(left_path).convert("RGB") if left_path else None
    right_img = Image.open(right_path).convert("RGB") if right_path else None

    real_imgs = [im for im in (left_img, right_img) if im is not None]
    target_height = max(im.height for im in real_imgs)

    def resize_to_height(im):
        if im is None:
            return None
        if im.height == target_height:
            return im
        ratio = target_height / im.height
        new_width = int(im.width * ratio)
        return im.resize((new_width, target_height), Image.LANCZOS)

    left_img = resize_to_height(left_img)
    right_img = resize_to_height(right_img)

    left_w = left_img.width if left_img else 0
    right_w = right_img.width if right_img else 0

    total_width = left_w + right_w + gap
    spread = Image.new("RGB", (total_width, target_height), gap_color)

    if left_img:
        spread.paste(left_img, (0, 0))
    if right_img:
        spread.paste(right_img, (left_w + gap, 0))

    return spread


def build_output_plan(pages, input_dir, aspect_threshold, forced_singles, excluded, cover_alone):
    """
    Shared plan-building logic used by both single-folder and batch review
    flows, and mirrored exactly in the browser's JS for live preview.

    Excluded pages are filtered out FIRST and are invisible to everything
    else. Already-merged spreads and forced-solo pages (including an
    implicit cover-alone) are then invisible to the pairing of ordinary
    pages -- ordinary pages pair with each other by skipping straight over
    them -- but every special keeps its own position so it lands back in
    the correct spot in the final reading order.

    Returns (plan, flags) where flags maps filename -> bool (already-merged).
    """
    working_pages = [p for p in pages if p not in excluded]

    flags = {}
    for p in working_pages:
        flags[p] = is_already_spread(os.path.join(input_dir, p), aspect_threshold)

    specials = []  # (idx, kind, filename) kind in {"spread", "forced"}
    normals = []   # (idx, filename)

    for idx, p in enumerate(working_pages):
        if flags[p]:
            specials.append((idx, "spread", p))
        elif p in forced_singles:
            specials.append((idx, "forced", p))
        elif cover_alone and idx == 0:
            specials.append((idx, "forced", p))
        else:
            normals.append((idx, p))

    plan_items = []  # (anchor_idx, op_tuple)
    i = 0
    while i < len(normals):
        right_idx, right_name = normals[i]
        if i + 1 < len(normals):
            left_idx, left_name = normals[i + 1]
            plan_items.append((right_idx, ("pair", left_name, right_name)))
            i += 2
        else:
            plan_items.append((right_idx, ("pair", None, right_name)))
            i += 1

    for idx, kind, name in specials:
        if kind == "spread":
            plan_items.append((idx, ("passthrough", name)))
        else:
            plan_items.append((idx, ("pair", None, name)))

    plan_items.sort(key=lambda item: item[0])
    plan = [item[1] for item in plan_items]
    return plan, flags


def generate_outputs(plan, input_dir, output_dir, gap, gap_color, img_format,
                      quality, cbz_path, cleanup_images, dry_run):
    """Renders a plan to spread images and optionally packages them into a
    .cbz. Returns the number of spreads written."""
    os.makedirs(output_dir, exist_ok=True)

    save_kwargs = {}
    if img_format in ("jpg", "webp"):
        save_kwargs["quality"] = quality
        save_kwargs["optimize"] = True

    spread_num = 1
    written_paths = []
    for op in plan:
        out_name = f"spread_{spread_num:03d}.{img_format}"
        out_path = os.path.join(output_dir, out_name)

        if op[0] == "passthrough":
            filename = op[1]
            print(f"  {out_name}:  PASSTHROUGH={filename} (already a spread)")
            if not dry_run:
                src_path = os.path.join(input_dir, filename)
                with Image.open(src_path) as im:
                    im.convert("RGB").save(out_path, **save_kwargs)
                written_paths.append(out_path)
        else:
            _, left, right = op
            left_disp = left if left else "(blank)"
            right_disp = right if right else "(blank)"
            print(f"  {out_name}:  LEFT={left_disp}   RIGHT={right_disp}")
            if not dry_run:
                left_path = os.path.join(input_dir, left) if left else None
                right_path = os.path.join(input_dir, right) if right else None
                spread = make_spread(left_path, right_path, gap, gap_color)
                spread.save(out_path, **save_kwargs)
                written_paths.append(out_path)

        spread_num += 1

    if dry_run:
        print("\nDry run complete. No files written.")
        return 0

    print(f"\nDone. {spread_num - 1} spread(s) saved to {output_dir}")

    if cbz_path:
        print(f"\nPackaging into compressed {os.path.basename(cbz_path)}...")
        os.makedirs(os.path.dirname(os.path.abspath(cbz_path)), exist_ok=True)
        with zipfile.ZipFile(cbz_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as cbz_file:
            for p in written_paths:
                cbz_file.write(p, os.path.basename(p))
        print(f"Success! {cbz_path} created.")

        if cleanup_images:
            for p in written_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(output_dir)
            except OSError:
                pass  # not empty (unrelated files present) -- leave it alone
            print("Cleaned up loose spread images (kept only the .cbz).")

    return spread_num - 1


def run_batch_review_server(folders, args):
    """
    Orchestrates the whole interactive flow in ONE browser tab and ONE
    local server:
      1. If more than one folder, show a checklist so the user picks which
         to include.
      2. Review each selected folder in turn (crop-preview, solo/exclude
         toggles, live-adjustable threshold/crop settings).
      3. Generate that folder's spreads + .cbz as soon as its review
         completes, then advance to the next one.
      4. Show a final summary once the queue is empty, or a cancelled
         screen if the user backs out.

    `folders` is a list of {"name", "path", "count", "is_archive"} dicts (as
    returned by discover_batch_folders, or a single-element list for plain
    -i/-o use). Archive-sourced folders are extracted to a fresh temp
    directory right when they become the "current" folder (so the browser
    can serve their images for the whole time they're being reviewed), and
    that temp directory is deleted again right after that chapter's output
    is generated -- at most one chapter's worth of extracted images exists
    on disk at any given moment, and originals are never modified.

    `args` is the parsed CLI namespace (used for defaults and for
    gap/format/quality/cbz/output settings).

    Returns a list of (folder_name, spread_count) tuples that were
    successfully generated.
    """
    gap_color = args.gap_color_tuple
    active_temp_dirs = []  # any temp extraction dirs not yet cleaned up

    def prepare_current_folder(folder):
        """If folder is archive-sourced, extract it to a fresh temp dir and
        return a NEW dict pointing at that dir. Non-archive folders (plain
        subfolders of pages) are returned unchanged -- no extraction needed."""
        if not folder.get("is_archive"):
            return folder
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", folder["name"])[:80] or "archive"
        dest = tempfile.mkdtemp(prefix="mangaspread_" + safe_name + "_")
        active_temp_dirs.append(dest)
        print(f"  Extracting {os.path.basename(folder['path'])}...")
        extract_archive(folder["path"], dest)
        prepared = dict(folder)
        prepared["path"] = dest
        prepared["extracted_temp_dir"] = dest
        return prepared

    def cleanup_extracted_folder(folder):
        temp_dir = folder.get("extracted_temp_dir")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if temp_dir in active_temp_dirs:
                active_temp_dirs.remove(temp_dir)

    events = queue.Queue()
    state = {
        "phase": "select" if len(folders) > 1 else "review",
        "queue": None,           # fixed list of RAW (unprepared) folder dicts, set once selected
        "queue_index": 0,        # position within queue
        "current": None,         # PREPARED folder dict currently being reviewed
        "folder_index": 0,
        "threshold": args.aspect_threshold,
        "crop_percent": args.crop_percent,
        "full_preview_count": args.full_preview_count,
        "thumb_size": args.thumb_size,
        "forced_singles": set(),
        "excluded": set(),
        "results": [],
        "cancelled": False,
    }
    state_lock = threading.Lock()

    if state["phase"] == "review":
        state["queue"] = list(folders)
        state["queue_index"] = 0
        state["current"] = prepare_current_folder(folders[0])
        state["folder_index"] = 1

    def current_folder_dir():
        with state_lock:
            cur = state["current"]
        return os.path.abspath(cur["path"]) if cur else None

    def build_state_payload():
        with state_lock:
            phase = state["phase"]
            if phase == "select":
                return {
                    "phase": "select",
                    "folders": [{"name": f["name"], "count": f["count"], "is_archive": f.get("is_archive", False)} for f in folders],
                    "defaults": {
                        "threshold": state["threshold"],
                        "crop_percent": state["crop_percent"],
                        "full_preview_count": state["full_preview_count"],
                        "thumb_size": state["thumb_size"],
                    },
                }
            elif phase == "review":
                cur = state["current"]
                return {
                    "phase": "review",
                    "folder_name": cur["name"],
                    "folder_index": state["folder_index"],
                    "folder_total": len(state["queue"]),
                    "pages": collect_pages(cur["path"]),
                    "threshold": state["threshold"],
                    "crop_percent": state["crop_percent"],
                    "full_preview_count": state["full_preview_count"],
                    "thumb_size": state["thumb_size"],
                    "forced": sorted(state["forced_singles"]),
                    "excluded": sorted(state["excluded"]),
                }
            else:
                return {
                    "phase": "done",
                    "results": state["results"],
                    "cancelled": state["cancelled"],
                }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_bytes(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # Every response from this server is either the shell page or
            # dynamic state -- never safe to cache. Applying this uniformly
            # (rather than only on /pages/) closes off any staleness on the
            # HTML shell or /api/state too.
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj):
            self._send_bytes(json.dumps(obj).encode("utf-8"), "application/json")

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/":
                self._send_bytes(PICKER_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send_json(build_state_payload())
            elif path.startswith("/pages/"):
                filename = urllib.parse.unquote(path[len("/pages/"):])
                folder_dir = current_folder_dir()
                if folder_dir is None:
                    self.send_error(404)
                    return
                filepath = os.path.join(folder_dir, filename)
                if (os.path.dirname(os.path.abspath(filepath)) == folder_dir
                        and os.path.isfile(filepath)):
                    ext = os.path.splitext(filepath)[1].lower()
                    mime = MIME_TYPES.get(ext, "application/octet-stream")
                    with open(filepath, "rb") as f:
                        data = f.read()
                    self._send_bytes(data, mime)
                else:
                    self.send_error(404)
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body) if body.strip() else {}
            except Exception as e:
                self.send_error(400, str(e))
                return

            if self.path == "/api/select":
                done = threading.Event()
                events.put(("select", data, done))
                done.wait()  # don't respond until the main thread has actually
                              # advanced state -- otherwise the browser's next
                              # /api/state fetch can race ahead and see stale data
                self._send_json({"ok": True})
            elif self.path == "/api/submit":
                done = threading.Event()
                events.put(("submit", data, done))
                done.wait()  # same reasoning -- this can take a few seconds for
                              # a real chapter (image generation + zipping), which
                              # is exactly why we must not respond early
                self._send_json({"ok": True})
            elif self.path == "/api/cancel":
                done = threading.Event()
                events.put(("cancel", data, done))
                done.wait()
                self._send_json({"ok": True})
            else:
                self.send_error(404)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/"
    print(f"\nOpening review tool in your browser: {url}")
    print("(If it doesn't open automatically, copy that URL into any browser.)")
    if state["phase"] == "select":
        print(f"Found {len(folders)} folders. Pick which to include, then the terminal will guide you through each.")
    else:
        print("Click pages to keep them solo or exclude them, then hit Continue.")
    webbrowser.open(url)

    def process_current_folder(forced, excl, threshold):
        with state_lock:
            cur = state["current"]
            idx_in_queue = state["queue_index"]
            is_last = idx_in_queue == len(state["queue"]) - 1

        print(f"\n=== {cur['name']} ===")
        pages = collect_pages(cur["path"])
        plan, _flags = build_output_plan(pages, cur["path"], threshold, forced, excl, args.cover_alone)

        out_dir, cbz_path = resolve_output_paths(args, cur["name"], cur["path"], cur.get("is_archive", False))
        count = generate_outputs(
            plan, cur["path"], out_dir, args.gap, gap_color, args.format,
            args.quality, cbz_path if args.cbz else None, args.cleanup_images,
            dry_run=False,
        )

        cleanup_extracted_folder(cur)

        next_prepared = None
        if not is_last:
            nxt_raw = state["queue"][idx_in_queue + 1]
            next_prepared = prepare_current_folder(nxt_raw)
            print(f"\nMoving to next folder: {nxt_raw['name']}")

        with state_lock:
            state["results"].append((cur["name"], count))
            if not is_last:
                state["current"] = next_prepared
                state["queue_index"] = idx_in_queue + 1
                state["folder_index"] += 1
                state["forced_singles"] = set()
                state["excluded"] = set()
            else:
                state["phase"] = "done"

    try:
        while True:
            kind, data, done_signal = events.get()

            if kind == "cancel":
                with state_lock:
                    state["cancelled"] = True
                    state["phase"] = "done"
                done_signal.set()
                break

            if kind == "select":
                selected_names = set(data.get("selected", []))
                chosen = [f for f in folders if f["name"] in selected_names]
                if not chosen:
                    with state_lock:
                        state["cancelled"] = True
                        state["phase"] = "done"
                    done_signal.set()
                    break
                prepared_first = prepare_current_folder(chosen[0])
                with state_lock:
                    state["queue"] = chosen
                    state["queue_index"] = 0
                    state["current"] = prepared_first
                    state["folder_index"] = 1
                    state["phase"] = "review"
                print(f"\n{len(chosen)} folder(s) selected. Starting review: {chosen[0]['name']}")
                done_signal.set()
                continue

            if kind == "submit":
                forced = set(data.get("forced_singles", []))
                excl = set(data.get("excluded", []))
                threshold = float(data.get("threshold", state["threshold"]))
                crop_percent = float(data.get("crop_percent", state["crop_percent"]))
                full_preview_count = int(data.get("full_preview_count", state["full_preview_count"]))
                thumb_size = int(data.get("thumb_size", state["thumb_size"]))
                with state_lock:
                    state["threshold"] = threshold
                    state["crop_percent"] = crop_percent
                    state["full_preview_count"] = full_preview_count
                    state["thumb_size"] = thumb_size

                try:
                    process_current_folder(forced, excl, threshold)
                finally:
                    # Always release the waiting browser request, even if
                    # processing raised -- otherwise a bad image mid-batch
                    # would leave the browser hanging forever with no
                    # response, instead of the script's traceback surfacing
                    # in the terminal where the user can actually see it.
                    done_signal.set()

                with state_lock:
                    is_finished = state["phase"] == "done"
                if is_finished:
                    break
                continue
    except KeyboardInterrupt:
        print("\nCancelled (Ctrl+C). Already-completed chapters were still saved.")
        httpd.shutdown()
        httpd.server_close()
        sys.exit(1)
    finally:
        # Safety net: clean up any temp extraction that's still hanging
        # around (e.g. a chapter was mid-review, extracted but not yet
        # generated, when the process ended one way or another).
        for d in list(active_temp_dirs):
            shutil.rmtree(d, ignore_errors=True)

    time.sleep(0.3)
    httpd.shutdown()
    httpd.server_close()

    with state_lock:
        results = list(state["results"])
        cancelled = state["cancelled"]

    if cancelled:
        if results:
            print(f"\nCancelled from the browser. {len(results)} chapter(s) completed before that; no further files were changed.")
        else:
            print("\nCancelled from the browser. No files were changed.")
    return results


def resolve_output_paths(args, folder_name, folder_path, is_archive=False):
    """
    Decide where a given chapter's spread images and .cbz should go.

    Batch mode, no -o given:    <parent>/<name>/_spreads/   and  <parent>/<name>.cbz
    Batch mode, -o given:       <output>/<name>/             and  <output>/<name>.cbz
    Single-folder mode:         args.output/                 and  ./<basename(output)>.cbz
                                 (unchanged from previous versions, for backward compatibility)

    When the chapter came from an existing .cbz/.zip, " [spreads]" is
    appended to the output .cbz name -- guarantees the output can never
    silently overwrite the source archive, and makes it obvious at a glance
    which file is the converted one.
    """
    suffix = " [spreads]" if is_archive else ""

    if args.batch:
        parent = os.path.abspath(args.batch)
        cbz_name = folder_name + suffix + ".cbz"
        if args.output:
            out_dir = os.path.join(args.output, folder_name)
            cbz_path = os.path.join(args.output, cbz_name)
        else:
            out_dir = os.path.join(folder_path, "_spreads")
            cbz_path = os.path.join(parent, cbz_name)
        return out_dir, cbz_path
    else:
        out_dir = args.output
        base_dir_name = os.path.basename(os.path.normpath(args.output)) or "merged_manga"
        cbz_path = f"{base_dir_name}{suffix}.cbz"
        return out_dir, cbz_path


# The picker UI served by run_batch_review_server(). Fully static -- all
# dynamic data (folder list, page list, images, current selections) is
# fetched at runtime via /api/state and /pages/<name>, so no Python-side
# templating is used here (deliberately avoiding str.format()/f-strings on
# this blob, since the CSS below is full of literal { } that would collide
# with them).
PICKER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spread Picker — review manga page pairing</title>
<style>
  :root {
    --bg: #141414;
    --surface: #1a1a1a;
    --surface-2: #232323;
    --border: #333333;
    --text: #f2f0ea;
    --text-muted: #8f8f8f;
    --red: #c1272d;
    --red-soft: rgba(193, 39, 45, 0.14);
    --blue: #4a90a4;
    --blue-soft: rgba(74, 144, 164, 0.14);
    --amber: #c98a3b;
    --radius: 5px;
    --preview-height: 300px;
  }

  * { box-sizing: border-box; }

  html, body {
    margin: 0;
    padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }

  .mono {
    font-family: ui-monospace, SFMono-Regular, "Cascadia Code", Menlo, Consolas, monospace;
  }

  a, button, input { font-family: inherit; }

  :focus-visible {
    outline: 2px solid var(--blue);
    outline-offset: 2px;
  }

  /* Bulletproof visibility toggle -- see the long comment on this same
     class in an earlier version of this file. Never rely on the bare
     [hidden] attribute for an element that also has an author CSS rule
     setting `display`, since same-specificity author rules silently beat
     the browser's default "[hidden] { display: none }". !important always
     wins regardless, so this is the only toggle mechanism used here. */
  .is-hidden { display: none !important; }

  header.page-header {
    padding: 26px 32px 18px;
    border-bottom: 1px solid var(--border);
  }

  .eyebrow {
    font-family: ui-monospace, SFMono-Regular, "Cascadia Code", Menlo, Consolas, monospace;
    text-transform: uppercase;
    letter-spacing: .12em;
    font-size: 11px;
    color: var(--blue);
    font-weight: 600;
    margin-bottom: 8px;
  }

  h1 {
    font-size: 21px;
    margin: 0 0 8px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }

  .sub {
    color: var(--text-muted);
    font-size: 13px;
    max-width: 660px;
    line-height: 1.55;
    margin: 0;
  }

  .sub .mono {
    color: var(--text);
    background: var(--surface-2);
    padding: 1px 6px;
    border-radius: var(--radius);
    font-size: 12px;
  }

  .toolbar {
    position: sticky;
    top: 0;
    z-index: 5;
    display: flex;
    align-items: center;
    gap: 18px;
    padding: 12px 32px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }

  .progress-label {
    font-size: 11.5px;
    color: var(--blue);
    font-weight: 700;
    letter-spacing: .02em;
    white-space: nowrap;
  }

  .threshold-control, .crop-control, .preview-count-control {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--text-muted);
    white-space: nowrap;
  }
  .threshold-control input[type="range"],
  .crop-control input[type="range"] {
    accent-color: var(--blue);
    width: 100px;
  }
  .preview-count-control input[type="number"] {
    width: 46px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 3px;
    padding: 3px 5px;
    font-size: 12px;
  }

  #resetBtn {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
    padding: 8px 12px;
    border-radius: var(--radius);
    font-size: 12px;
    cursor: pointer;
  }
  #resetBtn:hover { color: var(--text); border-color: var(--text-muted); }

  .stats {
    margin-left: auto;
    font-size: 12px;
    color: var(--text-muted);
    white-space: nowrap;
  }
  .stats strong { color: var(--text); font-weight: 700; }

  main {
    padding: 26px 32px 150px;
    max-width: min(1800px, 95vw);
    margin: 0 auto;
  }

  .loading-state {
    text-align: center;
    color: var(--text-muted);
    padding: 90px 0;
    font-size: 13.5px;
  }

  /* --- folder selection screen --- */
  .select-screen h2 {
    font-size: 17px;
    margin: 8px 0 4px;
  }
  .select-sub {
    color: var(--text-muted);
    font-size: 13px;
    margin: 0 0 16px;
  }
  .select-actions {
    display: flex;
    gap: 10px;
    margin-bottom: 14px;
  }
  .select-actions button {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
    padding: 7px 12px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .select-actions button:hover { border-color: var(--text-muted); }

  .folder-checklist {
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .folder-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 11px 14px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
  }
  .folder-row:last-child { border-bottom: none; }
  .folder-row:hover { background: var(--surface-2); }
  .folder-row input[type="checkbox"] {
    accent-color: var(--red);
    width: 15px;
    height: 15px;
    flex-shrink: 0;
  }
  .folder-name { flex: 1; font-size: 13px; }
  .folder-archive-badge {
    font-size: 9px;
    letter-spacing: .06em;
    text-transform: uppercase;
    padding: 2px 6px;
    border-radius: 3px;
    font-weight: 700;
    background: var(--blue);
    color: #06222a;
  }
  .folder-count { color: var(--text-muted); font-size: 11.5px; }

  /* --- review cards --- */
  .spread-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 12px;
    padding: 14px 18px;
    display: flex;
    align-items: center;
    gap: 18px;
  }
  .spread-card.solo { border-left-color: var(--red); background: linear-gradient(to right, var(--red-soft), transparent 20%), var(--surface); }
  .spread-card.passthrough { border-left-color: var(--blue); background: linear-gradient(to right, var(--blue-soft), transparent 20%), var(--surface); }

  .spread-index {
    font-size: 10.5px;
    color: var(--text-muted);
    width: 96px;
    flex-shrink: 0;
    text-transform: uppercase;
    letter-spacing: .04em;
    line-height: 1.5;
  }
  .spread-index b {
    color: var(--text);
    font-size: 13px;
    display: block;
    margin-bottom: 3px;
    letter-spacing: 0;
  }

  .spread-pages {
    display: flex;
    align-items: flex-end;
    gap: 0;
    flex: 1;
    justify-content: center;
    min-height: var(--preview-height);
    flex-wrap: wrap;
  }

  .page-slot {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    flex-shrink: 0;
  }
  .page-slot.wide { max-width: 90%; }
  .page-slot.blank { visibility: hidden; width: calc(var(--preview-height) * 0.68); }
  .page-slot.crop-mode { width: auto; }
  .page-slot.crop-mode.wide { width: auto; }

  .page-slot > img {
    max-width: 100%;
    max-height: var(--preview-height);
    object-fit: contain;
    border-radius: 2px;
    display: block;
    background: #000;
    cursor: pointer;
    transition: transform .12s ease, box-shadow .12s ease;
  }
  .page-slot:not(.wide):not(.crop-mode):hover > img {
    transform: translateY(-3px);
    box-shadow: 0 6px 16px rgba(0,0,0,.45);
  }

  .crop-window {
    position: relative;
    overflow: hidden;
    height: var(--preview-height);
    background: #000;
    border-radius: 2px;
    cursor: pointer;
  }
  .crop-window img {
    position: absolute;
    top: 0;
    height: var(--preview-height);
    display: block;
  }
  .crop-window.edge-right img { right: 0; }
  .crop-window.edge-left img { left: 0; }
  .crop-window.edge-center img { left: 50%; transform: translateX(-50%); }

  .seam-marker {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--red);
    opacity: .55;
    pointer-events: none;
  }
  .crop-window.edge-right .seam-marker { right: 0; }
  .crop-window.edge-left .seam-marker { left: 0; }
  .crop-window.edge-center .seam-marker { display: none; }

  .gutter {
    width: 3px;
    align-self: stretch;
    background: linear-gradient(to right, rgba(0,0,0,0), rgba(0,0,0,.7), rgba(0,0,0,0));
    margin: 0 3px;
    min-height: 90px;
  }

  .page-label {
    font-size: 10px;
    color: var(--text-muted);
    margin-top: 7px;
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .solo-toggle, .exclude-toggle {
    position: absolute;
    padding: 4px 9px;
    border-radius: 11px;
    font-size: 9.5px;
    font-weight: 700;
    letter-spacing: .03em;
    text-transform: uppercase;
    cursor: pointer;
    transition: background .12s, color .12s, transform .12s;
  }
  .solo-toggle:hover, .exclude-toggle:hover { transform: scale(1.06); }

  .solo-toggle {
    top: 6px;
    right: 6px;
    border: 1.5px solid var(--red);
    background: rgba(15,15,15,.75);
    color: var(--red);
  }
  .solo-toggle.active {
    background: var(--red);
    color: #fff;
    box-shadow: 0 0 0 3px rgba(193,39,45,.22);
  }

  .exclude-toggle {
    bottom: 6px;
    right: 6px;
    border: 1.5px solid var(--amber);
    background: rgba(15,15,15,.75);
    color: var(--amber);
  }
  .exclude-toggle.active {
    background: var(--amber);
    color: #1a1000;
    box-shadow: 0 0 0 3px rgba(201,138,59,.22);
  }

  .reveal-full-btn {
    position: absolute;
    bottom: 6px;
    left: 6px;
    font-size: 9px;
    padding: 3px 7px;
    border-radius: 3px;
    background: rgba(15,15,15,.85);
    border: 1px solid var(--border);
    color: var(--text-muted);
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: .04em;
  }
  .reveal-full-btn:hover { color: var(--text); border-color: var(--text-muted); }

  .badge {
    position: absolute;
    top: 6px;
    left: 6px;
    font-size: 9px;
    letter-spacing: .06em;
    text-transform: uppercase;
    padding: 3px 7px;
    border-radius: 3px;
    font-weight: 700;
    background: var(--blue);
    color: #06222a;
  }

  footer.exportbar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--surface-2);
    border-top: 1px solid var(--border);
    padding: 14px 32px;
    display: flex;
    align-items: center;
    gap: 14px;
    z-index: 10;
  }

  .cli-preview {
    font-size: 12.5px;
    color: var(--text-muted);
    flex: 1;
  }

  button.primary {
    background: var(--red);
    color: #fff;
    border: none;
    padding: 10px 20px;
    border-radius: 4px;
    font-weight: 700;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
  }
  button.primary:hover:not(:disabled) { background: #d6383f; }
  button.primary:disabled { opacity: .6; cursor: default; }

  button.secondary {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
    padding: 10px 14px;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
  }
  button.secondary:hover { border-color: var(--text-muted); }

  .done-overlay {
    position: fixed;
    inset: 0;
    background: rgba(10,10,10,.94);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
    overflow-y: auto;
  }
  .done-box { text-align: center; padding: 40px 20px; max-width: 420px; }
  .done-check {
    width: 60px;
    height: 60px;
    border-radius: 50%;
    background: var(--red);
    color: #fff;
    font-size: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 auto 18px;
  }
  .done-title { font-size: 18px; font-weight: 700; margin-bottom: 8px; }
  .done-sub { color: var(--text-muted); font-size: 12.5px; margin-bottom: 18px; }
  .done-summary {
    text-align: left;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .done-row {
    padding: 8px 12px;
    font-size: 11.5px;
    border-bottom: 1px solid var(--border);
    color: var(--text-muted);
  }
  .done-row:last-child { border-bottom: none; }

  @media (max-width: 720px) {
    header.page-header, .toolbar, main, footer.exportbar { padding-left: 16px; padding-right: 16px; }
    .stats { margin-left: 0; width: 100%; }
    .spread-card { flex-wrap: wrap; }
    .spread-index { width: 100%; }
    footer.exportbar { flex-wrap: wrap; }
  }

  @media (prefers-reduced-motion: reduce) {
    * { transition: none !important; }
  }
</style>
</head>
<body>

<header class="page-header">
  <div class="eyebrow">manga spread picker</div>
  <h1>Isolate the pages that shouldn't pair</h1>
  <p class="sub">
    Serving your pages locally from <span class="mono">merge_manga_spreads.py</span> — nothing leaves this
    machine. Click any page to force it solo or exclude it; everything else re-pairs around it automatically.
  </p>
</header>

<div class="toolbar is-hidden" id="toolbar">
  <div class="progress-label is-hidden mono" id="progressLabel"></div>
  <div class="crop-control">
    <label for="thumbSize">Thumbnail size</label>
    <input type="range" id="thumbSize" min="150" max="700" step="10" value="300">
    <span id="thumbSizeValue" class="mono">300px</span>
  </div>
  <div class="threshold-control">
    <label for="threshold">Auto-spread ratio ≥</label>
    <input type="range" id="threshold" min="1.05" max="2.5" step="0.05" value="1.15">
    <span id="thresholdValue" class="mono">1.15</span>
  </div>
  <div class="crop-control">
    <label for="cropPercent">Seam preview width</label>
    <input type="range" id="cropPercent" min="10" max="60" step="1" value="25">
    <span id="cropPercentValue" class="mono">25%</span>
  </div>
  <div class="preview-count-control">
    <label for="fullPreviewCount">Full preview: first</label>
    <input type="number" id="fullPreviewCount" min="0" max="200" value="3">
  </div>
  <button id="resetBtn" type="button">Reset selections</button>
  <div class="stats mono" id="stats"></div>
</div>

<main>
  <div class="loading-state" id="loadingState">Loading…</div>
  <div id="content"></div>
</main>

<footer class="exportbar is-hidden" id="exportBar">
  <div class="cli-preview mono" id="cliPreview"></div>
  <button class="secondary" id="cancelBtn" type="button">Cancel</button>
  <button class="primary" id="continueBtn" type="button">Continue →</button>
</footer>

<div class="done-overlay is-hidden" id="doneOverlay">
  <div class="done-box">
    <div class="done-check" id="doneIcon">✓</div>
    <div class="done-title" id="doneTitle">All done</div>
    <div class="done-sub mono" id="doneSub">You can close this tab now.</div>
    <div class="done-summary" id="doneSummary"></div>
  </div>
</div>

<script>
(function () {
  "use strict";

  var phase = "loading";
  var pages = [];
  var forcedSingles = new Set();
  var excluded = new Set();
  var revealedFull = new Set();
  var threshold = 1.15;
  var cropPercent = 25;
  var fullPreviewCount = 3;
  var thumbSize = 300; // display-only preference; never affects generated output

  var folderName = "";
  var folderIndex = 1;
  var folderTotal = 1;

  var folderList = [];
  var selectedFolders = new Set();

  var doneResults = [];
  var doneCancelled = false;
  var submitting = false;

  var toolbar = document.getElementById("toolbar");
  var progressLabel = document.getElementById("progressLabel");
  var thumbSizeSlider = document.getElementById("thumbSize");
  var thumbSizeValue = document.getElementById("thumbSizeValue");
  var thresholdSlider = document.getElementById("threshold");
  var thresholdValue = document.getElementById("thresholdValue");
  var cropPercentSlider = document.getElementById("cropPercent");
  var cropPercentValue = document.getElementById("cropPercentValue");
  var fullPreviewInput = document.getElementById("fullPreviewCount");
  var resetBtn = document.getElementById("resetBtn");
  var statsDiv = document.getElementById("stats");
  var loadingState = document.getElementById("loadingState");
  var content = document.getElementById("content");
  var exportBar = document.getElementById("exportBar");
  var cliPreview = document.getElementById("cliPreview");
  var cancelBtn = document.getElementById("cancelBtn");
  var continueBtn = document.getElementById("continueBtn");
  var doneOverlay = document.getElementById("doneOverlay");

  function applyThumbSize() {
    document.documentElement.style.setProperty("--preview-height", thumbSize + "px");
  }
  applyThumbSize();

  function naturalKey(name) {
    return name.split(/(\d+)/).filter(function (s) { return s.length > 0; })
      .map(function (chunk) {
        return /^\d+$/.test(chunk) ? parseInt(chunk, 10) : chunk.toLowerCase();
      });
  }

  function compareNames(a, b) {
    var ka = naturalKey(a), kb = naturalKey(b);
    var len = Math.max(ka.length, kb.length);
    for (var i = 0; i < len; i++) {
      var x = ka[i], y = kb[i];
      if (x === undefined) return -1;
      if (y === undefined) return 1;
      var xNum = typeof x === "number", yNum = typeof y === "number";
      if (xNum && yNum) {
        if (x !== y) return x - y;
      } else if (!xNum && !yNum) {
        if (x !== y) return x < y ? -1 : 1;
      } else {
        var xs = String(x), ys = String(y);
        if (xs !== ys) return xs < ys ? -1 : 1;
      }
    }
    return 0;
  }

  var IMAGE_LOAD_TIMEOUT_MS = 8000;

  // folderToken makes the URL unique per chapter (chapters very commonly
  // reuse filenames like "page1.png" across folders -- without this, the
  // browser can treat /pages/page1.png as "the same resource" across a
  // chapter transition and never issue a fresh request for it, or worse,
  // silently reuse or stall on whatever it fetched for a previous chapter).
  // A hard timeout guarantees this can never hang the whole review screen
  // indefinitely even if a single image request stalls for any reason.
  function loadOne(name, folderToken) {
    return new Promise(function (resolve) {
      var url = "/pages/" + encodeURIComponent(name) + "?ch=" + encodeURIComponent(folderToken);
      var img = new Image();
      var settled = false;

      var timer = setTimeout(function () {
        if (settled) return;
        settled = true;
        resolve({ name: name, url: url, width: 0, height: 0 });
      }, IMAGE_LOAD_TIMEOUT_MS);

      img.onload = function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve({ name: name, url: url, width: img.naturalWidth, height: img.naturalHeight });
      };
      img.onerror = function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve({ name: name, url: url, width: 0, height: 0 });
      };
      img.src = url;
    });
  }

  function isAutoSpread(page) {
    if (!page.height) return false;
    return (page.width / page.height) >= threshold;
  }

  // Mirrors build_output_plan() in the Python script exactly.
  function buildPlanFrom(pageList) {
    var specials = [];
    var normals = [];

    pageList.forEach(function (p, idx) {
      if (isAutoSpread(p)) {
        specials.push({ idx: idx, kind: "spread", name: p.name });
      } else if (forcedSingles.has(p.name)) {
        specials.push({ idx: idx, kind: "forced", name: p.name });
      } else {
        normals.push({ idx: idx, name: p.name });
      }
    });

    var items = [];
    var i = 0;
    while (i < normals.length) {
      var right = normals[i];
      if (i + 1 < normals.length) {
        var left = normals[i + 1];
        items.push({ anchor: right.idx, op: "pair", left: left.name, right: right.name });
        i += 2;
      } else {
        items.push({ anchor: right.idx, op: "pair", left: null, right: right.name });
        i += 1;
      }
    }

    specials.forEach(function (s) {
      if (s.kind === "spread") {
        items.push({ anchor: s.idx, op: "passthrough", name: s.name });
      } else {
        items.push({ anchor: s.idx, op: "pair", left: null, right: s.name });
      }
    });

    items.sort(function (a, b) { return a.anchor - b.anchor; });
    return items;
  }

  function toggleSolo(name) {
    excluded.delete(name);
    if (forcedSingles.has(name)) forcedSingles.delete(name);
    else forcedSingles.add(name);
    renderReview();
  }

  function toggleExcluded(name) {
    forcedSingles.delete(name);
    if (excluded.has(name)) excluded.delete(name);
    else excluded.add(name);
    renderReview();
  }

  // opts.crop: null (full preview) | "left" | "right" | "center"
  function makeSlot(page, opts) {
    opts = opts || {};
    var slot = document.createElement("div");
    slot.className = "page-slot" + (opts.wide ? " wide" : "") + (opts.crop ? " crop-mode" : "");
    slot.dataset.name = page.name;

    if (opts.crop) {
      var win = document.createElement("div");
      win.className = "crop-window edge-" + opts.crop;
      var ratio = (page.width && page.height) ? (page.width / page.height) : 0.7;
      var renderedWidth = thumbSize * ratio;
      var windowWidth = Math.max(28, renderedWidth * (cropPercent / 100));
      win.style.width = windowWidth + "px";

      var cimg = document.createElement("img");
      cimg.src = page.url;
      cimg.loading = "lazy";
      cimg.alt = page.name;
      cimg.style.width = renderedWidth + "px";
      win.appendChild(cimg);

      var seam = document.createElement("div");
      seam.className = "seam-marker";
      win.appendChild(seam);

      win.addEventListener("click", function () { toggleSolo(page.name); });
      slot.appendChild(win);
    } else {
      var img = document.createElement("img");
      img.src = page.url;
      img.loading = "lazy";
      img.alt = page.name;
      if (!opts.badge) {
        img.addEventListener("click", function () { toggleSolo(page.name); });
      }
      slot.appendChild(img);
    }

    if (opts.badge) {
      var badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = opts.badge;
      slot.appendChild(badge);
    } else {
      var isSolo = forcedSingles.has(page.name);
      var soloBtn = document.createElement("button");
      soloBtn.type = "button";
      soloBtn.className = "solo-toggle" + (isSolo ? " active" : "");
      soloBtn.textContent = isSolo ? "Solo ✓" : "Keep solo";
      soloBtn.title = isSolo ? "Click to pair this page back in" : "Click to keep this page solo";
      soloBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        toggleSolo(page.name);
      });
      slot.appendChild(soloBtn);
    }

    var isExcluded = excluded.has(page.name);
    var exBtn = document.createElement("button");
    exBtn.type = "button";
    exBtn.className = "exclude-toggle" + (isExcluded ? " active" : "");
    exBtn.textContent = isExcluded ? "Excluded ✓" : "Exclude";
    exBtn.title = isExcluded
      ? "Click to include this page again"
      : "Click to drop this page from the output entirely (your source file is never touched)";
    exBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleExcluded(page.name);
    });
    slot.appendChild(exBtn);

    if (opts.crop) {
      var revealBtn = document.createElement("button");
      revealBtn.type = "button";
      revealBtn.className = "reveal-full-btn";
      revealBtn.textContent = "Show full";
      revealBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        revealedFull.add(page.name);
        renderReview();
      });
      slot.appendChild(revealBtn);
    }

    var label = document.createElement("div");
    label.className = "page-label mono";
    label.textContent = page.name;
    slot.appendChild(label);

    return slot;
  }

  function updateStats(effectivePages) {
    var total = pages.length;
    var activeTotal = effectivePages.length;
    var autoCount = effectivePages.filter(isAutoSpread).length;
    statsDiv.innerHTML = "<strong>" + activeTotal + "</strong>/" + total + " active &nbsp;·&nbsp; <strong>" +
      autoCount + "</strong> auto &nbsp;·&nbsp; <strong>" + forcedSingles.size + "</strong> solo &nbsp;·&nbsp; <strong>" +
      excluded.size + "</strong> excluded";
  }

  function updateFooter() {
    if (phase === "select") {
      cliPreview.textContent = selectedFolders.size + " chapter(s) selected";
      continueBtn.textContent = "Start Review →";
      continueBtn.disabled = selectedFolders.size === 0;
    } else if (phase === "review") {
      cliPreview.textContent = forcedSingles.size + " solo, " + excluded.size + " excluded";
      continueBtn.textContent = (folderIndex < folderTotal) ? "Continue to next chapter →" : "Continue →";
      continueBtn.disabled = false;
    }
  }

  function renderSelect() {
    toolbar.classList.add("is-hidden");
    loadingState.classList.add("is-hidden");
    exportBar.classList.remove("is-hidden");
    content.innerHTML = "";

    var wrap = document.createElement("div");
    wrap.className = "select-screen";

    var h2 = document.createElement("h2");
    h2.textContent = "Select chapters to process";
    wrap.appendChild(h2);

    var p = document.createElement("p");
    p.className = "select-sub";
    p.textContent = "Found " + folderList.length + " folder(s) with pages. All are checked by default.";
    wrap.appendChild(p);

    var actions = document.createElement("div");
    actions.className = "select-actions";
    var allBtn = document.createElement("button");
    allBtn.type = "button"; allBtn.textContent = "Select all";
    allBtn.addEventListener("click", function () {
      selectedFolders = new Set(folderList.map(function (f) { return f.name; }));
      renderSelect();
    });
    var noneBtn = document.createElement("button");
    noneBtn.type = "button"; noneBtn.textContent = "Select none";
    noneBtn.addEventListener("click", function () {
      selectedFolders = new Set();
      renderSelect();
    });
    actions.appendChild(allBtn);
    actions.appendChild(noneBtn);
    wrap.appendChild(actions);

    var list = document.createElement("div");
    list.className = "folder-checklist";
    folderList.forEach(function (f) {
      var row = document.createElement("label");
      row.className = "folder-row";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selectedFolders.has(f.name);
      cb.addEventListener("change", function () {
        if (cb.checked) selectedFolders.add(f.name); else selectedFolders.delete(f.name);
        updateFooter();
      });
      row.appendChild(cb);
      var nm = document.createElement("span");
      nm.className = "folder-name mono";
      nm.textContent = f.name;
      row.appendChild(nm);
      if (f.is_archive) {
        var arc = document.createElement("span");
        arc.className = "folder-archive-badge mono";
        arc.textContent = "cbz";
        row.appendChild(arc);
      }
      var ct = document.createElement("span");
      ct.className = "folder-count mono";
      ct.textContent = f.count + " page" + (f.count === 1 ? "" : "s");
      row.appendChild(ct);
      list.appendChild(row);
    });
    wrap.appendChild(list);

    content.appendChild(wrap);
    updateFooter();
  }

  function renderReview() {
    toolbar.classList.remove("is-hidden");
    loadingState.classList.add("is-hidden");
    exportBar.classList.remove("is-hidden");

    thumbSizeSlider.value = thumbSize;
    thumbSizeValue.textContent = thumbSize + "px";
    applyThumbSize();
    thresholdSlider.value = threshold;
    thresholdValue.textContent = threshold.toFixed(2);
    cropPercentSlider.value = cropPercent;
    cropPercentValue.textContent = cropPercent + "%";
    fullPreviewInput.value = fullPreviewCount;

    if (folderTotal > 1) {
      progressLabel.textContent = "Chapter " + folderIndex + " of " + folderTotal + " — " + folderName;
      progressLabel.classList.remove("is-hidden");
    } else {
      progressLabel.classList.add("is-hidden");
    }

    var effectivePages = pages.filter(function (p) { return !excluded.has(p.name); });
    var plan = buildPlanFrom(effectivePages);
    var byName = new Map(pages.map(function (p) { return [p.name, p]; }));
    var frag = document.createDocumentFragment();

    plan.forEach(function (item, i) {
      var isSoloCard = item.op === "pair" && (!item.left || !item.right);
      var showFull = i < fullPreviewCount;
      var card = document.createElement("div");
      card.className = "spread-card" + (item.op === "passthrough" ? " passthrough" : isSoloCard ? " solo" : "");

      var num = String(i + 1).padStart(3, "0");
      var idxDiv = document.createElement("div");
      idxDiv.className = "spread-index mono";
      var kindLabel = item.op === "passthrough" ? "already merged" : isSoloCard ? "solo page" : "spread";
      idxDiv.innerHTML = "<b>#" + num + "</b>" + kindLabel;
      card.appendChild(idxDiv);

      var pagesDiv = document.createElement("div");
      pagesDiv.className = "spread-pages";

      if (item.op === "passthrough") {
        var pPage = byName.get(item.name);
        var pCrop = showFull || revealedFull.has(pPage.name) ? null : "center";
        pagesDiv.appendChild(makeSlot(pPage, { badge: "auto spread", wide: true, crop: pCrop }));
      } else {
        if (item.left) {
          var lPage = byName.get(item.left);
          // Left-slot page: the seam is on ITS right edge.
          var lCrop = showFull || revealedFull.has(lPage.name) ? null : "right";
          pagesDiv.appendChild(makeSlot(lPage, { crop: lCrop }));
        } else {
          var blank = document.createElement("div");
          blank.className = "page-slot blank";
          pagesDiv.appendChild(blank);
        }
        var gutter = document.createElement("div");
        gutter.className = "gutter";
        pagesDiv.appendChild(gutter);
        if (item.right) {
          var rPage = byName.get(item.right);
          // Right-slot page (or a solo page, which always lives here): the
          // seam -- real or hypothetical -- is on ITS left edge.
          var rCrop = showFull || revealedFull.has(rPage.name) ? null : "left";
          pagesDiv.appendChild(makeSlot(rPage, { crop: rCrop }));
        }
      }

      card.appendChild(pagesDiv);
      frag.appendChild(card);
    });

    content.innerHTML = "";
    content.appendChild(frag);

    updateStats(effectivePages);
    updateFooter();
  }

  function renderDone() {
    toolbar.classList.add("is-hidden");
    exportBar.classList.add("is-hidden");
    loadingState.classList.add("is-hidden");
    content.innerHTML = "";

    var icon = document.getElementById("doneIcon");
    var title = document.getElementById("doneTitle");
    var sub = document.getElementById("doneSub");
    var summary = document.getElementById("doneSummary");
    summary.innerHTML = "";

    if (doneCancelled) {
      icon.textContent = "✕";
      title.textContent = "Cancelled";
      sub.textContent = doneResults.length
        ? "No further files were changed after this point. You can close this tab."
        : "No files were changed. You can close this tab.";
    } else {
      icon.textContent = "✓";
      title.textContent = "All done";
      sub.textContent = "You can close this tab now.";
    }

    doneResults.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "done-row mono";
      row.textContent = r[0] + " — " + r[1] + " spread(s)";
      summary.appendChild(row);
    });

    doneOverlay.classList.remove("is-hidden");
  }

  function refreshState() {
    return fetch("/api/state", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (state) {
      phase = state.phase;

      if (phase === "select") {
        folderList = state.folders;
        selectedFolders = new Set(folderList.map(function (f) { return f.name; }));
        threshold = state.defaults.threshold;
        cropPercent = state.defaults.crop_percent;
        fullPreviewCount = state.defaults.full_preview_count;
        thumbSize = state.defaults.thumb_size;
        applyThumbSize();
        renderSelect();
        return;
      }

      if (phase === "review") {
        folderName = state.folder_name;
        folderIndex = state.folder_index;
        folderTotal = state.folder_total;
        threshold = state.threshold;
        cropPercent = state.crop_percent;
        fullPreviewCount = state.full_preview_count;
        thumbSize = state.thumb_size;
        applyThumbSize();
        forcedSingles = new Set(state.forced || []);
        excluded = new Set(state.excluded || []);
        revealedFull = new Set();

        // Show visible feedback and clear the OLD chapter's cards immediately,
        // rather than leaving them frozen on screen while the new chapter's
        // images load in the background -- if something ever does go wrong,
        // this makes it obvious ("stuck on Loading...") instead of silently
        // looking like nothing happened.
        toolbar.classList.add("is-hidden");
        content.innerHTML = "";
        loadingState.textContent = folderTotal > 1
          ? "Loading " + folderName + " (chapter " + folderIndex + " of " + folderTotal + ")…"
          : "Loading…";
        loadingState.classList.remove("is-hidden");

        // folderIndex is unique per step through the queue, so using it as
        // the cache-busting token guarantees fresh image URLs every single
        // chapter transition, even across chapters that reuse filenames.
        var folderToken = folderIndex + ":" + folderName;
        return Promise.all(state.pages.map(function (name) { return loadOne(name, folderToken); })).then(function (loaded) {
          loaded.sort(function (a, b) { return compareNames(a.name, b.name); });
          pages = loaded;
          renderReview();
        });
      }

      if (phase === "done") {
        doneResults = state.results || [];
        doneCancelled = !!state.cancelled;
        renderDone();
      }
    });
  }

  thumbSizeSlider.addEventListener("input", function (e) {
    thumbSize = parseInt(e.target.value, 10);
    thumbSizeValue.textContent = thumbSize + "px";
    applyThumbSize();
    if (phase === "review") renderReview();
  });

  thresholdSlider.addEventListener("input", function (e) {
    threshold = parseFloat(e.target.value);
    thresholdValue.textContent = threshold.toFixed(2);
    if (phase === "review") renderReview();
  });

  cropPercentSlider.addEventListener("input", function (e) {
    cropPercent = parseFloat(e.target.value);
    cropPercentValue.textContent = cropPercent + "%";
    if (phase === "review") renderReview();
  });

  fullPreviewInput.addEventListener("input", function (e) {
    var v = parseInt(e.target.value, 10);
    fullPreviewCount = isNaN(v) ? 0 : Math.max(0, v);
    if (phase === "review") renderReview();
  });

  resetBtn.addEventListener("click", function () {
    forcedSingles = new Set();
    excluded = new Set();
    if (phase === "review") renderReview();
  });

  continueBtn.addEventListener("click", function () {
    if (submitting) return;
    submitting = true;
    continueBtn.disabled = true;
    var prevText = continueBtn.textContent;
    continueBtn.textContent = "Sending…";

    var req;
    if (phase === "select") {
      req = fetch("/api/select", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected: Array.from(selectedFolders) }),
      });
    } else {
      req = fetch("/api/submit", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          forced_singles: Array.from(forcedSingles),
          excluded: Array.from(excluded),
          threshold: threshold,
          crop_percent: cropPercent,
          full_preview_count: fullPreviewCount,
          thumb_size: thumbSize,
        }),
      });
    }

    req.then(function () {
      return refreshState();
    }).then(function () {
      submitting = false;
    }).catch(function () {
      submitting = false;
      continueBtn.disabled = false;
      continueBtn.textContent = prevText;
      alert("Could not reach the local server. Check your terminal -- it may already have stopped.");
    });
  });

  cancelBtn.addEventListener("click", function () {
    if (submitting) return;
    if (!window.confirm("Cancel? Any chapters not yet completed will not be generated.")) return;
    fetch("/api/cancel", { method: "POST", cache: "no-store" })
      .then(function () { return refreshState(); })
      .catch(function () {
        alert("Could not reach the local server. Check your terminal -- it may already have stopped.");
      });
  });

  refreshState().catch(function (err) {
    loadingState.textContent = "Couldn't load: " + err;
  });
})();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(
        description="Merge manga pages into right-to-left landscape spreads."
    )
    parser.add_argument("-i", "--input", default=None, help="Input folder of pages (single-folder mode)")
    parser.add_argument("-o", "--output", default=None, help="Output folder (single-folder mode) / output parent (batch mode)")
    parser.add_argument("--batch", metavar="PARENT_DIR", default=None,
        help="Process every subfolder of PARENT_DIR that contains images, each into its own .cbz. "
             "Shows a checklist in the browser to pick which to include.")
    parser.add_argument("-g", "--gap", type=int, default=0, help="Gap in pixels between pages")
    parser.add_argument(
        "--gap-color", default="255,255,255",
        help="Gap/background color as R,G,B (default white)"
    )
    parser.add_argument(
        "--cover-alone", action="store_true",
        help="Keep the first page solo (as a cover), pairing starts from page 2+3"
    )
    parser.add_argument(
        "--aspect-threshold", type=float, default=1.15,
        help="Width/height ratio at or above which an image is treated as an "
             "already-merged spread and left untouched (default: 1.15)"
    )
    parser.add_argument(
        "--crop-percent", type=float, default=25.0,
        help="In review mode, %% of each page's width shown near the seam "
             "once past --full-preview-count (default: 25)"
    )
    parser.add_argument(
        "--full-preview-count", type=int, default=3,
        help="Number of leading output items shown at full size in review "
             "mode; everything after gets the cropped seam-only preview (default: 3)"
    )
    parser.add_argument(
        "--thumb-size", type=int, default=300,
        help="Thumbnail height in pixels in review mode -- purely a display "
             "preference, never affects generated output. Bump this up for "
             "large/high-DPI monitors. Live-adjustable too. (default: 300)"
    )
    parser.add_argument(
        "--force-single", default="",
        help="Comma-separated pages to force as solo (never paired). Accepts "
             "filenames (with/without extension) or 1-based page numbers, "
             "e.g. --force-single page5.png,12,cover"
    )
    parser.add_argument(
        "--force-single-file", default=None,
        help="Path to a text file listing additional forced-single pages, "
             "one per line (or comma-separated)."
    )
    parser.add_argument(
        "--review", action="store_true",
        help="Open a browser tab to visually review/adjust which pages stay "
             "solo or get excluded before generating anything."
    )
    parser.add_argument("--format", default="jpg", choices=["jpg", "png", "webp"], help="Output image format (default: jpg)")
    parser.add_argument("--quality", type=int, default=85, help="Image quality (1-100) if saving as jpg/webp (default: 85)")
    parser.add_argument("--cbz", action="store_true", help="Package output into a .cbz archive")
    parser.add_argument(
        "--cleanup-images", action="store_true",
        help="After a successful .cbz, delete the loose spread images this script "
             "generated (keeps just the .cbz). Never touches your original source pages."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan only, don't write files")
    args = parser.parse_args()

    try:
        gap_color = tuple(int(c) for c in args.gap_color.split(","))
        if len(gap_color) != 3:
            raise ValueError
    except ValueError:
        sys.exit("Error: --gap-color must be in R,G,B format, e.g. 255,255,255")
    args.gap_color_tuple = gap_color

    if args.batch:
        if args.input:
            print("Note: -i/--input is ignored when --batch is used (each chapter's own folder is used instead).")

        folders = discover_batch_folders(args.batch)
        if not folders:
            sys.exit(f"No subfolders with images found in {args.batch}")

        if args.dry_run:
            # Dry run in batch mode: just show what would be discovered and
            # the plan for each, without opening the browser or writing files.
            print(f"Found {len(folders)} folder(s) in {args.batch}:")
            for f in folders:
                print(f"  {f['name']}  ({f['count']} pages)" + (" [archive]" if f.get("is_archive") else ""))
                with resolved_folder(f) as rf:
                    pages = collect_pages(rf["path"])
                    plan, _flags = build_output_plan(pages, rf["path"], args.aspect_threshold, set(), set(), args.cover_alone)
                    for i, op in enumerate(plan):
                        if op[0] == "passthrough":
                            print(f"    spread_{i+1:03d}: PASSTHROUGH={op[1]}")
                        else:
                            print(f"    spread_{i+1:03d}: LEFT={op[1] or '(blank)'}  RIGHT={op[2]}")
            return

        if not args.review:
            print(f"Processing {len(folders)} folder(s) without review "
                  f"(plain sequential pairing, no browser). Add --review to pick per chapter.\n")
            results = []
            for f in folders:
                print(f"=== {f['name']} ===")
                with resolved_folder(f) as rf:
                    pages = collect_pages(rf["path"])
                    forced_singles = parse_force_single(args.force_single, args.force_single_file, pages)
                    if forced_singles:
                        ordered = [p for p in pages if p in forced_singles]
                        print(f"  Forcing {len(ordered)} page(s) to remain solo: {', '.join(ordered)}")
                    plan, flags = build_output_plan(pages, rf["path"], args.aspect_threshold, forced_singles, set(), args.cover_alone)
                    for p, already in flags.items():
                        if already:
                            print(f"  Detected already-merged spread: {p}")
                    out_dir, cbz_path = resolve_output_paths(args, f["name"], rf["path"], f.get("is_archive", False))
                    count = generate_outputs(
                        plan, rf["path"], out_dir, args.gap, args.gap_color_tuple, args.format,
                        args.quality, cbz_path if args.cbz else None, args.cleanup_images, args.dry_run,
                    )
                results.append((f["name"], count))
                print()
        else:
            results = run_batch_review_server(folders, args)

        if results:
            print(f"\n=== Batch complete: {len(results)} chapter(s) processed ===")
            for name, count in results:
                print(f"  {name}: {count} spread(s)")
        return

    # --- single-folder mode ---
    if not args.input or not args.output:
        parser.error("the following arguments are required: -i/--input, -o/--output (unless using --batch)")

    input_is_archive = is_archive_file(args.input)
    if input_is_archive:
        page_count = count_archive_pages(args.input)
        input_name = os.path.splitext(os.path.basename(os.path.normpath(args.input)))[0]
    elif os.path.isdir(args.input):
        page_count = len(collect_pages(args.input))
        input_name = os.path.basename(os.path.normpath(args.input)) or "pages"
    else:
        sys.exit(f"Error: input not found (expected a folder or a .cbz/.zip file): {args.input}")

    if not page_count:
        sys.exit(f"No supported images found in {args.input}")
    print(f"Found {page_count} pages.")

    pseudo_folder = {"name": input_name, "path": args.input, "count": page_count, "is_archive": input_is_archive}

    if args.review:
        # run_batch_review_server extracts/cleans up archives internally --
        # same machinery batch mode uses, just with a single-item queue.
        run_batch_review_server([pseudo_folder], args)
        return

    # Non-interactive: extract if needed, generate, clean up temp extraction.
    with resolved_folder(pseudo_folder) as rf:
        pages = collect_pages(rf["path"])

        forced_singles = parse_force_single(args.force_single, args.force_single_file, pages)
        if forced_singles:
            ordered = [p for p in pages if p in forced_singles]
            print(f"  Forcing {len(ordered)} page(s) to remain solo: {', '.join(ordered)}")

        plan, flags = build_output_plan(pages, rf["path"], args.aspect_threshold, forced_singles, set(), args.cover_alone)
        for p, already in flags.items():
            if already:
                print(f"  Detected already-merged spread: {p}")

        out_dir, cbz_path = resolve_output_paths(args, rf["name"], rf["path"], input_is_archive)
        out_dir = args.output  # single-folder mode always honors -o directly
        generate_outputs(
            plan, rf["path"], out_dir, args.gap, gap_color, args.format, args.quality,
            cbz_path if args.cbz else None, args.cleanup_images, args.dry_run,
        )


if __name__ == "__main__":
    main()
