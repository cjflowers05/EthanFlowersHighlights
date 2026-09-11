"""
upload_youtube.py - Upload highlight reels and full game footage to YouTube.

Supports uploading:
  --reels        Compiled highlight reels (output/reels/*.mp4)
  --fullgame     Raw full game recordings (Trace-FullGame-*.mp4)
  --all          Both reels and full games

FIRST-TIME SETUP (do this once):
  1. Go to https://console.cloud.google.com/
  2. Create a project → enable "YouTube Data API v3"
  3. Credentials → OAuth 2.0 Client ID → Desktop app → download JSON
  4. Save as: D:\Projects\Ethan Soccer Highlights\tools\client_secrets.json
  5. Run this script — browser opens once for auth, token is saved

Usage:
  python tools/upload_youtube.py --list              # show what's available
  python tools/upload_youtube.py --reels             # upload compiled reels
  python tools/upload_youtube.py --fullgame          # upload all full game recordings
  python tools/upload_youtube.py --all               # upload everything
  python tools/upload_youtube.py --file path/to.mp4  # upload a specific file
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 output so Unicode progress characters print on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).parent.parent
REELS_DIR = BASE_DIR / "output" / "reels"
SECRETS_FILE = Path(__file__).parent / "client_secrets.json"
TOKEN_FILE = Path(__file__).parent / "youtube_token.json"
LOG_FILE = BASE_DIR / "output" / "upload_log.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_credentials():
    import threading
    import webbrowser
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not SECRETS_FILE.exists():
                print(f"""
ERROR: client_secrets.json not found.

First-time setup:
  1. https://console.cloud.google.com/ → new project
  2. APIs & Services → Library → enable "YouTube Data API v3"
  3. Credentials → Create → OAuth Client ID → Desktop app → Download JSON
  4. Save as: {SECRETS_FILE}
  5. Re-run this script.
