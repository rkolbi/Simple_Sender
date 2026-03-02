from pathlib import Path
from logging.handlers import RotatingFileHandler

import pytest

from simple_sender import grbl_worker

pytestmark = pytest.mark.unit


def _clear_override_handlers(path: Path) -> None:
    logger = grbl_worker.logging.getLogger("simple_sender.serial")
    target = str(path.resolve())
    for handler in list(logger.handlers):
        if not isinstance(handler, RotatingFileHandler):
            continue
        if str(Path(handler.baseFilename).resolve()) != target:
            continue
        logger.removeHandler(handler)
        handler.close()


def test_get_rx_logger_uses_defaults_for_invalid_numeric_env(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "rx_invalid_env.log"
    monkeypatch.setenv("SIMPLE_SENDER_RX_LOG_PATH", str(log_path))
    monkeypatch.setenv("SIMPLE_SENDER_RX_LOG_MAX_BYTES", "not-an-int")
    monkeypatch.setenv("SIMPLE_SENDER_RX_LOG_BACKUPS", "bad")
    monkeypatch.setattr(grbl_worker, "_RX_LOGGER", None)

    logger = grbl_worker._get_rx_logger()
    assert logger is not None
    handlers = [
        h for h in logger.handlers
        if isinstance(h, RotatingFileHandler)
        and str(Path(h.baseFilename).resolve()) == str(log_path.resolve())
    ]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == 2_097_152
    assert handlers[0].backupCount == 5

    _clear_override_handlers(log_path)
    monkeypatch.setattr(grbl_worker, "_RX_LOGGER", None)


def test_get_rx_logger_handles_typeerror_from_env_values(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "rx_typeerror_env.log"
    monkeypatch.setattr(grbl_worker, "_RX_LOGGER", None)
    original_getenv = grbl_worker.os.getenv

    def _fake_getenv(name: str, default=None):
        if name == "SIMPLE_SENDER_RX_LOG_PATH":
            return str(log_path)
        if name in ("SIMPLE_SENDER_RX_LOG_MAX_BYTES", "SIMPLE_SENDER_RX_LOG_BACKUPS"):
            return None
        return original_getenv(name, default)

    monkeypatch.setattr(grbl_worker.os, "getenv", _fake_getenv)

    logger = grbl_worker._get_rx_logger()
    assert logger is not None
    handlers = [
        h for h in logger.handlers
        if isinstance(h, RotatingFileHandler)
        and str(Path(h.baseFilename).resolve()) == str(log_path.resolve())
    ]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == 2_097_152
    assert handlers[0].backupCount == 5

    _clear_override_handlers(log_path)
    monkeypatch.setattr(grbl_worker, "_RX_LOGGER", None)

