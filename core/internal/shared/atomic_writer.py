#!/usr/bin/env python3
# GREP_SUMMARY: atomic-writer, atomic-write, atomic-write-json, atomic-write-text, fsync, os.replace, tempfile, validator, no-partial-write, canonical
# STRUCTURE: ▶ atomic_write ┌path, content, mode, validator, tmp_dir┐ → ⊕ mkdir parent → ⊕ NamedTemporaryFile(dir) → ⊕ write + flush + fsync → ⊕ chmod → ◇ validator(tmp)? → ⊕ os.replace → ⎋ Path │ ▶ atomic_write_json ┌path, data┐ → atomic_write(json.dumps) │ ▶ atomic_write_text ┌path, text┐ → atomic_write(text)
# region MODULE_CONTRACT
## @purpose  Canonical atomic file writer (DevPlan 119 E5) — tempfile + fsync + os.replace with
##           optional validator. ЕДИНСТВЕННЫЙ канон атомарной записи файлов для генераторов
##           платформы. Заменяет 12+ локальных копий os.replace/NamedTemporaryFile с разной
##           семантикой (часть без fsync, часть без chmod, часть без cleanup при ошибке).
## @scope    Consumed by генераторы (secrets_env_parser, docker_registry_auth, s3_ssl_cache,
##           docker_daemon, sudoers_generator, lifecycle helpers system/secrets, metrics cache,
##           sync_env_defaults, template_engine, node_yaml._write_back). НЕ мигрируется:
##           healthcheck/metrics/json_writer.py — Docker bind-mount (TRAP[DOCKER-BIND-MOUNT]
##           задокументирован в json_writer.py: os.replace создаёт новый inode → bind mount
##           видит старый inode → stale data).
## @invariants
##   - tempfile создаётся В ТОЙ ЖЕ директории, что target (same-filesystem rename)
##   - fsync вызывается ДО os.replace (crash-safe: содержимое на диске до переименования)
##   - optional validator(tmp_path) -> bool: False → отмена записи + cleanup temp
##   - os.replace атомарен на POSIX (читатель видит старый или новый файл, не частичный)
##   - mode применяется к temp ДО replace (нет окна с правами по умолчанию)
##   - Ошибка на любом шаге → temp unlink (нет мусора), target НЕ трогается
## @rationale Q: Зачем единый канон? A: аудит S5 (DevPlan 119) нашёл 12 файлов с os.replace и
##   16 с NamedTemporaryFile — разная семантика (fsync есть/нет, chmod до/после, cleanup
##   есть/нет). Единый writer с validator'ом (sudoers → visudo) устраняет дрейф и даёт
##   R5-negative-тест на отсутствие partial write при прерывании.
##   Q: Почему validator а не исключение? A: некоторые генераторы (sudoers) валидируют
##   содержимое ДО переименования (visudo -c -f) — отказ валидации ≠ ошибка I/O, это
##   бизнес-решение отменить запись.
## @changes 2026-08-02 · DevPlan 119 E5 — создан как канон; мигрированы генераторы
## @modulemap
##   atomic_write [W:1] — ядро: temp → write → fsync → chmod → validator → replace
##   atomic_write_json [W:1] — обёртка: json.dumps → atomic_write
##   atomic_write_text [W:1] — обёртка: text → atomic_write
## @usecases
##   - secrets_env_parser.write → atomic_write_text(path, content, mode=0o600)
##   - sudoers_generator._write_sudoers_file → atomic_write(path, content, validator=visudo)
##   - docker_registry_auth._write_daemon_json → atomic_write_json(path, data)
## @links    CONSUMERS(core/internal/shared/secrets_env_parser.py, core/internal/bootstrap/docker_registry_auth.py,
##           core/internal/bootstrap/docker_daemon.py, core/internal/bootstrap/lifecycle/helpers/system.py,
##           core/internal/bootstrap/lifecycle/secrets_manager.py, core/internal/bootstrap/s3_ssl_cache.py,
##           core/internal/bootstrap/deploy/sudoers_generator.py, core/internal/healthcheck/metrics/cache.py,
##           core/internal/scripts/sync_env_defaults.py, core/internal/template_engine.py,
##           core/internal/shared/node_yaml.py), EXCLUDED(core/internal/healthcheck/metrics/json_writer.py)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Type of the optional validator: receives the temp file path, returns True to commit.
Validator = Callable[[str], bool]


