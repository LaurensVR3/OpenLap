"""
FFmpeg location and failure reporting.

Issue #20 was one broken FFmpeg on the reporter's machine producing three
unrelated-looking failures, none of which said so. The app made that
undiagnosable in three ways, each pinned here:

  * FFmpeg was located one way for the Settings check (an env override plus a
    copy shipped beside the app) and another way everywhere real work happens
    (a bare command name resolved through PATH), so the two could disagree.
  * A missing FFmpeg surfaced as a raw OS "cannot find the file" error.
  * A broken FFmpeg was reported as version "unknown" with every encoder
    unavailable, which reads as "no encoders on this machine".
"""
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

if 'webview' not in sys.modules:
    sys.modules['webview'] = MagicMock()

import utils
from exceptions import FFmpegNotFoundError


# ── Locating the binaries ─────────────────────────────────────────────────────

class TestToolPath:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        fake = tmp_path / 'ffmpeg.exe'
        fake.write_text('')
        monkeypatch.setenv('FFMPEG_BIN', str(fake))
        assert utils.tool_path('ffmpeg') == str(fake)

    def test_env_override_ignored_when_it_is_not_a_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv('FFMPEG_BIN', str(tmp_path / 'does_not_exist'))
        monkeypatch.setattr(utils.shutil, 'which', lambda *a, **k: '/usr/bin/ffmpeg')
        assert utils.tool_path('ffmpeg') == '/usr/bin/ffmpeg'

    def test_falls_back_to_a_binary_shipped_beside_the_app(self, tmp_path, monkeypatch):
        """The documented packaged layout puts ffmpeg next to the app. The
        Settings check already looked there; the export path did not."""
        monkeypatch.delenv('FFMPEG_BIN', raising=False)
        monkeypatch.setattr(utils.shutil, 'which', lambda *a, **k: None)
        exe = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
        bundled = tmp_path / exe
        bundled.write_text('')
        monkeypatch.setattr(utils, '_bundled_dirs', lambda: [str(tmp_path)])
        assert utils.tool_path('ffmpeg') == str(bundled)

    def test_bare_name_as_last_resort(self, monkeypatch):
        """Still returns something launchable so the caller gets a clean
        FFmpegNotFoundError rather than silently doing nothing."""
        monkeypatch.delenv('FFMPEG_BIN', raising=False)
        monkeypatch.setattr(utils.shutil, 'which', lambda *a, **k: None)
        monkeypatch.setattr(utils, '_bundled_dirs', lambda: [])
        assert utils.tool_path('ffmpeg') == 'ffmpeg'

    def test_ffprobe_uses_its_own_override(self, tmp_path, monkeypatch):
        fake = tmp_path / 'ffprobe.exe'
        fake.write_text('')
        monkeypatch.setenv('FFPROBE_BIN', str(fake))
        assert utils.ffprobe_path() == str(fake)

    def test_every_call_site_goes_through_the_resolver(self):
        """The whole point: no module may hardcode a bare binary name again,
        or the Settings check and the exporter can disagree about which
        FFmpeg they mean."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for name in ('video_renderer.py', 'auto_sync.py', 'session_scanner.py',
                     'webview_api.py'):
            for i, line in enumerate((root / name).read_text(encoding='utf-8').splitlines(), 1):
                if line.lstrip().startswith('#'):
                    continue
                for lit in ("'ffmpeg'", '"ffmpeg"', "'ffprobe'", '"ffprobe"'):
                    # tool_path('ffmpeg') is the resolver itself, not a call site
                    if lit in line and 'tool_path' not in line:
                        offenders.append(f'{name}:{i}: {line.strip()}')
        assert not offenders, 'hardcoded FFmpeg binary names:\n' + '\n'.join(offenders)


# ── Reporting a missing binary ────────────────────────────────────────────────

class TestMissingBinaryIsNamedClearly:
    MISSING = ['definitely-not-a-real-binary-xyz', '-version']

    def test_run_raises_ffmpeg_not_found(self):
        with pytest.raises(FFmpegNotFoundError) as exc:
            utils._run(self.MISSING)
        assert 'PATH' in str(exc.value)

    def test_popen_raises_ffmpeg_not_found(self):
        with pytest.raises(FFmpegNotFoundError) as exc:
            utils._popen(self.MISSING)
        assert 'PATH' in str(exc.value)

    def test_message_mentions_the_env_override(self):
        with pytest.raises(FFmpegNotFoundError) as exc:
            utils._run(self.MISSING)
        assert 'FFMPEG_BIN' in str(exc.value)

    def test_it_is_a_domain_error_not_a_bare_oserror(self):
        """export_runner prints str(e) straight to the log; an OSError there
        reads like a problem with the user's own files."""
        from exceptions import OpenLapError
        assert issubclass(FFmpegNotFoundError, OpenLapError)


