"""
Tests for _VideoFileHandler — the local HTTP server that serves video files
to the frontend. Covers the security and robustness fixes from the code review:
  - Extension whitelist (path traversal prevention)
  - Known-path allowlist (server only serves paths the app itself resolved)
  - Range header parsing (malformed input → 400, out-of-range → 416,
    suffix form "bytes=-N" → last N bytes)
  - Seek bounds (start >= file_size → 416)
  - Normal full and partial requests
  - No wildcard CORS header
  - Threaded server handles overlapping requests without serializing them
"""
import http.client
import http.server
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Mock webview before importing webview_api (it needs a display to initialise)
if 'webview' not in sys.modules:
    sys.modules['webview'] = MagicMock()

from webview_api import (
    _VideoFileHandler, _ALLOWED_VIDEO_EXTENSIONS, _ThreadingHTTPServer,
    _register_known_video_path,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def server_port():
    """Start a single _VideoFileHandler server (the same threaded class used
    in production) for the whole module."""
    srv = _ThreadingHTTPServer(('127.0.0.1', 0), _VideoFileHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.fixture
def mp4_file(tmp_path):
    """A 16-byte fake .mp4 file: b'0123456789ABCDEF', pre-registered with the
    known-path allowlist so legitimate requests against it are served."""
    p = tmp_path / 'clip.mp4'
    p.write_bytes(b'0123456789ABCDEF')
    _register_known_video_path(str(p))
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
        # Registered but never written to disk — exercises the isfile() check
        # specifically, independent of the known-path allowlist.
        _register_known_video_path(str(p))
        status, _, _ = _req(server_port, _furl(str(p)))
        assert status == 404


# ── Known-path allowlist ────────────────────────────────────────────────────────

class TestKnownPathAllowlist:
    def test_unregistered_path_returns_403(self, server_port, tmp_path):
        """A path with a valid video extension that the app never itself
        resolved (via session scan / manual assign / camera-folder link) must
        be rejected, even though it exists on disk — this is the mitigation
        for arbitrary cross-origin fetch() of any video-looking path."""
        p = tmp_path / 'unregistered.mp4'
        p.write_bytes(b'0123456789ABCDEF')
        status, _, _ = _req(server_port, _furl(str(p)))
        assert status == 403

    def test_registered_path_is_served(self, server_port, tmp_path):
        p = tmp_path / 'registered.mp4'
        p.write_bytes(b'0123456789ABCDEF')
        _register_known_video_path(str(p))
        status, _, body = _req(server_port, _furl(str(p)))
        assert status == 200
        assert body == b'0123456789ABCDEF'


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

    def test_no_wildcard_cors_header(self, server_port, mp4_file):
        """A same-origin <video> tag never needs CORS headers; a wildcard
        Access-Control-Allow-Origin would let an unrelated cross-origin site
        read response bytes via fetch() if it could guess/name the port+path."""
        _, hdrs, _ = _req(server_port, _furl(mp4_file))
        assert hdrs.get('Access-Control-Allow-Origin') is None


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

    def test_suffix_range_last_n_bytes(self, server_port, mp4_file):
        """RFC 7233 suffix form: 'bytes=-500' means the LAST 500 bytes of the
        file, not bytes 0-500. File is 16 bytes 'b0123456789ABCDEF'; the last
        5 bytes are 'BCDEF' (indices 11-15), not the first 6 bytes '012345'."""
        status, hdrs, body = _req(server_port, _furl(mp4_file),
                                   headers={'Range': 'bytes=-5'})
        assert status == 206
        assert body == b'BCDEF'
        assert hdrs.get('Content-Range') == 'bytes 11-15/16'

    def test_suffix_range_longer_than_file_returns_whole_file(self, server_port, mp4_file):
        """Suffix length exceeding the file size should clamp to the whole file."""
        status, _, body = _req(server_port, _furl(mp4_file),
                                headers={'Range': 'bytes=-9999'})
        assert status == 206
        assert body == b'0123456789ABCDEF'


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

    def test_start_after_end_returns_416(self, server_port, mp4_file):
        # start > end is not a valid range regardless of form.
        # (Note: 'bytes=-5' is the RFC 7233 suffix form, NOT "start=-5" — it's
        # handled separately, see TestRangeRequests.test_suffix_range_last_n_bytes.)
        status, _, _ = _req(server_port, _furl(mp4_file),
                            headers={'Range': 'bytes=5-3'})  # start > end
        assert status == 416


# ── Concurrency ────────────────────────────────────────────────────────────────

class TestConcurrentRequests:
    def test_overlapping_range_requests_do_not_serialize(self, server_port, tmp_path):
        """The production server uses _ThreadingHTTPServer (ThreadingMixIn), so
        two overlapping range requests must both complete promptly rather than
        the second blocking until the first's connection is closed. Uses a
        larger file + artificial per-chunk delay (via a slow reader path is not
        available, so we instead just fire many concurrent requests and assert
        they all finish well within a generous window) as a coarse but real
        end-to-end check against the actual running server fixture."""
        big = tmp_path / 'big.mp4'
        big.write_bytes(b'x' * (2 * 1024 * 1024))  # 2 MB
        _register_known_video_path(str(big))

        results = []
        errors = []

        def _worker():
            try:
                status, _, body = _req(server_port, _furl(str(big)),
                                        headers={'Range': 'bytes=0-1048575'})
                results.append((status, len(body)))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(6)]
        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        elapsed = time.time() - start

        assert not errors, f'concurrent requests raised: {errors}'
        assert len(results) == 6
        assert all(status == 206 and n == 1048576 for status, n in results)
        # A single-threaded HTTPServer would serialize these; generous bound
        # to avoid flakiness while still catching gross serialization.
        assert elapsed < 8, f'requests took {elapsed:.2f}s — server may be serializing connections'