# region FUNC_atomic_write
## @purpose  Write content to path atomically: tempfile in same dir → write → flush → fsync →
##           chmod → optional validator(tmp_path) → os.replace. Failure at any step → temp
##           cleanup, target untouched (R5: no partial write).
## @io       ⇥ path: str | Path — target path
##           ⇥ content: str | bytes — content to write
##           ⇥ mode: int — file permissions (default 0o644)
##           ⇥ validator: Validator | None — called with tmp_path BEFORE replace; False → abort
##           ⇥ tmp_dir: str | Path | None — temp dir (default: same dir as path)
##           ⎋ Path — resolved target path
##           ⚡ OSError — write/fsync/chmod/replace failure; ValueError — validator rejected
## @complexity O(N) где N = len(content)
## @invariants
##   - tempfile in same dir (or tmp_dir) — same-filesystem os.replace
##   - fsync before replace — crash-safe
##   - chmod on temp before replace — no default-permission window
##   - validator False → temp cleanup, target NOT touched, ValueError raised
##   - All failure paths unlink temp (no garbage)
def atomic_write(
    path: str | Path,
    content: str | bytes,
    mode: int = 0o644,
    validator: Validator | None = None,
    tmp_dir: str | Path | None = None,
) -> Path:
    """Atomically write content to path (tempfile + fsync + os.replace + optional validator)."""
    target = Path(path)
    dir_path = Path(tmp_dir) if tmp_dir is not None else target.parent
    os.makedirs(dir_path, exist_ok=True)
    logger.info("[IMP:7][atomic_write][start] path=%s mode=0%o validator=%s", target, mode, bool(validator))

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w" if isinstance(content, str) else "wb",
            encoding="utf-8" if isinstance(content, str) else None,
            dir=str(dir_path),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_path = fh.name
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())

        os.chmod(tmp_path, mode)

        if validator is not None and not validator(tmp_path):
            logger.error(
                "[IMP:10][atomic_write][rejected] Validator rejected temp file %s — target %s untouched",
                tmp_path,
                target,
            )
            _cleanup(tmp_path)
            # Контракт B4 (DevPlan 116 B4 T2): структурная ошибка контента → ConfigValidationError
            # (не bare ValueError — гейт no_bare_raise).
            from core.internal.shared.exceptions import ConfigValidationError

            raise ConfigValidationError(f"Validator rejected atomic write to {target}")

        os.replace(tmp_path, target)
        tmp_path = None  # consumed by replace
        logger.info("[IMP:9][atomic_write][done] Atomic write committed: %s", target)
        return target
    except BaseException:
        # Any failure (I/O, validator) → cleanup temp, never leave garbage
        if tmp_path is not None:
            _cleanup(tmp_path)
        raise


# endregion FUNC_atomic_write


# region FUNC_atomic_write_json
## @purpose  JSON-специализация atomic_write: сериализует data в JSON-строку и пишет атомарно.
## @io       ⇥ path: str | Path, data: dict | list → ⎋ Path ⚡ OSError/ValueError
## @complexity O(N) где N = size of serialized JSON
## @invariants
##   - indent=2 + ensure_ascii=False (читаемый, UTF-8-safe)
##   - trailing newline (POSIX-стандарт)
def atomic_write_json(path: str | Path, data: dict | list, mode: int = 0o644) -> Path:
    """Serialize data to JSON and write atomically (indent=2, UTF-8, trailing newline)."""
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    logger.info("[IMP:8][atomic_write_json][start] path=%s", path)
    return atomic_write(path, payload, mode=mode)


# endregion FUNC_atomic_write_json


# region FUNC_atomic_write_text
## @purpose  Text-специализация atomic_write: обёртка для str-контента (явный API для текстовых
##           генераторов, чтобы не путать с bytes-записями бинарных файлов).
## @io       ⇥ path: str | Path, text: str → ⎋ Path ⚡ OSError/ValueError
## @complexity O(N) где N = len(text)
## @invariants
##   - Ничего не добавляет к text (caller ответственен за trailing newline)
def atomic_write_text(path: str | Path, text: str, mode: int = 0o644) -> Path:
    """Write text atomically (thin wrapper over atomic_write for str content)."""
    logger.info("[IMP:8][atomic_write_text][start] path=%s", path)
    return atomic_write(path, text, mode=mode)


# endregion FUNC_atomic_write_text


# region FUNC__cleanup
## @purpose  Unlink temp file best-effort (suppress OSError — cleanup не должен маскировать
##           исходную ошибку).
## @io       ⇥ path: str → ⎋ None
## @complexity O(1)
def _cleanup(path: str) -> None:
    """Remove a temp file, suppressing OSError (cleanup must not mask the original error)."""
    try:
        os.unlink(path)
        logger.debug("[IMP:5][atomic_write][cleanup] Removed temp %s", path)
    except OSError:
        logger.warning("[IMP:5][atomic_write][cleanup] Could not remove temp %s", path)


# endregion FUNC__cleanup
