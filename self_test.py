"""
self_test.py — Check a build contains everything it needs, then exit.

Run as ``OpenLap.exe --self-test <report.json> [module ...]`` (or
``python main.py --self-test ...`` from source). PyInstaller silently leaves
out any package that wasn't installed at build time, and a missing one only
surfaces when a user reaches the feature that imports it: scipy and requests
were both absent from every release up to 0.3.3, breaking auto-sync and
RaceBox cloud download. scripts/smoke_test_build.py drives this from CI.

The packaged exe has no console, so results go to a JSON report file rather
than stdout; the exit code is 0 only if every check passed.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import traceback


def _check_imports(modules: list) -> dict:
    failed = {}
    for name in modules:
        try:
            importlib.import_module(name)
        except BaseException as e:   # SystemExit/ImportError alike count as broken
            failed[name] = ''.join(traceback.format_exception_only(type(e), e)).strip()
    return failed


def _check_styles(expected: list) -> dict:
    """style_registry discovers plugins by listing the bundled styles/ folder,
    separately from how the import check above reaches them, so check that
    route found every style module too."""
    import style_registry
    style_registry._load_all()
    loaded = sorted(mod.__name__ for mod in style_registry._cache.values())
    missing = sorted(set(expected) - set(loaded))
    return {'loaded': loaded, 'missing': missing}


def _check_ffmpeg() -> dict:
    """In a packaged build FFmpeg must ship inside the app, not merely be on
    this machine's PATH (the build machine has it on PATH; users don't)."""
    from utils import _bundled_dirs, _run
    exe_suffix = '.exe' if sys.platform == 'win32' else ''
    result = {}
    for tool in ('ffmpeg', 'ffprobe'):
        path = next((os.path.join(d, tool + exe_suffix) for d in _bundled_dirs()
                     if os.path.isfile(os.path.join(d, tool + exe_suffix))), None)
        if path is None:
            result[tool] = 'not bundled'
            continue
        try:
            r = _run([path, '-version'], text=True, timeout=30)
            result[tool] = 'ok' if r.returncode == 0 else f'exit code {r.returncode}'
        except Exception as e:
            result[tool] = f'could not run: {e}'
    return result


def run(argv: list) -> int:
    """argv: [report_path, module, ...]. Returns the process exit code."""
    if not argv:
        print('usage: --self-test <report.json> [module ...]', file=sys.stderr)
        return 2
    report_path, modules = argv[0], argv[1:]

    report = {'frozen': bool(getattr(sys, 'frozen', False))}
    report['failed_imports'] = _check_imports(modules)
    ok = not report['failed_imports']

    try:
        report['styles'] = _check_styles([m for m in modules if m.startswith('styles.')])
        ok = ok and not report['styles']['missing']
    except Exception as e:
        report['styles'] = {'error': repr(e)}
        ok = False

    if report['frozen']:
        try:
            report['ffmpeg'] = _check_ffmpeg()
            ok = ok and all(v == 'ok' for v in report['ffmpeg'].values())
        except Exception as e:
            report['ffmpeg'] = {'error': repr(e)}
            ok = False

    report['ok'] = ok
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    return 0 if ok else 1
