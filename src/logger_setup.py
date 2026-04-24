from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class LoggerSet:
    app: logging.Logger
    console: logging.Logger
    network: logging.Logger
    errors: logging.Logger


def _reset_logger(name: str, level: int) -> logging.Logger:
    # Очистка handlers защищает от дублирования записей при повторном запуске в одном процессе.
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False
    return logger


def _build_file_handler(file_path: Path, formatter: logging.Formatter) -> logging.Handler:
    handler = logging.FileHandler(file_path, encoding="utf-8")
    handler.setFormatter(formatter)
    return handler


def _build_optional_logger(
    name: str,
    file_path: Path,
    level: int,
    enabled: bool,
    formatter: logging.Formatter,
) -> logging.Logger:
    # Отдельные логгеры можно отключать через env, не трогая остальной pipeline.
    logger = _reset_logger(name, level)
    if enabled:
        logger.addHandler(_build_file_handler(file_path, formatter))
    else:
        logger.addHandler(logging.NullHandler())
    return logger


def setup_loggers(
    run_directory: Path,
    run_timestamp: str,
    log_level: str,
    save_console_log: bool,
    save_network_log: bool,
    save_error_log: bool,
) -> LoggerSet:
    # Разделяем application/browser/network/error логи, чтобы артефакты было удобно анализировать.
    level_value = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    app_logger = _reset_logger(f"browser_worker.app.{run_timestamp}", level_value)
    app_logger.addHandler(_build_file_handler(run_directory / f"000_application_{run_timestamp}.log", formatter))

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    app_logger.addHandler(stream_handler)

    console_logger = _build_optional_logger(
        name=f"browser_worker.console.{run_timestamp}",
        file_path=run_directory / f"000_browser_console_{run_timestamp}.log",
        level=logging.INFO,
        enabled=save_console_log,
        formatter=formatter,
    )
    network_logger = _build_optional_logger(
        name=f"browser_worker.network.{run_timestamp}",
        file_path=run_directory / f"000_network_{run_timestamp}.log",
        level=logging.INFO,
        enabled=save_network_log,
        formatter=formatter,
    )
    error_logger = _build_optional_logger(
        name=f"browser_worker.errors.{run_timestamp}",
        file_path=run_directory / f"000_errors_{run_timestamp}.log",
        level=logging.INFO,
        enabled=save_error_log,
        formatter=formatter,
    )

    return LoggerSet(
        app=app_logger,
        console=console_logger,
        network=network_logger,
        errors=error_logger,
    )
