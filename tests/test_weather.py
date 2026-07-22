"""
Tests for weather.py's pure logic (compass conversion, WMO code mapping,
hourly-data formatting). Network calls are mocked — these tests never hit
the real Open-Meteo API.
"""
from unittest.mock import patch
import pytest

from weather import _compass, _format, _WMO, fetch_weather


class TestCompass:
    @pytest.mark.parametrize('deg,expected', [
        (0,   'N'),
        (45,  'NE'),
        (90,  'E'),
        (135, 'SE'),
        (180, 'S'),
        (225, 'SW'),
        (270, 'W'),
        (315, 'NW'),
        (360, 'N'),    # wraps back to N
        (22,  'N'),    # rounds down into N's bucket
        (23,  'NE'),   # rounds up into NE's bucket
    ])
    def test_compass_points(self, deg, expected):
        assert _compass(deg) == expected


class TestWmoMapping:
    def test_known_codes_have_descriptions(self):
        assert _WMO[0]  == 'Clear sky'
        assert _WMO[61] == 'Light rain'
        assert _WMO[95] == 'Thunderstorm'

    def test_format_unknown_code_omits_description(self):
        hourly = {
            'temperature_2m':   [18.0],
            'weathercode':      [9999],   # not in _WMO
            'windspeed_10m':    [10.0],
            'winddirection_10m': [90.0],
        }
        weather_str, wind_str = _format(hourly, hour=0)
        assert weather_str == '18°C'   # no description appended
        assert wind_str == 'E  10 km/h'


class TestFormat:
    def test_full_hourly_data(self):
        hourly = {
            'temperature_2m':    [20.0],
            'weathercode':       [1],
            'windspeed_10m':     [15.0],
            'winddirection_10m': [270.0],
        }
        weather_str, wind_str = _format(hourly, hour=0)
        assert weather_str == '20°C  Mainly clear'
        assert wind_str == 'W  15 km/h'

    def test_hour_out_of_range_returns_empty(self):
        hourly = {'temperature_2m': [20.0], 'weathercode': [1],
                   'windspeed_10m': [15.0], 'winddirection_10m': [270.0]}
        assert _format(hourly, hour=5) == ('', '')

    def test_missing_temperature_returns_empty(self):
        assert _format({}, hour=0) == ('', '')

    def test_wind_speed_without_direction(self):
        hourly = {
            'temperature_2m':    [20.0],
            'weathercode':       [0],
            'windspeed_10m':     [5.0],
            'winddirection_10m': [],
        }
        _, wind_str = _format(hourly, hour=0)
        assert wind_str == '5 km/h'

    def test_malformed_data_does_not_raise(self):
        # Deliberately malformed — _format must fail safe, not propagate.
        assert _format({'temperature_2m': 'not-a-list'}, hour=0) == ('', '')


class TestFetchWeather:
    def test_missing_coordinates_returns_empty_without_network_call(self):
        with patch('weather._fetch_hourly') as mock_fetch:
            result = fetch_weather(0, 0, '2024-06-15T12:00:00Z')
        mock_fetch.assert_not_called()
        assert result == ('', '')

    def test_missing_date_returns_empty(self):
        assert fetch_weather(45.0, 6.0, '') == ('', '')

    def test_invalid_date_returns_empty(self):
        assert fetch_weather(45.0, 6.0, 'not-a-date') == ('', '')

    def test_uses_cache_when_available(self, tmp_path, monkeypatch):
        import weather
        monkeypatch.setattr(weather, 'CACHE_FILE', tmp_path / 'weather_cache.json')
        cached_hourly = {
            'temperature_2m': [22.0] * 24, 'weathercode': [0] * 24,
            'windspeed_10m': [8.0] * 24, 'winddirection_10m': [180.0] * 24,
        }
        with patch('weather._fetch_hourly') as mock_fetch:
            weather._save_cache({'45.0,6.0,2024-06-15': cached_hourly})
            result = fetch_weather(45.0, 6.0, '2024-06-15T12:00:00Z')
        mock_fetch.assert_not_called()
        assert result == ('22°C  Clear sky', 'S  8 km/h')

    def test_network_failure_returns_empty(self, tmp_path, monkeypatch):
        import weather
        monkeypatch.setattr(weather, 'CACHE_FILE', tmp_path / 'weather_cache.json')
        with patch('weather._fetch_hourly', return_value={}):
            result = fetch_weather(45.0, 6.0, '2024-06-15T12:00:00Z')
        assert result == ('', '')
