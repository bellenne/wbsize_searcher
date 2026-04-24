from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def current_timestamp() -> str:
    """Возвращает timestamp для имён файлов и директорий."""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def create_run_directory(base_output_dir: Path, run_timestamp: str) -> Path:
    """Создаёт отдельную директорию запуска, чтобы артефакты разных прогонов не смешивались."""
    run_directory = base_output_dir / run_timestamp
    run_directory.mkdir(parents=True, exist_ok=True)
    return run_directory


def build_artifact_path(run_directory: Path, step_prefix: str, step_name: str, extension: str) -> Path:
    """Собирает понятное имя файла с шагом сценария и уникальным timestamp."""
    normalized_extension = extension.lstrip(".")
    return run_directory / f"{step_prefix}_{step_name}_{current_timestamp()}.{normalized_extension}"


def write_text_file(file_path: Path, content: str) -> None:
    """Пишет UTF-8 текст с Unix line endings для предсказуемого diff и чтения в Linux."""
    file_path.write_text(content, encoding="utf-8", newline="\n")


def write_json_file(file_path: Path, payload: dict[str, Any]) -> None:
    """Пишет JSON в человекочитаемом виде, потому что это отладочный артефакт."""
    file_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def find_text_occurrences(page_text: str, search_text: str) -> tuple[bool, int, list[int]]:
    """Возвращает факт наличия строки, число совпадений и позиции всех вхождений."""
    if not search_text:
        return False, 0, []

    matches = [match.start() for match in re.finditer(re.escape(search_text), page_text, re.IGNORECASE)]
    return bool(matches), len(matches), matches
