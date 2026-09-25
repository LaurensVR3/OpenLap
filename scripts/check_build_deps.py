"""
check_build_deps.py — Fail the release build if a runtime dependency is missing.

PyInstaller bundles only what is installed, and anything missing ships as an
exe that breaks only when the user reaches the feature needing it (scipy and
requests were both missing through 0.3.3, back when the release workflow kept
its own package list instead of installing from pyproject.toml). This checks
the build environment against pyproject.toml before building and exits
non-zero on any gap; scripts/smoke_test_build.py checks the result after.

Usage: python scripts/check_build_deps.py [--extra NAME ...] [path/to/pyproject.toml]
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import Requirement


def missing_dependencies(pyproject: Path, extras: tuple = ()) -> list[str]:
    with open(pyproject, 'rb') as f:
        project = tomllib.load(f)['project']
    deps = list(project['dependencies'])
    for extra in extras:
        deps += project.get('optional-dependencies', {})[extra]
    missing = []
    for spec in deps:
        req = Requirement(spec)
        if req.marker and not req.marker.evaluate():
            continue  # not needed on this platform
        try:
            installed = version(req.name)
        except PackageNotFoundError:
            missing.append(f'{spec}  (not installed)')
            continue
        if req.specifier and installed not in req.specifier:
            missing.append(f'{spec}  (installed {installed})')
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description='Check pyproject.toml dependencies are installed.')
    ap.add_argument('--extra', action='append', default=[],
                    help='optional-dependency group to include (repeatable)')
    ap.add_argument('pyproject', nargs='?',
                    default=Path(__file__).resolve().parent.parent / 'pyproject.toml')
    args = ap.parse_args()
    missing = missing_dependencies(Path(args.pyproject), tuple(args.extra))
    if missing:
        print('Runtime dependencies from pyproject.toml are not satisfied:')
        for m in missing:
            print(f'  - {m}')
        return 1
    print('All runtime dependencies from pyproject.toml are installed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
