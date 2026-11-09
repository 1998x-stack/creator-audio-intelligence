#!/usr/bin/env python3
"""Local fixture site: a stand-in creator homepage + video pages.

Purpose: exercise the *real* browser-harness discovery and player code paths
against a real Chrome (real DOM, real <video>, real playback, real autoplay
gesture gate) without touching an external site or anyone else's content.

It is deliberately a different platform ("fixture") with its own host so a
fixture result can never be mistaken for a Douyin result.

Routes
    /user/<name>              creator homepage listing visible video links
    /video/<id>               video page with a real <video> element
    /media/<file>             the mp4 itself (HTTP Range supported)
    /user/empty               homepage with zero works   -> EMPTY_PROFILE
    /user/loginwall           login wall, no works       -> LOGIN_REQUIRED
    /user/broken              no anchors, no video       -> DOM_CHANGED
    /video/<id>?noplayer=1    page without a <video>     -> "no video element"
    /healthz                  readiness probe

Usage:
    scripts/serve_fixture.py --port 8099 --fixtures data/fixtures [--quiet]
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

# 19-digit ids keep the same shape as a real Douyin video id.
BASE_ID = 9_000_000_000_000_000_000
CREATOR = 'local-e2e-creator'

_CHROME = """<nav><a href="/">首页</a><a href="/user/{name}">主页</a><a href="/about">关于</a></nav>"""

_HOMEPAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{name} 的主页</title></head><body>
<h1>{name}</h1>
{chrome}
<nav>作品 {count}</nav>
<ul class="works">
{items}
</ul>
</body></html>"""

_ITEM = '<li><a href="/video/{vid}" title="{title}"><img alt="{title}" src="/media/{media}"></a></li>'

_VIDEO_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{title}</title></head><body>
<h1>{title}</h1>
<video id="player" src="/media/{media}" preload="metadata" controls playsinline></video>
<div class="meta">作者 {creator}</div>
</body></html>"""

_NO_PLAYER_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{title}</title></head><body><h1>{title}</h1>
<p>该作品暂不支持播放。</p></body></html>"""

_LOGIN_WALL = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>登录</title></head><body>
<nav><a href="/">首页</a><a href="/login">登录</a><a href="/about">关于</a></nav>
<h1>请先登录</h1>
<p>扫码登录后查看该用户的作品，登录后可继续浏览。</p></body></html>"""

_EMPTY = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>空闲主页</title></head><body>
<nav><a href="/">首页</a><a href="/user/empty">主页</a><a href="/about">关于</a></nav>
<h1>该用户还没有发布作品</h1>
<p>暂时没有内容可以显示，请稍后再来看看。</p></body></html>"""

_BROKEN = "<html><body></body></html>"


class Fixture:
    def __init__(self, fixtures_dir: Path):
        manifest_path = fixtures_dir / 'manifest.json'
        if not manifest_path.is_file():
            raise SystemExit(f'missing {manifest_path}; run scripts/make_fixture.sh first')
        self.entries = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not self.entries:
            raise SystemExit('fixture manifest is empty')
        self.dir = fixtures_dir
        self.videos = {}
        for index, entry in enumerate(self.entries):
            self.videos[str(BASE_ID + index)] = entry

    def title(self, vid: str) -> str:
        return f"fixture work {self.videos[vid]['name']}"

    def homepage(self, name: str) -> str:
        if name == 'empty':
            return _EMPTY
        if name == 'loginwall':
            return _LOGIN_WALL
        if name == 'broken':
            return _BROKEN
        items = '\n'.join(_ITEM.format(vid=vid, title=self.title(vid), media=self.videos[vid]['media'])
                          for vid in self.videos)
        return _HOMEPAGE.format(name=CREATOR, count=len(self.videos), items=items,
                                chrome=_CHROME.format(name=CREATOR))

    def video_page(self, vid: str, noplayer: bool) -> str:
        entry = self.videos[vid]
        if noplayer:
            return _NO_PLAYER_PAGE.format(title=self.title(vid))
        return _VIDEO_PAGE.format(title=self.title(vid), media=entry['media'], creator=CREATOR)


_RANGE = re.compile(r'bytes=(\d*)-(\d*)')


class Handler(BaseHTTPRequestHandler):
    server_version = 'cai-fixture/1'
    fixture: Fixture

    def log_message(self, fmt, *args):  # noqa: A003 - quieten default logging
        if not getattr(self.server, 'quiet', True):
            sys.stderr.write('fixture %s - %s\n' % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = 'text/html; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _serve_media(self, filename: str):
        path = (self.fixture.dir / unquote(filename)).resolve()
        if self.fixture.dir.resolve() not in path.parents or not path.is_file():
            self._send(404, b'not found')
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        code = 200
        match = _RANGE.match(self.headers.get('Range') or '')
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        remaining = length
        with path.open('rb') as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        if path == '/healthz':
            self._send(200, b'ok', 'text/plain; charset=utf-8')
            return
        if path.startswith('/media/'):
            self._serve_media(path[len('/media/'):])
            return
        if path.startswith('/user/'):
            self._send(200, self.fixture.homepage(unquote(path[len('/user/'):])).encode('utf-8'))
            return
        if path.startswith('/video/'):
            vid = unquote(path[len('/video/'):]).strip('/')
            if vid not in self.fixture.videos:
                self._send(404, b'unknown video id')
                return
            html = self.fixture.video_page(vid, 'noplayer' in query)
            self._send(200, html.encode('utf-8'))
            return
        self._send(404, b'not found')


def serve(port: int, fixtures_dir: Path, quiet: bool = True):
    fixture = Fixture(fixtures_dir)
    handler = type('BoundHandler', (Handler,), {'fixture': fixture})
    httpd = ThreadingHTTPServer(('127.0.0.1', port), handler)
    httpd.quiet = quiet
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8099)
    ap.add_argument('--fixtures', type=Path, default=Path('data/fixtures'))
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)
    httpd = serve(args.port, args.fixtures, args.quiet)
    base = f'http://127.0.0.1:{args.port}'
    print(f'fixture site on {base}')
    print(f'  homepage  {base}/user/{CREATOR}')
    print(f'  negatives {base}/user/empty  {base}/user/loginwall  {base}/user/broken')
    for vid in httpd.RequestHandlerClass.fixture.videos:
        print(f'  video     {base}/video/{vid}')
    print('press Ctrl-C to stop')
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print('\nstopping')
    finally:
        httpd.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
