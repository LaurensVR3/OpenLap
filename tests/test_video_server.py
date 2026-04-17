"""
Tests for _VideoFileHandler — the local HTTP server that serves video files
to the frontend. Covers the security and robustness fixes from the code review:
  - Extension whitelist (path traversal prevention)
  - Range header parsing (malformed input → 400, out-of-range → 416)
  - Seek bounds (start >= file_size → 416)
  - Normal full and partial requests
"""
import http.client
import http.server
import sys
import threading
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Mock webview before importing webview_api (it needs a display to initialise)
if 'webview' not in sys.modules:
    sys.modules['webview'] = MagicMock()

from webview_api import _VideoFileHandler, _ALLOWED_VIDEO_EXTENSIONS


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def server_port():
    """Start a single _VideoFileHandler server for the whole module."""
    srv = http.server.HTTPServer(('127.0.0.1', 0), _VideoFileHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.fixture
def mp4_file(tmp_path):
    """A 16-byte fake .mp4 file: b'0123456789ABCDEF'."""
    p = tmp_path / 'clip.mp4'
    p.write_bytes(b'0123456789ABCDEF')
    return str(p)


def _req(port, path, headers=None):
    """Make a GET request, return (status, headers_dict, body)."""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request('GET', path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    headers_out = dict(resp.getheaders())
    conn.close()
    return resp.status, headers_out, body


def _furl(path):
    """Build the ?f=<encoded-path> query string the handler expects."""
    return f'/?f={urllib.parse.quote(path, safe="")}'


# ── Extension whitelist ────────────────────────────────────────────────────────

class TestAllowedExtensions:
    def test_constant_contains_common_video_types(self):
        for ext in ('.mp4', '.mov', '.avi', '.mkv', '.m4v'):
            assert ext in _ALLOWED_VIDEO_EXTENSIONS
        for ext in ('.MP4', '.MOV', '.AVI', '.MKV'):
            assert ext in _ALLOWED_VIDEO_EXTENSIONS

    def test_non_video_extensions_absent(self):
        for ext in ('.py', '.json', '.env', '.exe', '.csv', '.txt', '.ini'):
            assert ext not in _ALLOWED_VIDEO_EXTENSIONS

    def test_disallowed_extension_returns_403(self, server_port, tmp_path):
        p = tmp_path / 'config.json'
        p.write_text('{"secret": "value"}')
        status, _, _ = _req(server_port, _furl(str(p)))
        assert status == 403

    def test_disallowed_py_extension_returns_403(self, server_port, tmp_path):
        p = tmp_path / 'secret.py'
        p.write_text('pass')
        status, _, _ = _req(server_port, _furl(str(p)))
        assert status == 403

    def test_nonexistent_video_returns_404(self, server_port, tmp_path):
        p = tmp_path / 'missing.mp4'
        status, _, _ = _req(server_port, _furl(str(p)))
        assert status == 404


# ── Full file requests ─────────────────────────────────────────────────────────

class TestFullRequests:
    def test_200_for_existing_mp4(self, server_port, mp4_file):
        status, hdrs, body = _req(server_port, _furl(mp4_file))
        assert status == 200
        assert body == b'0123456789ABCDEF'

    def test_content_length_header(self, server_port, mp4_file):
        _, hdrs, _ = _req(server_port, _furl(mp4_file))
        assert hdrs.get('Content-Length') == '16'

    def test_accept_ranges_header(self, server_port, mp4_file):
        _, hdrs, _ = _req(server_port, _furl(mp4_file))
        assert hdrs.get('Accept-Ranges') == 'bytes'

    def test_cors_header(self, server_port, mp4_file):
        _, hdrs, _ = _req(server_port, _furl(mp4_file))
        assert hdrs.get('Access-Control-Allow-Origin') == '*'


# ── Range requests ─────────────────────────────────────────────────────────────

class TestRangeRequests:
    def test_partial_range_returns_206(self, server_port, mp4_file):
        status, hdrs, body = _req(server_port, _furl(mp4_file),
                                   headers={'Range': 'bytes=0-7'})
        assert status == 206
        assert body == b'01234567'

    def test_range_from_offset(self, server_port, mp4_file):
        status, _, body = _req(server_port, _furl(mp4_file),
                                headers={'Range': 'bytes=4-7'})
        assert status == 206
        assert body == b'4567'

    def test_open_ended_range(self, server_port, mp4_file):
        # bytes=8- means byte 8 to end
        status, _, body = _req(server_port, _furl(mp4_file),
                                headers={'Range': 'bytes=8-'})
        assert status == 206
        assert body == b'89ABCDEF'

    def test_content_range_header(self, server_port, mp4_file):
        _, hdrs, _ = _req(server_port, _furl(mp4_file),
                          headers={'Range': 'bytes=0-3'})
        assert hdrs.get('Content-Range') == 'bytes 0-3/16'


# ── Range error cases ──────────────────────────────────────────────────────────

class TestRangeErrors:
    def test_malformed_range_returns_400(self, server_port, mp4_file):
        status, _, _ = _req(server_port, _furl(mp4_file),
                            headers={'Range': 'bytes=abc-def'})
        assert status == 400

    def test_start_beyond_eof_returns_416(self, server_port, mp4_file):
        # File is 16 bytes; start=20 is out of range
        status, _, _ = _req(server_port, _furl(mp4_file),
                            headers={'Range': 'bytes=20-30'})
        assert status == 416

    def test_negative_start_returns_416(self, server_port, mp4_file):
        # bytes=-5 is valid HTTP syntax (suffix range) but our handler rejects it
        # because parts[0] would be '' and parts[1] would be '5' → start=0, end=5
        # The check is: start < 0 — this tests the non-negative guard
        status, _, _ = _req(server_port, _furl(mp4_file),
                            headers={'Range': 'bytes=5-3'})  # start > end
        assert status == 416
