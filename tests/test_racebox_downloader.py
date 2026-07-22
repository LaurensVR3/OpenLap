"""
Tests for racebox_downloader.py's path-safety helper.

session.source_id is parsed straight out of a scraped <a href> from
racebox.pro's session list and used to build a local file path — a
compromised/MITM'd response could otherwise smuggle path-traversal
characters into a filesystem write via dest_path()/already_downloaded().
"""
from racebox_downloader import _sanitize_source_id, RaceBoxSource, RemoteSession
from datetime import datetime, timezone


class TestSanitizeSourceId:
    def test_normal_id_is_unchanged(self):
        assert _sanitize_source_id('abc123') == 'abc123'

    def test_hyphens_and_underscores_preserved(self):
        assert _sanitize_source_id('abc-123_def') == 'abc-123_def'

    def test_path_traversal_is_neutralized(self):
        result = _sanitize_source_id('../../etc/passwd')
        assert '/' not in result
        assert '..' not in result or result.count('.') == 0  # dots get replaced too

    def test_absolute_path_prefix_is_neutralized(self):
        result = _sanitize_source_id('/etc/passwd')
        assert '/' not in result

    def test_windows_path_separators_neutralized(self):
        result = _sanitize_source_id('..\\..\\windows\\system32')
        assert '\\' not in result

    def test_excessively_long_id_is_truncated(self):
        result = _sanitize_source_id('a' * 500)
        assert len(result) <= 128


class TestDestPathUsesSanitizedId:
    def test_dest_path_neutralizes_traversal(self, tmp_path):
        source = RaceBoxSource.__new__(RaceBoxSource)  # no __init__ side effects needed
        session = RemoteSession(
            source_id='../../evil', date=datetime.now(timezone.utc),
            track='Test', session_type='Track', laps=1, best_lap=None,
        )
        dest = source.dest_path(session, str(tmp_path))
        # The resulting path must stay inside tmp_path, not escape it.
        resolved = __import__('os').path.abspath(dest)
        assert resolved.startswith(str(tmp_path))