""")
                sys.exit(1)

            PORT = 58080
            auth_code = [None]
            auth_done = threading.Event()

            class _Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    params = parse_qs(urlparse(self.path).query)
                    if 'code' in params:
                        auth_code[0] = params['code'][0]
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html')
                        self.end_headers()
                        self.wfile.write(b'<h2>Authorization complete! You can close this tab.</h2>')
                        auth_done.set()
                    else:
                        self.send_response(400)
                        self.end_headers()
                def log_message(self, *args):
                    pass

            server = HTTPServer(('localhost', PORT), _Handler)

            flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
            flow.redirect_uri = f'http://localhost:{PORT}/'
            auth_url, _ = flow.authorization_url(access_type='offline', prompt='consent')

            def _serve():
                server.handle_request()
                server.server_close()

            t = threading.Thread(target=_serve, daemon=True)
            t.start()

            print(f"\nOpening browser for Google authorization...")
            print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
            webbrowser.open(auth_url)
            print("Waiting for you to approve (120s timeout)...")

            auth_done.wait(timeout=120)
            t.join(timeout=5)

            if not auth_code[0]:
                print("Authorization timed out. Re-run the script and approve quickly.")
                sys.exit(1)

            flow.fetch_token(code=auth_code[0])
            creds = flow.credentials

        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
        print("  Authorization saved — future uploads won't need browser login.")

    return creds


def upload_video(file_path, title, description="", privacy="unlisted"):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    size_mb = file_path.stat().st_size / 1024 / 1024
    print(f"\n  Uploading: {file_path.name}")
    print(f"  Size:      {size_mb:.0f} MB")
    print(f"  Title:     {title}")
    print(f"  Privacy:   {privacy}  (only people with the link can view)")
    print()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["soccer", "highlights", "Ethan Flowers", "Fuquay Varina"],
            "categoryId": "17"  # Sports
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(file_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 8  # 8 MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    last_pct = -1
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct != last_pct:
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                print(f"\r  [{bar}] {pct}%  ", end="", flush=True)
                last_pct = pct

    print(f"\r  [{'█'*20}] 100%  ")
    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"  ✓ {url}")
    return url


def save_to_log(file_path, url, title, kind, privacy):
    log = []
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            try:
                log = json.load(f)
            except Exception:
                log = []
    log.append({
        "kind": kind,
        "file": file_path.name,
        "url": url,
        "title": title,
        "privacy": privacy,
        "uploaded_at": datetime.now().isoformat()
    })
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)


def already_uploaded(file_path):
    if not LOG_FILE.exists():
        return None
    with open(LOG_FILE) as f:
        try:
            log = json.load(f)
        except Exception:
            return None
    for entry in log:
        if entry.get("file") == file_path.name:
            return entry.get("url")
    return None


def make_title_reel(file_path):
    stem = file_path.stem
    parts = stem.replace("_", " ").title()
    return f"Ethan Flowers | {parts}"


def make_title_fullgame(folder_name, filename):
    # "2 FV vs Enloe" / "Trace-FullGame-20260910-period-1.mp4"
    m = re.match(r'Trace-FullGame-(\d{8})-(half|period)-(\d+)\.mp4', filename, re.IGNORECASE)
    half_label = ""
    if m:
        try:
            date_str = datetime.strptime(m.group(1), '%Y%m%d').strftime('%b %d, %Y')
        except Exception:
            date_str = m.group(1)
        half_label = f" — Half {m.group(3)}"
        date_part = f" ({date_str})"
    else:
        date_part = ""
    game = re.sub(r'^\d+[\.\s]+', '', folder_name).strip()
    return f"Ethan Flowers | {game}{half_label}{date_part} [Full Game]"


def find_full_game_files():
    """Find all Trace-FullGame-*.mp4 files across the project."""
    results = []
    skip = {'tools', 'viewer', 'output', '.git', '__pycache__'}
    for team_dir in BASE_DIR.iterdir():
        if not team_dir.is_dir() or team_dir.name in skip:
            continue
        for season_dir in team_dir.iterdir():
            if not season_dir.is_dir():
                continue
            for game_dir in season_dir.iterdir():
                if not game_dir.is_dir():
                    continue
                for f in sorted(game_dir.glob("Trace-FullGame-*.mp4")):
                    results.append((game_dir.name, f))
    return results


def find_highlight_files():
    """Find individual player highlight .mp4 files (contain #<number> in filename)."""
    import re as _re
    results = []
    skip = {'tools', 'viewer', 'output', '.git', '__pycache__'}
    for team_dir in BASE_DIR.iterdir():
        if not team_dir.is_dir() or team_dir.name in skip:
            continue
        for season_dir in team_dir.iterdir():
            if not season_dir.is_dir():
                continue
            for game_dir in season_dir.iterdir():
                if not game_dir.is_dir():
                    continue
                for f in sorted(game_dir.glob("*.mp4")):
                    if _re.search(r'#\d+', f.name) and 'fullgame' not in f.name.lower():
                        results.append((game_dir.name, team_dir.name, season_dir.name, f))
    return results


def make_title_highlight(game_folder, team, season, file_path):
    """Generate a YouTube title for a player highlight file."""
    # Strip leading game number from folder name: "6 FV vs Enloe" → "FV vs Enloe"
    game = re.sub(r'^\d+[\.\s]+', '', game_folder).strip()
    return f"Ethan Flowers | {game} | {file_path.stem}"


def list_available():
    print("\n── Player Highlights ───────────────────────────────────")
    hl_files = find_highlight_files()
    if hl_files:
        for game_folder, team, season, f in hl_files:
            uploaded = already_uploaded(f)
            flag = f"  ↗ {uploaded}" if uploaded else ""
            print(f"  {game_folder}/{f.name}  ({f.stat().st_size/1024/1024:.0f} MB){flag}")
    else:
        print("  (none found — looking for *.mp4 files with #19 or similar in game folders)")

    print("\n── Compiled Reels ─────────────────────────────────────")
    reels = sorted(REELS_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    if reels:
        for i, r in enumerate(reels):
            uploaded = already_uploaded(r)
            flag = f"  ↗ {uploaded}" if uploaded else ""
            print(f"  [{i+1}] {r.name}  ({r.stat().st_size/1024/1024:.0f} MB){flag}")
    else:
        print("  (none — run: python tools/auto_build.py)")

    print("\n── Full Game Recordings ────────────────────────────────")
    fg_files = find_full_game_files()
    if fg_files:
        for i, (folder, f) in enumerate(fg_files):
            uploaded = already_uploaded(f)
            flag = f"  ↗ {uploaded}" if uploaded else ""
            print(f"  {folder}/{f.name}  ({f.stat().st_size/1024/1024:.0f} MB){flag}")
    else:
        print("  (none found)")
    print()


def batch_upload(files_with_meta, privacy, skip_uploaded=True):
    """Upload a list of (file_path, title, kind) tuples."""
    uploaded_urls = []
    for file_path, title, kind in files_with_meta:
        existing = already_uploaded(file_path)
        if existing and skip_uploaded:
            print(f"  [Skip] {file_path.name} — already uploaded: {existing}")
            continue
        try:
            url = upload_video(file_path, title, privacy=privacy)
            save_to_log(file_path, url, title, kind, privacy)
            uploaded_urls.append(url)
        except Exception as e:
            print(f"\n  [Error] {file_path.name}: {e}")
    return uploaded_urls


def main():
    parser = argparse.ArgumentParser(description="Upload Ethan's soccer videos to YouTube")
    parser.add_argument("--file", help="Upload a specific MP4 file")
    parser.add_argument("--title", help="Custom title (auto-generated if omitted)")
    parser.add_argument("--reels", action="store_true", help="Upload all compiled highlight reels")
    parser.add_argument("--highlights", action="store_true", help="Upload individual game highlight files (#19, #3, etc.)")
    parser.add_argument("--fullgame", action="store_true", help="Upload all full game recordings")
    parser.add_argument("--all", dest="upload_all", action="store_true",
                        help="Upload highlights, reels, and full game recordings")
    parser.add_argument("--privacy", choices=["unlisted", "private", "public"], default="unlisted",
                        help="YouTube privacy (default: unlisted)")
    parser.add_argument("--list", action="store_true", help="List available files and exit")
    parser.add_argument("--re-upload", action="store_true",
                        help="Re-upload even if file was already uploaded")
    args = parser.parse_args()

    if args.list:
        list_available()
        return

    skip = not args.re_upload
    to_upload = []

    if args.file:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = BASE_DIR / file_path
        if not file_path.exists():
            print(f"Error: File not found: {file_path}")
            sys.exit(1)
        title = args.title or make_title_reel(file_path)
        to_upload.append((file_path, title, "custom"))

    if args.highlights or args.upload_all:
        hl_files = find_highlight_files()
        if not hl_files:
            print("No player highlight files found (looking for *.mp4 with #19 or similar).")
        for game_folder, team, season, f in hl_files:
            title = args.title or make_title_highlight(game_folder, team, season, f)
            to_upload.append((f, title, "highlight"))

    if args.reels or args.upload_all:
        reels = sorted(REELS_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not reels:
            print("No compiled reels found. Run: python tools/auto_build.py")
        for r in reels:
            title = args.title or make_title_reel(r)
            to_upload.append((r, title, "reel"))

    if args.fullgame or args.upload_all:
        fg_files = find_full_game_files()
        if not fg_files:
            print("No full game files found.")
        for folder, f in fg_files:
            title = args.title or make_title_fullgame(folder, f.name)
            to_upload.append((f, title, "fullgame"))

    if not to_upload:
        # Interactive mode
        list_available()
        print("What would you like to upload?")
        print("  [r] Compiled reels")
        print("  [f] Full game recordings")
        print("  [a] All of the above")
        print("  [n] A specific file (enter path)")
        choice = input("\nChoice: ").strip().lower()
        if choice == 'r':
            reels = sorted(REELS_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
            for r in reels:
                to_upload.append((r, make_title_reel(r), "reel"))
        elif choice == 'f':
            for folder, f in find_full_game_files():
                to_upload.append((f, make_title_fullgame(folder, f.name), "fullgame"))
        elif choice == 'a':
            for r in sorted(REELS_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
                to_upload.append((r, make_title_reel(r), "reel"))
            for folder, f in find_full_game_files():
                to_upload.append((f, make_title_fullgame(folder, f.name), "fullgame"))
        elif choice == 'n':
            path = input("File path: ").strip().strip('"')
            fp = Path(path)
            if not fp.is_absolute():
                fp = BASE_DIR / fp
            if fp.exists():
                to_upload.append((fp, make_title_reel(fp), "custom"))
            else:
                print(f"File not found: {fp}")
                sys.exit(1)
        else:
            print("Cancelled.")
            return

    if not to_upload:
        print("Nothing to upload.")
        return

    print(f"\n{'='*55}")
    print(f"  Ready to upload {len(to_upload)} file(s) as {args.privacy}")
    print(f"{'='*55}")

    urls = batch_upload(to_upload, privacy=args.privacy, skip_uploaded=skip)

    print(f"\n{'='*55}")
    print(f"  Uploaded {len(urls)} video(s).")
    if urls:
        print(f"  Log saved to: {LOG_FILE}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
