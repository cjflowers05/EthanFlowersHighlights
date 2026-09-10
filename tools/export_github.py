"""
export_github.py - Export the viewer as a static GitHub Pages site.

Since GitHub Pages can't serve large video files, this exports the viewer
with YouTube URLs embedded (from upload_log.json). Videos not yet on YouTube
show a placeholder with a link to upload them.

Usage:
  python tools/export_github.py                  # export to docs/ folder
  python tools/export_github.py --out my_site    # custom output folder
  python tools/export_github.py --open           # open in browser after export

Then push the repo to GitHub and enable Pages on the docs/ folder:
  git add docs/
  git commit -m "Update highlights site"
  git push
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
VIEWER_DIR = BASE_DIR / "viewer"
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"
UPLOAD_LOG = OUTPUT_DIR / "upload_log.json"


def load_upload_log():
    """Return dict of filename → YouTube URL from upload_log.json."""
    if not UPLOAD_LOG.exists():
        return {}
    with open(UPLOAD_LOG) as f:
        try:
            log = json.load(f)
        except Exception:
            return {}
    result = {}
    for entry in log:
        fname = entry.get("file", "")
        url = entry.get("url", "")
        if fname and url:
            result[fname] = url
    return result


def youtube_embed_url(watch_url):
    """Convert https://youtube.com/watch?v=ID to embed URL."""
    m = re.search(r'[?&]v=([A-Za-z0-9_-]+)', watch_url)
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}"
    return watch_url


def build_static_manifest(manifest, upload_log):
    """
    Add youtube_url and youtube_embed fields to every video in the manifest.
    Videos not yet uploaded get youtube_url: null.
    """
    import copy
    m = copy.deepcopy(manifest)

    for season in m["seasons"]:
        for game in season["games"]:
            # Player highlight files
            for ph in game.get("player_highlights", []):
                yt = upload_log.get(ph["filename"])
                ph["youtube_url"] = yt
                ph["youtube_embed"] = youtube_embed_url(yt) if yt else None

            # Full game files
            for fg in game.get("full_game_files", []):
                yt = upload_log.get(fg["filename"])
                fg["youtube_url"] = yt
                fg["youtube_embed"] = youtube_embed_url(yt) if yt else None

            # Individual clips (trace_moment files)
            for clip in game.get("clips", []):
                yt = upload_log.get(clip["filename"])
                clip["youtube_url"] = yt
                clip["youtube_embed"] = youtube_embed_url(yt) if yt else None

    m["static_export"] = True
    m["exported_at"] = datetime.now().isoformat()
    return m


def copy_thumbnails(src_dir, dest_dir):
    """Copy thumbnail images to the export folder."""
    thumbs_src = src_dir / "output" / "thumbnails"
    thumbs_dest = dest_dir / "output" / "thumbnails"
    if thumbs_src.exists():
        thumbs_dest.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in thumbs_src.glob("*.jpg"):
            shutil.copy2(f, thumbs_dest / f.name)
            copied += 1
        print(f"  Copied {copied} thumbnails")
    else:
        print("  No thumbnails to copy")


def patch_viewer_for_static(html_content, manifest_json):
    """
    Embed the manifest directly into the HTML so it works without a server.
    Patches the async loadManifest() to use window.__STATIC_MANIFEST__ first.
    """
    # Replace the fetch call inside loadManifest to check static data first
    old_fetch = "    const res = await fetch(`${API}/api/manifest`);\n    if (!res.ok) throw new Error(await res.text());\n    manifest = await res.json();"
    new_fetch = "    if (window.__STATIC_MANIFEST__) { manifest = window.__STATIC_MANIFEST__; render(); return; }\n    const res = await fetch(`${API}/api/manifest`);\n    if (!res.ok) throw new Error(await res.text());\n    manifest = await res.json();"

    if old_fetch in html_content:
        html_content = html_content.replace(old_fetch, new_fetch)
    else:
        # Fallback: patch loadManifest to always use static manifest when available
        html_content = html_content.replace(
            'async function loadManifest() {',
            'async function loadManifest() {\n  if (window.__STATIC_MANIFEST__) { manifest = window.__STATIC_MANIFEST__; render(); return; }'
        )

    # Inject the manifest data BEFORE the main <script> block so it's defined
    # when loadManifest() runs — injecting after </body> is too late.
    inline_manifest = f"window.__STATIC_MANIFEST__ = {manifest_json};"
    html_content = html_content.replace(
        '<script>\nconst API',
        f'<script>\n{inline_manifest}\n</script>\n<script>\nconst API'
    )

    return html_content


def main():
    import argparse
    import webbrowser

    parser = argparse.ArgumentParser(description="Export viewer as static GitHub Pages site")
    parser.add_argument("--out", default="docs", help="Output folder (default: docs/)")
    parser.add_argument("--open", action="store_true", help="Open in browser after export")
    args = parser.parse_args()

    out_dir = BASE_DIR / args.out
    print(f"\nExporting static site to: {out_dir}")

    if not MANIFEST_FILE.exists():
        print("ERROR: No manifest found. Run: python tools/scan.py first.")
        sys.exit(1)

    with open(MANIFEST_FILE) as f:
        manifest = json.load(f)

    upload_log = load_upload_log()
    yt_count = len(upload_log)
    print(f"  Found {yt_count} YouTube upload(s) in log")

    # Build static manifest with YouTube URLs
    static_manifest = build_static_manifest(manifest, upload_log)
    manifest_json = json.dumps(static_manifest, indent=2)

    # Create output directory
    out_dir.mkdir(exist_ok=True)

    # Read and patch the viewer HTML
    viewer_html = (VIEWER_DIR / "index.html").read_text(encoding="utf-8")
    patched_html = patch_viewer_for_static(viewer_html, manifest_json)

    # Write index.html
    (out_dir / "index.html").write_text(patched_html, encoding="utf-8")
    print("  Written: docs/index.html")

    # Copy thumbnails
    copy_thumbnails(BASE_DIR, out_dir)

    # Write a manifest.json for reference
    (out_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")

    # Write .nojekyll so GitHub Pages serves files as-is
    (out_dir / ".nojekyll").write_text("")

    # Summary
    total_yt = sum(
        1 for s in static_manifest["seasons"]
        for g in s["games"]
        for ph in g.get("player_highlights", [])
        if ph.get("youtube_url")
    )
    total_ph = sum(
        len(g.get("player_highlights", []))
        for s in static_manifest["seasons"]
        for g in s["games"]
    )

    print(f"\n  Player highlights with YouTube links: {total_yt}/{total_ph}")
    if total_ph > total_yt:
        missing = total_ph - total_yt
        print(f"  [!] {missing} video(s) not yet on YouTube - upload them first:")
        print(f"     python tools/upload_youtube.py --reels")

    print("Done! Static site ready at: " + str(out_dir) + "/")
    print("")
    print("To publish: git add docs/ && git commit -m 'Update site' && git push")
    print("Live at: https://cjflowers05.github.io/EthanFlowersHighlights/")

    if args.open:
        webbrowser.open(str(out_dir / "index.html"))


if __name__ == "__main__":
    main()
