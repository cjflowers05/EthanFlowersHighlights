"""
build_reel.py - Compiles selected clips into a highlight reel using FFmpeg.

Usage:
  python tools/build_reel.py                          # Build a full season reel (all selected clips)
  python tools/build_reel.py --game "6"               # Build reel for game #6
  python tools/build_reel.py --season "Fuquay Varina Bengals"
  python tools/build_reel.py --output my_reel.mp4
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
REELS_DIR = OUTPUT_DIR / "reels"
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"


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


def build_reel(clip_files, output_path, title=None):
    """
    Concatenate clip_files into output_path using FFmpeg concat demuxer.
    clip_files: list of absolute Path objects
    """
    if not FFMPEG:
        print("ERROR: FFmpeg not found. Cannot build reel.")
        return False

    if not clip_files:
        print("No clips selected.")
        return False

    REELS_DIR.mkdir(exist_ok=True)

    # Write concat list file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
        for clip in clip_files:
            # FFmpeg concat format requires forward slashes and escaped single quotes
            path_str = str(clip).replace('\\', '/').replace("'", "\\'")
            tmp.write(f"file '{path_str}'\n")
        concat_file = tmp.name

    print(f"\nBuilding reel from {len(clip_files)} clip(s)...")
    print(f"Output: {output_path}\n")

    try:
        cmd = [
            FFMPEG,
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',          # Copy streams (fast, no re-encoding)
            '-movflags', '+faststart',  # Web-optimized MP4
            str(output_path),'-y'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("FFmpeg error:")
            print(result.stderr[-2000:])
            return False
        print(f"Success! Reel saved to:\n  {output_path}")
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"  Size: {size_mb:.1f} MB")
        return True
    finally:
        os.unlink(concat_file)


def load_manifest():
    if not MANIFEST_FILE.exists():
        print("No manifest found. Run: python tools/scan.py first.")
        return None
    with open(MANIFEST_FILE) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Build a soccer highlight reel")
    parser.add_argument('--season', help="Season name (partial match OK)")
    parser.add_argument('--game', help="Game number or folder name (partial match OK)")
    parser.add_argument('--output', help="Output filename (saved in output/reels/)")
    parser.add_argument('--all', action='store_true', help="Include ALL clips (ignore selected flag)")
    args = parser.parse_args()

    manifest = load_manifest()
    if not manifest:
        return

    clip_files = []
    label_parts = []

    for season in manifest['seasons']:
        if args.season and args.season.lower() not in season['name'].lower():
            continue
        for game in season['games']:
            if args.game:
                game_str = str(game.get('number', '')) + ' ' + game.get('folder', '')
                if args.game.lower() not in game_str.lower():
                    continue

            selected_clips = [
                c for c in game['clips']
                if args.all or c.get('selected', True)
            ]

            if selected_clips:
                label_parts.append(f"Game {game.get('number', '?')} ({game.get('away_team', '?')}): {len(selected_clips)} clips")
                for clip in selected_clips:
                    clip_path = BASE_DIR / clip['file'].replace('/', os.sep)
                    if clip_path.exists():
                        clip_files.append(clip_path)
                    else:
                        print(f"  WARNING: Missing file: {clip['file']}")

    if not clip_files:
        print("No clips found for the given filters (check --season / --game / selection flags).")
        return

    print(f"Clips to compile:")
    for l in label_parts:
        print(f"  {l}")

    # Determine output filename
    if args.output:
        out_name = args.output if args.output.endswith('.mp4') else args.output + '.mp4'
    elif args.game:
        out_name = re.sub(r'[^\w]', '_', f"game_{args.game}_reel") + ".mp4"
    elif args.season:
        out_name = re.sub(r'[^\w]', '_', f"{args.season}_reel") + ".mp4"
    else:
        out_name = "season_highlights.mp4"

    output_path = REELS_DIR / out_name
    build_reel(clip_files, output_path)


if __name__ == '__main__':
    main()
