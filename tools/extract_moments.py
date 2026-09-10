"""
extract_moments.py - Analyzes a full game video and extracts highlight clips.

Uses FFmpeg motion/scene detection to find high-action segments (shots, runs,
dribbles) and saves them as individual clip files, like Trace moment clips.

Usage:
  python tools/extract_moments.py "path/to/full_game.mp4"
  python tools/extract_moments.py "path/to/full_game.mp4" --sensitivity 0.4 --min-duration 8 --max-duration 45
  python tools/extract_moments.py "path/to/full_game.mp4" --timestamps 5:30 12:45 34:10   # manual timestamps
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


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
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe") if FFMPEG else None


def get_video_duration(filepath):
    if not FFPROBE:
        return None
    try:
        result = subprocess.run(
            [FFPROBE, '-v', 'quiet', '-print_format', 'json',
             '-show_format', str(filepath)],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        return float(data.get('format', {}).get('duration', 0))
    except Exception:
        return None


def detect_scenes(video_path, threshold=0.35):
    """
    Use FFmpeg scene detection to find timestamps where the scene changes significantly.
    threshold: 0.0-1.0, lower = more sensitive (more scenes detected)
    Returns list of timestamps in seconds.
    """
    if not FFMPEG:
        return []

    print(f"  Analyzing video for scene changes (threshold={threshold})...")
    cmd = [
        FFMPEG, '-i', str(video_path),
        '-vf', f"select='gt(scene,{threshold})',showinfo",
        '-vsync', 'vfr',
        '-f', 'null', '-'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    timestamps = []
    # Parse "pts_time:" from showinfo output
    for line in result.stderr.split('\n'):
        if 'pts_time:' in line:
            m = re.search(r'pts_time:([\d.]+)', line)
            if m:
                timestamps.append(float(m.group(1)))

    return sorted(timestamps)


def detect_motion_peaks(video_path, sample_rate=2):
    """
    Analyze motion vectors to find high-activity periods.
    Returns list of (timestamp, motion_score) tuples.
    sample_rate: analyze every N seconds
    """
    if not FFMPEG:
        return []

    print(f"  Analyzing motion intensity...")
    # Use mpdecimate filter to detect motion - count frames that have significant diff
    cmd = [
        FFMPEG, '-i', str(video_path),
        '-vf', f"fps=1/{sample_rate},mpdecimate=hi=64*12:lo=64*5:frac=0.33,showinfo",
        '-vsync', 'vfr',
        '-f', 'null', '-'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    # Parse which frames were NOT decimated (kept = high motion)
    kept_times = []
    for line in result.stderr.split('\n'):
        if 'showinfo' in line and 'pts_time:' in line:
            m = re.search(r'pts_time:([\d.]+)', line)
            if m:
                kept_times.append(float(m.group(1)))

    return kept_times


def cluster_timestamps(timestamps, gap=8.0):
    """
    Group nearby timestamps into segments.
    Returns list of (start, end) tuples.
    """
    if not timestamps:
        return []

    sorted_ts = sorted(timestamps)
    segments = []
    seg_start = sorted_ts[0]
    seg_end = sorted_ts[0]

    for ts in sorted_ts[1:]:
        if ts - seg_end <= gap:
            seg_end = ts
        else:
            segments.append((seg_start, seg_end))
            seg_start = ts
            seg_end = ts
    segments.append((seg_start, seg_end))
    return segments


def extract_clip(video_path, start, end, output_path, pad_before=5, pad_after=5):
    """Extract a clip from video_path from start to end (seconds), with padding."""
    if not FFMPEG:
        return False

    actual_start = max(0, start - pad_before)
    actual_end = end + pad_after
    duration = actual_end - actual_start

    cmd = [
        FFMPEG,
        '-ss', str(actual_start),
        '-i', str(video_path),
        '-t', str(duration),
        '-c:v', 'libx264', '-crf', '23', '-preset', 'fast',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        str(output_path), '-y'
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    return result.returncode == 0 and output_path.exists()


def parse_timestamp(ts_str):
    """Parse 'MM:SS' or 'H:MM:SS' into seconds."""
    parts = ts_str.strip().split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return float(ts_str)


def main():
    parser = argparse.ArgumentParser(description="Extract highlight clips from a full game video")
    parser.add_argument('video', help="Path to the full game video file")
    parser.add_argument('--sensitivity', type=float, default=0.35,
                        help="Scene change sensitivity 0.1-0.9 (lower=more clips, default=0.35)")
    parser.add_argument('--min-duration', type=float, default=6,
                        help="Minimum clip duration in seconds (default=6)")
    parser.add_argument('--max-duration', type=float, default=40,
                        help="Maximum clip duration in seconds (default=40)")
    parser.add_argument('--pad-before', type=float, default=4,
                        help="Seconds to add before each moment (default=4)")
    parser.add_argument('--pad-after', type=float, default=5,
                        help="Seconds to add after each moment (default=5)")
    parser.add_argument('--timestamps', nargs='+',
                        help="Manual timestamps (MM:SS) to extract instead of auto-detection")
    parser.add_argument('--output-dir',
                        help="Output directory (default: same folder as video)")
    args = parser.parse_args()

    if not FFMPEG:
        print("ERROR: FFmpeg not found.")
        sys.exit(1)

    video_path = Path(args.video)
    if not video_path.is_absolute():
        video_path = BASE_DIR / video_path
    if not video_path.exists():
        print(f"ERROR: Video not found: {video_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else video_path.parent
    output_dir.mkdir(exist_ok=True)

    print(f"Video: {video_path.name}")
    duration = get_video_duration(video_path)
    if duration:
        mins = int(duration // 60)
        secs = int(duration % 60)
        print(f"Duration: {mins}:{secs:02d}")

    if args.timestamps:
        # Manual timestamp mode
        print(f"\nExtracting {len(args.timestamps)} manually-specified moments...")
        moments = [(parse_timestamp(ts), parse_timestamp(ts) + 10) for ts in args.timestamps]
    else:
        # Auto-detection mode
        print("\nAuto-detecting highlight moments...")
        scene_times = detect_scenes(video_path, threshold=args.sensitivity)
        print(f"  Found {len(scene_times)} scene changes")

        if not scene_times:
            print("No scenes detected. Try lowering --sensitivity (e.g. 0.2)")
            sys.exit(1)

        # Cluster nearby scene changes into segments
        raw_segments = cluster_timestamps(scene_times, gap=6.0)

        # Filter by duration
        moments = []
        for start, end in raw_segments:
            seg_len = end - start + args.pad_before + args.pad_after
            if args.min_duration <= seg_len <= args.max_duration:
                moments.append((start, end))

        print(f"  Identified {len(moments)} highlight moments (after duration filter)")

    if not moments:
        print("\nNo moments to extract. Adjust --sensitivity or use --timestamps.")
        sys.exit(1)

    # Extract clips
    extracted = []
    for i, (start, end) in enumerate(moments, 1):
        clip_name = f"extracted_moment_{i:03d}_{int(start):05d}s.mp4"
        clip_path = output_dir / clip_name

        start_str = f"{int(start)//60}:{int(start)%60:02d}"
        end_str = f"{int(end)//60}:{int(end)%60:02d}"
        print(f"  [{i}/{len(moments)}] {start_str} - {end_str}  →  {clip_name}")

        if extract_clip(video_path, start, end, clip_path, args.pad_before, args.pad_after):
            extracted.append(clip_path)
        else:
            print(f"    WARNING: Failed to extract clip {i}")

    print(f"\nExtracted {len(extracted)} clips to: {output_dir}")
    print("\nNext step: run  python tools/scan.py  to update the manifest")


if __name__ == '__main__':
    main()
