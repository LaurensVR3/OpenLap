"""The build-verification tooling: self_test.py (the app's --self-test mode)
and scripts/smoke_test_build.py / scripts/check_build_deps.py that drive it
from CI. Releases through 0.3.3 shipped without scipy and requests because
nothing checked the packaged build could import what it needs."""
import importlib.util
import json
from pathlib import Path

import self_test

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check_build_deps = _load_script('check_build_deps')
smoke_test_build = _load_script('smoke_test_build')


# ── self_test.run ──────────────────────────────────────────────────────────────

def _run_self_test(tmp_path, modules):
    report = tmp_path / 'report.json'
    code = self_test.run([str(report)] + modules)
    return code, json.loads(report.read_text(encoding='utf-8'))


def test_self_test_passes_for_importable_modules(tmp_path):
    code, report = _run_self_test(tmp_path, ['json', 'units', 'styles.gauge_dial'])
    assert code == 0 and report['ok'] is True
    assert report['failed_imports'] == {}


def test_self_test_names_the_module_that_failed(tmp_path):
    code, report = _run_self_test(tmp_path, ['json', 'no_such_module_xyz'])
    assert code == 1 and report['ok'] is False
    assert list(report['failed_imports']) == ['no_such_module_xyz']
    assert 'No module named' in report['failed_imports']['no_such_module_xyz']


def test_self_test_fails_when_style_registry_misses_a_plugin(tmp_path):
    # Importable as a module but not a plugin style_registry discovers.
    code, report = _run_self_test(tmp_path, ['styles.__init__'])
    assert code == 1
    assert report['styles']['missing'] == ['styles.__init__']


def test_self_test_without_report_path_is_a_usage_error():
    assert self_test.run([]) == 2


# ── smoke_test_build module list ──────────────────────────────────────────────

def test_module_list_covers_every_source_module_and_style():
    mods = smoke_test_build.first_party_modules()
    assert 'main' not in mods                     # the entry point itself
    assert {'auto_sync', 'webview_api', 'self_test'} <= set(mods)
    styles = {p.stem for p in (ROOT / 'styles').glob('*.py') if not p.stem.startswith('_')}
    assert {m.split('.', 1)[1] for m in mods if m.startswith('styles.')} == styles


def test_dependency_modules_use_import_names_not_distribution_names():
    mods = smoke_test_build.dependency_modules([])
    # opencv-python, Pillow and pywebview import under different names.
    assert {'cv2', 'PIL', 'webview', 'scipy', 'numpy'} <= set(mods)
    assert 'opencv-python' not in mods and 'mpl_toolkits' not in mods


def test_dependency_modules_include_requested_extras():
    assert 'requests' not in smoke_test_build.dependency_modules([])
    assert {'requests', 'playwright'} <= set(
        smoke_test_build.dependency_modules(['racebox-download']))


# ── check_build_deps extras ────────────────────────────────────────────────────

def test_dependency_check_includes_extras(tmp_path):
    p = tmp_path / 'pyproject.toml'
    p.write_text('[project]\nname = "x"\ndependencies = ["numpy"]\n'
                 '[project.optional-dependencies]\n'
                 'dl = ["definitely-not-installed-pkg-xyz"]\n')
    assert check_build_deps.missing_dependencies(p) == []
    missing = check_build_deps.missing_dependencies(p, ('dl',))
    assert len(missing) == 1 and 'definitely-not-installed-pkg-xyz' in missing[0]


def _pyproject(tmp_path, deps):
    p = tmp_path / 'pyproject.toml'
    p.write_text('[project]\nname = "x"\ndependencies = [\n'
                 + ''.join(f'    "{d}",\n' for d in deps) + ']\n')
    return p


def test_dependency_check_reports_uninstalled_package(tmp_path):
    p = _pyproject(tmp_path, ['numpy', 'definitely-not-installed-pkg-xyz'])
    missing = check_build_deps.missing_dependencies(p)
    assert len(missing) == 1 and 'definitely-not-installed-pkg-xyz' in missing[0]


def test_dependency_check_reports_unsatisfied_version(tmp_path):
    assert check_build_deps.missing_dependencies(_pyproject(tmp_path, ['numpy>=999']))


def test_dependency_check_skips_other_platforms(tmp_path):
    p = _pyproject(tmp_path, ["definitely-not-installed-pkg-xyz; sys_platform == 'no-such-os'"])
    assert check_build_deps.missing_dependencies(p) == []


def test_real_pyproject_is_satisfied_in_dev_env():
    # The dev/test environment installs from pyproject.toml, so this also
    # catches the script mis-parsing the real file.
    assert check_build_deps.missing_dependencies(ROOT / 'pyproject.toml') == []
