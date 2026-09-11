"""
scan.py - Scans the project directory for Trace camera clips and builds manifest.json

Season order and team info are defined in player_config.json.
Run this whenever you add new game footage.
"""
import os
import json
import re
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
THUMBNAILS_DIR = OUTPUT_DIR / "thumbnails"
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"
CONFIG_FILE = BASE_DIR / "player_config.json"
GAME_DATES_FILE = BASE_DIR / "game_dates.json"

SKIP_DIRS = {'tools', 'viewer', 'output', '.git', '__pycache__'}


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
FFPROBE = FFMPEG.replace("ffmpeg.EXE", "ffprobe.exe").replace("ffmpeg.exe", "ffprobe.exe") if FFMPEG else None


def parse_game_folder(name):
    # "6. Fuquay-Varina vs Athens Drive"
    m = re.match(r'^(\d+)\.\s+(.+?)\s+vs\s+(.+)$', name)
    if m:
        return {"number": int(m.group(1)), "home_team": m.group(2).strip(), "away_team": m.group(3).strip()}
    # "2 FV vs Enloe"  (no period after number, abbreviated home team)
    m2 = re.match(r'^(\d+)\s+(.+?)\s+vs\s+(.+)$', name)
    if m2:
        return {"number": int(m2.group(1)), "home_team": m2.group(2).strip(), "away_team": m2.group(3).strip()}
    # "Fuquay-Varina vs Athens Drive"
    m3 = re.match(r'^(.+?)\s+vs\s+(.+)$', name)
    if m3:
        return {"number": None, "home_team": m3.group(1).strip(), "away_team": m3.group(2).strip()}
    return {"number": None, "home_team": name, "away_team": ""}


def parse_clip_filename(name):
    m = re.match(r'trace_moment_(\d+)_(home|away)_(\d+)_([a-f0-9]+)\.mp4', name, re.IGNORECASE)
    if m:
        return {"moment_id": m.group(1), "team_side": m.group(2).lower(),
                "jersey": int(m.group(3)), "hash": m.group(4)}
    m2 = re.match(r'extracted_moment_(\d+)_(\d+)s\.mp4', name, re.IGNORECASE)
    if m2:
        return {"moment_id": f"extracted_{m2.group(1)}_{m2.group(2)}",
                "team_side": "home", "jersey": 19, "hash": ""}
    return None


def parse_full_game_filename(name):
    # Matches both "-half-N" and "-period-N" variants
    m = re.match(r'Trace-FullGame-(\d{8})-(half|period)-(\d+)\.mp4', name, re.IGNORECASE)
    if m:
        try:
            date = datetime.strptime(m.group(1), '%Y%m%d').strftime('%Y-%m-%d')
        except ValueError:
            date = None
        return {"type": "full_game", "date": date, "half": int(m.group(3)),
                "label": f"Half {m.group(3)}"}
    return None


def parse_player_highlight_filename(name, jersey=None):
    """Recognize Trace player highlight downloads like 'FVHS vs CHS Ethan#19.mp4'."""
    stem = Path(name).stem
    # Must contain a jersey reference (#19, #3, etc.) or player name
    jersey_match = re.search(r'#(\d+)', stem)
    if jersey_match:
        label = stem  # use full filename stem as label
        return {"type": "player_cut", "half": None, "label": label,
                "jersey": int(jersey_match.group(1))}
    return None


