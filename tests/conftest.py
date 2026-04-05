import sys
import os
from pathlib import Path

import pytest

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def racebox_car_csv_path():
    return str(FIXTURES_DIR / "racebox_car.csv")


@pytest.fixture
def racebox_bike_csv_path():
    return str(FIXTURES_DIR / "racebox_bike.csv")


@pytest.fixture
def aim_csv_path():
    return str(FIXTURES_DIR / "aim_session.csv")


@pytest.fixture
def not_telemetry_csv_path():
    return str(FIXTURES_DIR / "not_a_telemetry_file.csv")


@pytest.fixture
def racebox_car_session(racebox_car_csv_path):
    from racebox_data import load_csv
    return load_csv(racebox_car_csv_path)


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect config to a temp dir so tests don't touch ~/.openlap/config.json."""
    import app_config
    fake_config = tmp_path / "config.json"
    nonexistent = tmp_path / "nonexistent.json"
    monkeypatch.setattr(app_config, "CONFIG_FILE", fake_config)
    monkeypatch.setattr(app_config, "_OLD_CONFIG_V2", nonexistent)
    monkeypatch.setattr(app_config, "_OLD_CONFIG_V1", nonexistent)
    return tmp_path
