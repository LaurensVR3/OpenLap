"""
exceptions.py — Custom exception hierarchy for OpenLap.

All application-specific exceptions inherit from OpenLapError so callers
can catch domain errors separately from unexpected bugs:

    try:
        session = load_csv(path)
    except OpenLapError as e:
        show_user_message(str(e))
    except Exception:
        logger.exception("Unexpected error")
"""


class OpenLapError(Exception):
    """Base class for all OpenLap domain exceptions."""


# ── Data errors ────────────────────────────────────────────────────────────────

class DataError(OpenLapError):
    """Problems with telemetry data files."""


class CSVParseError(DataError):
    """A CSV file could not be parsed."""


class MissingHeaderError(CSVParseError):
    """The expected data header row was not found in the CSV."""


class NoDataRowsError(CSVParseError):
    """The CSV header was found but contained no data rows."""


class UnsupportedFormatError(DataError):
    """The file format is not recognised as a supported telemetry source."""


# ── Video errors ───────────────────────────────────────────────────────────────

class VideoError(OpenLapError):
    """Problems with video processing."""


class VideoMuxError(VideoError):
    """FFmpeg mux step failed."""


class VideoConcatError(VideoError):
    """FFmpeg concat step failed."""


class LapOutOfRangeError(VideoError):
    """The lap window falls outside the video duration."""


# ── Style / rendering errors ───────────────────────────────────────────────────

class StyleError(OpenLapError):
    """Problems with overlay style plugins."""


class StyleNotFoundError(StyleError):
    """The requested style plugin is not registered."""


# ── Configuration errors ───────────────────────────────────────────────────────

class ConfigError(OpenLapError):
    """Problems with application configuration."""


class ConfigMigrationError(ConfigError):
    """Failed to migrate configuration from an older location or format."""