# ── check_encoders honesty ────────────────────────────────────────────────────

def _fake_run(handler):
    """Build a _run stand-in dispatching on the ffmpeg arguments."""
    def run(cmd, **kwargs):
        rc, out, err = handler(cmd[1:])
        return subprocess.CompletedProcess(cmd, rc, out, err)
    return run


@pytest.fixture
def api(tmp_config_dir):
    from webview_api import WebviewAPI
    return WebviewAPI()


class TestCheckEncodersReportsRealFailures:
    def test_broken_ffmpeg_is_an_error_not_version_unknown(self, api):
        """The exact shape of the issue #20 screenshot: a cheerful
        'FFmpeg unknown.' with every encoder greyed out."""
        with patch('utils._run', _fake_run(lambda a: (1, '', ''))):
            result = api.check_encoders()
        assert result.get('version') != 'unknown'
        assert 'error' in result
        assert 'exited with code 1' in result['error']

    def test_a_binary_that_is_not_ffmpeg_is_called_out(self, api):
        with patch('utils._run', _fake_run(lambda a: (0, 'not ffmpeg at all\n', ''))):
            result = api.check_encoders()
        assert 'error' in result
        assert 'not FFmpeg' in result['error']

    def test_missing_ffmpeg_surfaces_the_install_hint(self, api):
        def boom(cmd, **kwargs):
            raise FFmpegNotFoundError('Could not run ffmpeg: nope. Install FFmpeg ... PATH')
        with patch('utils._run', boom):
            result = api.check_encoders()
        assert 'PATH' in result['error']

    def test_working_ffmpeg_reports_a_version_and_its_path(self, api):
        def handler(args):
            if '-version' in args:
                return 0, 'ffmpeg version 7.1 Copyright (c)\n', ''
            if '-encoders' in args:
                return 0, ' V....D libx264   H.264\n', ''
            return 0, '', ''
        with patch('utils._run', _fake_run(handler)):
            result = api.check_encoders()
        assert result['version'] == '7.1'
        assert result['ffmpeg_path']


class TestEncoderProbeDegradesGracefully:
    """A build without the lavfi input cannot be probed functionally. That
    used to mark every encoder unavailable, software ones included, which is
    what made the issue #20 screenshot look like a hardware problem."""

    @staticmethod
    def _handler_without_lavfi(args):
        if '-version' in args:
            return 0, 'ffmpeg version 6.1 Copyright (c)\n', ''
        if '-encoders' in args:
            return 0, (' V....D libx264               H.264 software\n'
                       ' V....D libx265               H.265 software\n'), ''
        if 'lavfi' in args:
            return 1, '', 'Unknown input format: lavfi\n'
        return 0, '', ''

    def test_encoders_in_the_build_are_still_reported_available(self, api):
        with patch('utils._run', _fake_run(self._handler_without_lavfi)):
            result = api.check_encoders()
        by_name = {e['name']: e for e in result['encoders']}
        assert by_name['libx264']['available'] is True
        assert 'not verified' in by_name['libx264']['detail']

    def test_encoders_absent_from_the_build_are_still_unavailable(self, api):
        with patch('utils._run', _fake_run(self._handler_without_lavfi)):
            result = api.check_encoders()
        by_name = {e['name']: e for e in result['encoders']}
        assert by_name['h264_nvenc']['available'] is False
        assert 'not in this FFmpeg build' in by_name['h264_nvenc']['detail']

    def test_a_listed_encoder_that_cannot_actually_encode_is_unavailable(self, api):
        """nvenc ships in most builds and still fails with no NVIDIA card, so
        the functional probe has to stay when it is usable."""
        def handler(args):
            if '-version' in args:
                return 0, 'ffmpeg version 7.1 Copyright (c)\n', ''
            if '-encoders' in args:
                return 0, ' V....D libx264  H.264\n V....D h264_nvenc  NVENC\n', ''
            if 'h264_nvenc' in args:
                return 1, '', 'Cannot load nvcuda.dll\n'
            return 0, '', ''
        with patch('utils._run', _fake_run(handler)):
            result = api.check_encoders()
        by_name = {e['name']: e for e in result['encoders']}
        assert by_name['libx264']['available'] is True
        assert by_name['h264_nvenc']['available'] is False
        assert 'failed to encode' in by_name['h264_nvenc']['detail']


class TestCheckEncodersUsesTheNoConsoleWrapper:
    def test_it_does_not_call_subprocess_run_directly(self):
        """Bare subprocess.run flashes console windows on Windows, which the
        rest of the codebase avoids via utils._run."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent / 'webview_api.py').read_text(encoding='utf-8')
        body = src[src.index('def check_encoders'):src.index('# ── About')]
        assert 'subprocess.run' not in body