def get_video_duration(filepath):
    if not FFPROBE:
        return None
    try:
        result = subprocess.run(
            [FFPROBE, '-v', 'quiet', '-print_format', 'json', '-show_streams', str(filepath)],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                return round(float(stream.get('duration', 0)), 1)
    except Exception:
        pass
    return None


def generate_thumbnail(video_path, thumbnail_path, at_second=4):
    if not FFMPEG:
        return False
    try:
        subprocess.run(
            [FFMPEG, '-ss', str(at_second), '-i', str(video_path),
             '-vframes', '1', '-q:v', '3', '-vf', 'scale=480:-1',
             str(thumbnail_path), '-y'],
            capture_output=True, timeout=30
        )
        return thumbnail_path.exists()
    except Exception:
        return False


def sort_game_key(d):
    m = re.match(r'^(\d+)[\.\s]', d.name)  # handle "10 FV vs..." and "10. FV vs..."
    return (int(m.group(1)) if m else 9999, d.name)


def scan_game_dir(game_dir, season_key, existing_clips):
    game_info = parse_game_folder(game_dir.name)
    game = {
        **game_info,
        "folder": game_dir.name,
        "relative_path": str(game_dir.relative_to(BASE_DIR)).replace('\\', '/'),
        "date": None,
        "clips": [],
        "player_highlights": [],
        "full_game_files": [],
        "compiled_reel": None
    }

    for f in sorted(game_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() != '.mp4':
            continue
        rel_path = str(f.relative_to(BASE_DIR)).replace('\\', '/')

        if f.name.lower().startswith('trace_moment_') or f.name.lower().startswith('extracted_moment_'):
            clip_info = parse_clip_filename(f.name)
            if clip_info:
                thumb_name = f"thumb_{clip_info['moment_id']}.jpg"
                thumb_path = THUMBNAILS_DIR / thumb_name
                if not thumb_path.exists():
                    print(f"  Generating thumbnail: {f.name}")
                    generate_thumbnail(f, thumb_path)
                saved = existing_clips.get(clip_info["moment_id"], {})
                game["clips"].append({
                    **clip_info,
                    "file": rel_path,
                    "filename": f.name,
                    "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                    "duration": get_video_duration(f),
                    "thumbnail": f"output/thumbnails/{thumb_name}" if thumb_path.exists() else None,
                    "selected": saved.get("selected", True),
                    "note": saved.get("note", "")
                })

        elif f.name.lower().startswith('trace-fullgame-'):
            fg_info = parse_full_game_filename(f.name)
            if fg_info:
                if fg_info.get('date') and not game.get('date'):
                    game['date'] = fg_info['date']
                game['full_game_files'].append({
                    "file": rel_path, "filename": f.name,
                    "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                    **fg_info
                })

        else:
            # Check for player highlight videos (e.g. "FVHS vs CHS Ethan#19.mp4")
            ph_info = parse_player_highlight_filename(f.name)
            if ph_info:
                safe_stem = re.sub(r'[^\w]', '_', f.stem)
                thumb_name = f"thumb_ph_{safe_stem}.jpg"
                thumb_path = THUMBNAILS_DIR / thumb_name
                if not thumb_path.exists():
                    print(f"  Generating thumbnail: {f.name}")
                    generate_thumbnail(f, thumb_path)
                game['player_highlights'].append({
                    "file": rel_path, "filename": f.name,
                    "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                    "duration": get_video_duration(f),
                    "thumbnail": f"output/thumbnails/{thumb_name}" if thumb_path.exists() else None,
                    **ph_info
                })

    # Check for pre-built reel
    slug = re.sub(r'[^\w]', '_', f"{season_key}_{game_dir.name}")
    reel_path = OUTPUT_DIR / "reels" / f"{slug}_reel.mp4"
    if reel_path.exists():
        game['compiled_reel'] = str(reel_path.relative_to(BASE_DIR)).replace('\\', '/')

    game["clips"].sort(key=lambda c: c["moment_id"])

    if game['clips'] or game['player_highlights'] or game['full_game_files']:
        return game
    return None


def scan():
    OUTPUT_DIR.mkdir(exist_ok=True)
    THUMBNAILS_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "reels").mkdir(exist_ok=True)

    # Preserve existing user edits
    existing_clips = {}
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE) as f:
                existing = json.load(f)
            for season in existing.get("seasons", []):
                for game in season.get("games", []):
                    for clip in game.get("clips", []):
                        existing_clips[clip["moment_id"]] = {
                            "selected": clip.get("selected", True),
                            "note": clip.get("note", "")
                        }
        except Exception:
            pass

    # Load player config
    config = {"name": "Ethan Flowers", "tabs": []}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
        except Exception:
            pass

    # Load manual game dates
    game_dates_config = {}
    if GAME_DATES_FILE.exists():
        try:
            with open(GAME_DATES_FILE) as f:
                game_dates_config = json.load(f)
        except Exception:
            pass

    manifest = {
        "player": {
            "name": config.get("name", "Ethan Flowers"),
            "tabs": config.get("tabs", []),
            "bio": config.get("bio", {})
        },
        "generated_at": datetime.now().isoformat(),
        "seasons": []
    }

    # Build seasons from the tab definitions in player_config
    for tab in config.get("tabs", []):
        team_folder = BASE_DIR / tab["folder"]
        season_folder = team_folder / tab["season_folder"]

        season = {
            "name": f"{tab['team']} — {tab['season_folder']}",
            "team": tab["team"],
            "year": tab["year"],
            "season_folder": tab["season_folder"],
            "jersey": tab.get("jersey"),
            "position": tab.get("position", ""),
            "former": tab.get("former", False),
            "league": tab.get("league", ""),
            "games": []
        }

        if season_folder.exists():
            season_key = re.sub(r'[^\w]', '_', f"{tab['folder']}_{tab['season_folder']}")
            # Look up manual dates for this team/season
            team_dates = game_dates_config.get(tab['team'], {}).get(tab['season_folder'], {})
            for game_dir in sorted(season_folder.iterdir(), key=sort_game_key):
                if not game_dir.is_dir():
                    continue
                game = scan_game_dir(game_dir, season_key, existing_clips)
                if game:
                    # Manual date always wins over filename-embedded date
                    if game.get('number'):
                        manual = team_dates.get(str(game['number']))
                        if manual:
                            game['date'] = manual
                    season["games"].append(game)
                else:
                    # Folder exists but no footage yet — add as placeholder tile
                    game_info = parse_game_folder(game_dir.name)
                    num = game_info.get('number')
                    manual = team_dates.get(str(num)) if num else None
                    season['games'].append({
                        **game_info,
                        "folder": game_dir.name,
                        "relative_path": str(game_dir.relative_to(BASE_DIR)).replace('\\', '/'),
                        "date": manual,
                        "clips": [],
                        "player_highlights": [],
                        "full_game_files": [],
                        "compiled_reel": None,
                        "placeholder": True
                    })

            # Add placeholder tiles for all missing game numbers:
            # 1. Any number in game_dates.json (known schedule)
            # 2. Any gap between 1 and the highest game number found on disk
            found_numbers = {g['number'] for g in season['games'] if g.get('number')}
            max_found = max(found_numbers, default=0)
            all_expected = set(range(1, max_found + 1))
            # Also include any numbers from game_dates config beyond what was found
            for num_str in team_dates:
                all_expected.add(int(num_str))
            for num in all_expected:
                if num not in found_numbers:
                    date = team_dates.get(str(num))
                    season['games'].append({
                        "number": num,
                        "home_team": "",
                        "away_team": "",
                        "folder": None,
                        "relative_path": None,
                        "date": date,
                        "clips": [],
                        "player_highlights": [],
                        "full_game_files": [],
                        "compiled_reel": None,
                        "placeholder": True
                    })

            # Sort all games by number
            season['games'].sort(key=lambda g: (g.get('number') or 9999, g.get('folder') or ''))

        manifest["seasons"].append(season)

    with open(MANIFEST_FILE, 'w') as f:
        json.dump(manifest, f, indent=2)

    total_clips = sum(len(g['clips']) for s in manifest['seasons'] for g in s['games'])
    total_games = sum(len(s['games']) for s in manifest['seasons'])
    print(f"\nDone. {len(manifest['seasons'])} tab(s) | {total_games} game(s) with footage | {total_clips} clip(s)")
    print(f"Manifest: {MANIFEST_FILE}")
    return manifest


if __name__ == '__main__':
    print(f"FFmpeg: {FFMPEG or 'NOT FOUND — thumbnails will be skipped'}")
    print("Scanning clips...\n")
    scan()
