"""
smoke_test_build.py — Verify a build can import everything OpenLap needs.

Runs the app's --self-test mode (see self_test.py) with a module list derived
from the source tree, so nothing here has to be kept in sync by hand:

  * every top-level .py module in the repo (except main.py, the entry point)
  * every style plugin in styles/
  * the import package of every runtime dependency in pyproject.toml, plus
    any optional-dependency groups named with --extra

Usage:
  python scripts/smoke_test_build.py [--extra NAME ...] -- LAUNCHER...

  LAUNCHER is the command that starts OpenLap, e.g.
    dist/OpenLap/OpenLap.exe          (packaged build)
    python main.py                    (source checkout)

Exits non-zero, listing what's broken, if any check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent


def _norm(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def first_party_modules() -> list:
    mods = [p.stem for p in sorted(ROOT.glob('*.py')) if p.stem != 'main']
    mods += [f'styles.{p.stem}' for p in sorted((ROOT / 'styles').glob('*.py'))
             if not p.stem.startswith('_')]
    return mods


def dependency_modules(extras: list) -> list:
    """Import names for the runtime dependencies (and chosen extras) that
    apply to this platform, e.g. Pillow -> PIL, pywebview -> webview.
    The mapping comes from the installed distributions' own metadata, so it
    must run in the environment the build was made from."""
    with open(ROOT / 'pyproject.toml', 'rb') as f:
        project = tomllib.load(f)['project']
    specs = list(project['dependencies'])
    for extra in extras:
        specs += project.get('optional-dependencies', {})[extra]

    dist_to_mods: dict = {}
    for mod, dists in packages_distributions().items():
        for d in dists:
            dist_to_mods.setdefault(_norm(d), []).append(mod)

    mods = []
    for spec in specs:
        req = Requirement(spec)
        if req.marker and not req.marker.evaluate():
            continue
        candidates = [m for m in dist_to_mods.get(_norm(req.name), [])
                      if not m.startswith('_')]
        if not candidates:
            # Not installed here — list it anyway so the self-test reports
            # it by name instead of this script guessing it's fine.
            candidates = [req.name.replace('-', '_')]
        # A distribution can ship helper top-level packages too (matplotlib
        # also installs mpl_toolkits, pylab); when one is named after the
        # distribution itself, that's the one the app actually imports.
        own = [m for m in candidates if _norm(m) == _norm(req.name)]
        mods += own or candidates
    return sorted(set(mods))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--extra', action='append', default=[],
                    help='optional-dependency group to include (repeatable)')
    ap.add_argument('--timeout', type=float, default=300.0)
    ap.add_argument('launcher', nargs='+', help='command that starts OpenLap')
    args = ap.parse_args()

    modules = first_party_modules() + dependency_modules(args.extra)

    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / 'self_test.json'
        launcher = list(args.launcher)
        # Windows resolves a relative executable against the caller's
        # directory, not cwd=, so make an existing path absolute first.
        if os.path.isfile(launcher[0]):
            launcher[0] = os.path.abspath(launcher[0])
        cmd = launcher + ['--self-test', str(report_path)] + modules
        proc = subprocess.run(cmd, cwd=ROOT, timeout=args.timeout)
        if not report_path.exists():
            print(f'Self-test wrote no report (exit code {proc.returncode}). '
                  f'The app probably failed before reaching the self-test.')
            return 1
        report = json.loads(report_path.read_text(encoding='utf-8'))

    print(f'Checked {len(modules)} modules ({"packaged build" if report["frozen"] else "source"}).')
    for mod, err in report.get('failed_imports', {}).items():
        print(f'  import {mod}: {err}')
    styles = report.get('styles', {})
    if styles.get('error'):
        print(f'  style discovery failed: {styles["error"]}')
    for mod in styles.get('missing', []):
        print(f'  style not discovered by style_registry: {mod}')
    for tool, status in report.get('ffmpeg', {}).items():
        if status != 'ok':
            print(f'  {tool}: {status}')

    if report.get('ok') and proc.returncode == 0:
        print('Self-test passed.')
        return 0
    print('Self-test FAILED.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
