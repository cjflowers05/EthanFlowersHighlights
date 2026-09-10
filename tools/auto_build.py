"""
auto_build.py - Full auto pipeline: full game recordings → highlight reel.

For each game that has full game files but no trace clips yet:
  1. Runs scene-detection on the full game to extract highlight moments
  2. Re-scans to pick up the new clips
  3. Compiles them into a highlight reel

Also compiles reels for games that already have Trace moment clips.

Usage:
  python tools/auto_build.py                        # Process all games
  python tools/auto_build.py --season "2026"        # One season
  python tools/auto_build.py --game 2               # One game number
  python tools/auto_build.py --clips-only           # Skip extraction, only compile reels
  python tools/auto_build.py --sensitivity 0.3      # More clips (lower = more sensitive)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
REELS_DIR = OUTPUT_DIR / "reels"
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"
TOOLS_DIR = Path(__file__).parent


def find_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    winget_base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget_base.exists():
        for pkg in winget_base.glob("Gyan.FFmpeg*"):
            for f in pkg.rglob("ffmpeg.exe"):
                return str(f)
    return None


FFMPEG = find_ffmpeg()


def run_scan():
    print("\n[Scan] Scanning for clips...")
    result = subprocess.run([sys.executable, str(TOOLS_DIR / "scan.py")], cwd=str(BASE_DIR))
    return result.returncode == 0


def load_manifest():
    if not MANIFEST_FILE.exists():
        return None
    with open(MANIFEST_FILE) as f:
        return json.load(f)


def extract_moments_from_game(game, sensitivity=0.35):
    """Run extract_moments.py on each full game file for this game."""
    full_files = game.get("full_game_files", [])
    if not full_files:
        return False

    any_extracted = False
    for fg in full_files:
        video_path = BASE_DIR / fg["file"].replace("/", os.sep)
        if not video_path.exists():
            print(f"  [!] File not found: {fg['file']}")
            continue

        half = fg.get("half", "?")
        print(f"  [Extract] Half {half}: {video_path.name}")
        result = subprocess.run([
            sys.executable, str(TOOLS_DIR / "extract_moments.py"),
            str(video_path),
            "--sensitivity", str(sensitivity),
            "--output-dir", str(video_path.parent)
        ], cwd=str(BASE_DIR))

        if result.returncode == 0:
            any_extracted = True

    return any_extracted


def build_reel_for_game(game, season, output_path):
    """Concatenate all selected clips for a game into a reel."""
    clips = [c for c in game.get("clips", []) if c.get("selected", True)]
    if not clips:
        print(f"  [Skip] No clips to compile.")
        return False

    clip_files = []
    for c in clips:
        fp = BASE_DIR / c["file"].replace("/", os.sep)
        if fp.exists():
            clip_files.append(fp)
        else:
            print(f"  [!] Missing clip: {c['file']}")

    if not clip_files:
        return False

    print(f"  [Build] Compiling {len(clip_files)} clip(s) → {output_path.name}")
    REELS_DIR.mkdir(exist_ok=True)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
        for fp in clip_files:
            p = str(fp).replace('\\', '/').replace("'", "\\'")
            tmp.write(f"file '{p}'\n")
        concat_file = tmp.name

    try:
        result = subprocess.run([
            FFMPEG, '-f', 'concat', '-safe', '0', '-i', concat_file,
            '-c', 'copy', '-movflags', '+faststart',
            str(output_path), '-y'
        ], capture_output=True, timeout=600)

        if result.returncode == 0 and output_path.exists():
            size_mb = output_path.stat().st_size / 1024 / 1024
            print(f"  [Done] {output_path.name} ({size_mb:.1f} MB)")
            return True
        else:
            print(f"  [Error] FFmpeg failed: {result.stderr[-500:]}")
            return False
    finally:
        os.unlink(concat_file)


def main():
    parser = argparse.ArgumentParser(description="Auto-build highlight reels from full game recordings")
    parser.add_argument('--season', help="Filter by season (partial match)")
    parser.add_argument('--game', type=int, help="Filter by game number")
    parser.add_argument('--clips-only', action='store_true', help="Skip moment extraction; only compile reels")
    parser.add_argument('--sensitivity', type=float, default=0.35,
                        help="Scene detection sensitivity (0.1-0.9, lower=more clips, default=0.35)")
    args = parser.parse_args()

    if not FFMPEG:
        print("ERROR: FFmpeg not found. Please install it first.")
        sys.exit(1)

    print("=" * 55)
    print("  Auto-Build Highlight Reels")
    print("=" * 55)

    # Initial scan
    run_scan()
    manifest = load_manifest()
    if not manifest:
        print("No manifest found after scan.")
        sys.exit(1)

    processed = []
    needs_rescan = False

    for season in manifest["seasons"]:
        if args.season and args.season.lower() not in season["name"].lower():
            continue

        for game in season["games"]:
            if args.game and game.get("number") != args.game:
                continue

            game_label = f"{season['team']} {season['year']} · Game {game.get('number','?')} vs {game.get('away_team','?')}"
            has_full_game = bool(game.get("full_game_files"))
            has_clips = bool(game.get("clips"))

            print(f"\n{'─'*50}")
            print(f"  {game_label}")
            print(f"  Full game: {'yes' if has_full_game else 'no'} | Clips: {len(game.get('clips',[]))}")

            # Step 1: Extract moments from full game if no clips yet
            if has_full_game and not has_clips and not args.clips_only:
                print(f"  No clips yet — extracting moments from full game...")
                extracted = extract_moments_from_game(game, sensitivity=args.sensitivity)
                if extracted:
                    needs_rescan = True
                else:
                    print(f"  [!] Extraction produced no clips.")
                    continue
            elif not has_clips:
                print(f"  [Skip] No clips and no full game file. Add footage first.")
                continue

            processed.append((season, game, game_label))

    # Re-scan if we extracted new clips
    if needs_rescan:
        print(f"\n{'─'*50}")
        run_scan()
        manifest = load_manifest()
        # Rebuild game list after rescan
        processed_keys = {(s["name"], g.get("number")) for s, g, _ in processed}
        processed = []
        for season in manifest["seasons"]:
            for game in season["games"]:
                if (season["name"], game.get("number")) in processed_keys:
                    game_label = f"{season['team']} {season['year']} · Game {game.get('number','?')} vs {game.get('away_team','?')}"
                    processed.append((season, game, game_label))

    # Step 2: Build reels
    built = []
    for season, game, game_label in processed:
        slug = re.sub(r'[^\w]', '_', f"{season['team']}_{season['year']}_game{game.get('number','')}")
        output_path = REELS_DIR / f"{slug}_highlights.mp4"

        print(f"\n  Building reel: {game_label}")
        if build_reel_for_game(game, season, output_path):
            built.append((game_label, output_path))

    # Step 3: Build a combined season reel if multiple games were built
    if len(built) >= 2:
        print(f"\n{'─'*50}")
        print(f"  Building combined season reel from {len(built)} game(s)...")
        season_slug = re.sub(r'[^\w]', '_', f"season_highlights_{len(built)}_games")
        season_reel = REELS_DIR / f"{season_slug}.mp4"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
            for _, reel_path in built:
                p = str(reel_path).replace('\\', '/').replace("'", "\\'")
                tmp.write(f"file '{p}'\n")
            concat_file = tmp.name

        try:
            result = subprocess.run([
                FFMPEG, '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-c', 'copy', '-movflags', '+faststart',
                str(season_reel), '-y'
            ], capture_output=True, timeout=600)
            if result.returncode == 0:
                size_mb = season_reel.stat().st_size / 1024 / 1024
                print(f"  [Done] {season_reel.name} ({size_mb:.1f} MB)")
                built.append(("Season Highlights", season_reel))
        finally:
            os.unlink(concat_file)

    # Summary
    print(f"\n{'='*55}")
    print(f"  Done! Built {len(built)} reel(s):")
    for label, path in built:
        size_mb = path.stat().st_size / 1024 / 1024 if path.exists() else 0
        print(f"    · {path.name} ({size_mb:.1f} MB)")
    print(f"\n  All reels saved to: {REELS_DIR}")
    print(f"  View at: http://localhost:8080")
    print("=" * 55)


if __name__ == '__main__':
    main()
