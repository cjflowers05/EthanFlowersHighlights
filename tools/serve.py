"""
serve.py - Local web server for the soccer highlights viewer.
Handles range requests (needed for video seeking) and JSON API endpoints.

Usage:
  python tools/serve.py           # Starts at http://localhost:8080
  python tools/serve.py --port 9000
  python tools/serve.py --no-browser
"""
import argparse
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"
VIEWER_DIR = BASE_DIR / "viewer"


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


class HighlightsHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default logging (too noisy for video range requests)
        if '/api/' in (args[0] if args else ''):
            print(f"  API: {args[0]}")

    def send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg, status=400):
        self.send_json({"error": msg}, status)

    def serve_file_with_range(self, file_path):
        """Serve a file with HTTP range request support (required for video seeking)."""
        try:
            file_size = file_path.stat().st_size
        except FileNotFoundError:
            self.send_error(404, "File not found")
            return

        # Determine MIME type
        suffix = file_path.suffix.lower()
        mime = {
            '.mp4': 'video/mp4', '.webm': 'video/webm',
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
            '.json': 'application/json', '.ico': 'image/x-icon'
        }.get(suffix, 'application/octet-stream')

        range_header = self.headers.get('Range')
        if range_header:
            m = re.match(r'bytes=(\d*)-(\d*)', range_header)
            if m:
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                self.send_header('Content-Length', str(length))
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()

                try:
                    with open(file_path, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                    pass
                return

        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(file_size))
        self.send_header('Accept-Ranges', 'bytes')
        if mime in ('text/html', 'application/javascript', 'text/css'):
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()

        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # API endpoints
        if path == '/api/manifest':
            if MANIFEST_FILE.exists():
                with open(MANIFEST_FILE) as f:
                    data = json.load(f)
                self.send_json(data)
            else:
                self.send_error_json("No manifest found. Run: python tools/scan.py", 404)
            return

        if path == '/api/status':
            self.send_json({"status": "ok", "base_dir": str(BASE_DIR)})
            return

        # Static viewer files
        if path == '/' or path == '/index.html':
            file_path = VIEWER_DIR / 'index.html'
            self.serve_file_with_range(file_path)
            return

        if path.startswith('/viewer/'):
            rel = path[len('/viewer/'):]
            file_path = VIEWER_DIR / rel
            if file_path.exists() and file_path.is_file():
                self.serve_file_with_range(file_path)
                return

        # Media and output files — served relative to BASE_DIR
        # Strip leading slash
        rel_path = path.lstrip('/')
        file_path = BASE_DIR / rel_path.replace('/', os.sep)

        if file_path.exists() and file_path.is_file():
            self.serve_file_with_range(file_path)
            return

        self.send_error(404, f"Not found: {path}")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == '/api/update-selection':
            # body: {moment_id: str, selected: bool, note: str}
            if not MANIFEST_FILE.exists():
                self.send_error_json("No manifest", 404)
                return
            with open(MANIFEST_FILE) as f:
                manifest = json.load(f)

            moment_id = str(body.get('moment_id', ''))
            updated = False
            for season in manifest['seasons']:
                for game in season['games']:
                    for clip in game['clips']:
                        if clip['moment_id'] == moment_id:
                            if 'selected' in body:
                                clip['selected'] = bool(body['selected'])
                            if 'note' in body:
                                clip['note'] = body['note']
                            updated = True

            if updated:
                with open(MANIFEST_FILE, 'w') as f:
                    json.dump(manifest, f, indent=2)
                self.send_json({"ok": True})
            else:
                self.send_error_json(f"Clip not found: {moment_id}", 404)
            return

        if path == '/api/build-reel':
            # body: {season: str, game_number: int, clip_ids: [str], output_name: str}
            if not FFMPEG:
                self.send_error_json("FFmpeg not found on this machine", 500)
                return
            if not MANIFEST_FILE.exists():
                self.send_error_json("No manifest", 404)
                return

            with open(MANIFEST_FILE) as f:
                manifest = json.load(f)

            clip_ids = set(str(i) for i in body.get('clip_ids', []))
            season_filter = body.get('season', '')
            game_filter = body.get('game_number')
            output_name = re.sub(r'[^\w]', '_', body.get('output_name', 'custom_reel')) + '.mp4'

            clip_files = []
            for season in manifest['seasons']:
                if season_filter and season_filter.lower() not in season['name'].lower():
                    continue
                for game in season['games']:
                    if game_filter and str(game.get('number')) != str(game_filter):
                        continue
                    for clip in game['clips']:
                        if not clip_ids or clip['moment_id'] in clip_ids:
                            if clip.get('selected', True) or clip_ids:
                                fp = BASE_DIR / clip['file'].replace('/', os.sep)
                                if fp.exists():
                                    clip_files.append(fp)

            if not clip_files:
                self.send_error_json("No matching clips found", 400)
                return

            output_path = OUTPUT_DIR / "reels" / output_name
            (OUTPUT_DIR / "reels").mkdir(exist_ok=True)

            def run_build():
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
                    for clip in clip_files:
                        p = str(clip).replace('\\', '/').replace("'", "\\'")
                        tmp.write(f"file '{p}'\n")
                    concat_file = tmp.name
                try:
                    subprocess.run(
                        [FFMPEG, '-f', 'concat', '-safe', '0', '-i', concat_file,
                         '-c', 'copy', '-movflags', '+faststart', str(output_path), '-y'],
                        capture_output=True, timeout=600
                    )
                finally:
                    os.unlink(concat_file)

            thread = threading.Thread(target=run_build, daemon=True)
            thread.start()

            rel_output = str(output_path.relative_to(BASE_DIR)).replace('\\', '/')
            self.send_json({
                "ok": True,
                "message": f"Building reel from {len(clip_files)} clips...",
                "output": rel_output,
                "clip_count": len(clip_files)
            })
            return

        if path == '/api/save-marks':
            # body: {season_name, game_number, marks: [{start, end, label}]}
            if not MANIFEST_FILE.exists():
                self.send_error_json("No manifest", 404)
                return
            with open(MANIFEST_FILE) as f:
                manifest = json.load(f)

            season_name = body.get('season_name', '')
            game_number = str(body.get('game_number', ''))
            marks = body.get('marks', [])

            updated = False
            for season in manifest['seasons']:
                if season_name and season['name'] != season_name:
                    continue
                for game in season['games']:
                    if str(game.get('number', '')) == game_number:
                        game['full_game_marks'] = marks
                        updated = True

            if updated:
                with open(MANIFEST_FILE, 'w') as f:
                    json.dump(manifest, f, indent=2)
                self.send_json({"ok": True, "saved": len(marks)})
            else:
                self.send_error_json("Game not found", 404)
            return

        if path == '/api/build-reel-from-marks':
            # body: {video_file: str, marks: [{start, end, label}], output_name: str}
            if not FFMPEG:
                self.send_error_json("FFmpeg not found", 500)
                return

            video_rel = body.get('video_file', '')
            marks = body.get('marks', [])
            output_name = re.sub(r'[^\w]', '_', body.get('output_name', 'marked_reel')) + '.mp4'

            if not video_rel or not marks:
                self.send_error_json("Missing video_file or marks", 400)
                return

            video_path = BASE_DIR / video_rel.replace('/', os.sep)
            if not video_path.exists():
                self.send_error_json(f"Video not found: {video_rel}", 404)
                return

            output_path = OUTPUT_DIR / "reels" / output_name
            (OUTPUT_DIR / "reels").mkdir(exist_ok=True)

            def extract_and_concat():
                import tempfile
                tmp_dir = Path(tempfile.mkdtemp())
                segment_files = []
                try:
                    for i, mark in enumerate(marks):
                        start = float(mark['start'])
                        end = float(mark['end'])
                        if end <= start:
                            continue
                        seg_path = tmp_dir / f"seg_{i:03d}.mp4"
                        subprocess.run([
                            FFMPEG,
                            '-ss', str(start),
                            '-to', str(end),
                            '-i', str(video_path),
                            '-c:v', 'libx264', '-crf', '22', '-preset', 'fast',
                            '-c:a', 'aac', '-b:a', '128k',
                            '-avoid_negative_ts', 'make_zero',
                            '-movflags', '+faststart',
                            str(seg_path), '-y'
                        ], capture_output=True, timeout=300)
                        if seg_path.exists():
                            segment_files.append(seg_path)

                    if not segment_files:
                        return

                    # Concatenate segments
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                                     encoding='utf-8', dir=str(tmp_dir)) as tmp:
                        for seg in segment_files:
                            p = str(seg).replace('\\', '/').replace("'", "\\'")
                            tmp.write(f"file '{p}'\n")
                        concat_file = tmp.name

                    subprocess.run([
                        FFMPEG, '-f', 'concat', '-safe', '0', '-i', concat_file,
                        '-c', 'copy', '-movflags', '+faststart',
                        str(output_path), '-y'
                    ], capture_output=True, timeout=600)

                finally:
                    import shutil as _shutil
                    _shutil.rmtree(tmp_dir, ignore_errors=True)

            thread = threading.Thread(target=extract_and_concat, daemon=True)
            thread.start()

            rel_output = str(output_path.relative_to(BASE_DIR)).replace('\\', '/')
            self.send_json({
                "ok": True,
                "message": f"Extracting {len(marks)} mark(s) from full game...",
                "output": rel_output,
                "mark_count": len(marks)
            })
            return

        self.send_error(404, f"Unknown API: {path}")


def main():
    parser = argparse.ArgumentParser(description="Soccer highlights web server")
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()

    # Auto-scan if no manifest exists
    if not MANIFEST_FILE.exists():
        print("No manifest found. Running scan first...")
        scan_script = Path(__file__).parent / 'scan.py'
        subprocess.run(['python', str(scan_script)])

    server = http.server.ThreadingHTTPServer(('localhost', args.port), HighlightsHandler)
    url = f"http://localhost:{args.port}"
    print(f"\n{'='*50}")
    print(f"  Ethan's Soccer Highlights")
    print(f"  {url}")
    print(f"{'='*50}")
    print(f"  Ctrl+C to stop\n")

    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.5), webbrowser.open(url)), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == '__main__':
    main()
