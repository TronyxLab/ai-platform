# GREP_SUMMARY: python-deps, python3.14, deadsnakes, pip, apt, requirements, content-hash, idempotent, ensurepip, symlink, DI, runner-param, facts-param, CommandRunner, EnvironmentFacts, import-probe, canonical-path, F-019
# STRUCTURE: ▶ ensure_python_deps → _resolve_requirements_path(canonical+F-019 self-heal) → _check_content_hash(hash+pyver) → ◇ match → _probe_critical_imports(boto3) ⚡ fail→reinstall / ok→no-op → _install_python314(PPA) → _install_requirements(/usr/local/bin/python3 -m pip) → ⎋ CLI

# region MODULE_CONTRACT
## @purpose  Idempotent install of Python 3.14 (deadsnakes PPA) + platform Python
##           dependencies on VPS. Ensures bare `python3` resolves to 3.14 via the
##           /usr/local/bin/python3 → /usr/bin/python3.14 symlink (PATH order).
## @scope    VPS bootstrap — Python 3.14 via deadsnakes PPA (Ubuntu 24.04),
##           python3.14-venv, ensurepip, requirements.txt, content-hash guard (hash + pyver).
## @invariants
##   - Fail-soft: returns False on failure, never raises
##   - Stdlib-only imports (hashlib, logging, os, re, subprocess, sys) — this module
##     runs under the OLD system python3 (3.12) to install 3.14
##   - Content-hash guard keyed by (requirements.txt hash + python version); old-format
##     markers (hash only) are treated as mismatch → reinstall (correct for 3.12→3.14)
##   - PEP 668 workaround: --break-system-packages (deadsnakes python3.14 is externally-managed)
##   - typing_extensions conflict: --ignore-installed first
##   - /usr/bin/python3 (system 3.12) is NEVER touched — only /usr/local/bin/python3 symlink
##   - Ubuntu != 24.04 → WARN + fallback to apt python3-pip (system python 3.12)
##   - All subprocess calls with capture_output=True, text=True
##   - E1 (160): runner/facts DI-параметры (runner=None → subprocess.run, facts=None →
##     реальные системные вызовы); поведение/exit-коды/идемпотентность НЕ изменены
##   - Plan 012 T2 (F-019): requirements резолвится ТОЛЬКО от `core_dir`/requirements.txt
##     (канон доставки); передача корня платформы самолечится WARN'ом → `root`/core/
##   - Plan 012 T2 (F-019): marker-match НЕ блокирует переустановку — выполняется
##     import-probe критичных модулей (boto3 минимум); проваленный probe → reinstall
## @rationale Shell→Python migration (Strangler-Fig). User decision 2026-08-01: deadsnakes
##            PPA for Python 3.14 on Ubuntu 24.04 (see TRAP[DECISION] below).
## @changes
##   2026-07-25  Initial port from node-lifecycle.sh:_ensure_python_deps()
##   2026-08-01  Python 3.14 via deadsnakes PPA; pip via /usr/local/bin/python3 -m pip;
##               hash marker now includes python version (old-format marker = mismatch)
##   2026-08-03  DevPlan 123 T11 (FL7) — Step 2 аудит: остальные пакеты requirements.txt
##               НЕ конфликтуют с apt python3-* (python3-yaml/jinja2/cryptography удовлетворяют
##               пины; boto3/botocore/httpx/pydantic не ставятся apt; httpcore — транзитив httpx)
##   2026-08-13  DevPlan 160 E1 — +runner: CommandRunner / facts: EnvironmentFacts (DI)
##   2026-08-26  Plan 012 T2 (F-019) — canonical requirements path + import-probe
##               invalidation of marker (boto3); F-019 self-heal для корня платформы
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-08-01 · HI · Python 3.14 через deadsnakes PPA (Ubuntu 24.04)
# · Rejected: uv (новый инструмент + curl-скрипт с GitHub — внешняя поставка вне apt) /
# ·           source build (5-15 мин компиляции на голом сервере)
# · Reason: deadsnakes = официальный PPA-канал, apt-управляемый, ~30-60s установка;
# ·         ensurepip --upgrade гарантирует pip без отдельного pip-пакета
# · Rev: если 3.14 исчезнет из deadsnakes или появится в официальном universe →
# ·      перейти на стандартный apt-репозиторий Ubuntu, убрать PPA-ветку

import hashlib
import logging
import os
import pathlib
import re
import subprocess
import sys

from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.subprocess_io import CommandRunner

# W1-A1 (план 170): apt-get timeout=600 → канон APT_TIMEOUT (300). Фикс рассинхрона политики:
# python_deps φ1 обходил канон APT_TIMEOUT=300 (T7-гейт покрывал только system.py/tor_setup/
# install_acme — НЕ φ1). Значение 600 было локальным литералом-дублём SoT (см. TRAP[BUG] ниже).
from core.internal.shared.timeouts import APT_TIMEOUT

logger = logging.getLogger(__name__)

# ⚠️ TRAP[BUG] · 2026-08-14 · P1 · apt-get timeout=600 обходил канон APT_TIMEOUT=300 (план 170 W1-A1)
# · Symptom: python_deps.py:372-402 использовал timeout=600 для apt-get install/update/add-apt-repository,
# ·   тогда как канон shared/timeouts.APT_TIMEOUT=300 (DevPlan 123 T7) применялся к остальной
# ·   bootstrap-цепи (system.py/tor_setup/install_acme). T7-гейт (test_apt_timeouts_use_canon)
# ·   НЕ покрывал φ1 python_deps — рассинхрон не детектировался.
# · Root: python_deps.py добавлен в bootstrap-цепь после T7 (волна 160 E1); литерал 600 скопирован
# ·   из общего bootstrap-шаблона, канон APT_TIMEOUT не был применён к новому файлу.
# · Fix: 4 вызова apt-get (372/382/392/402) переведены на timeout=APT_TIMEOUT (300) — импорт из SoT.
# ·   Уникальное значение 600 в этом файле больше не существует.
# · Prevention: T7-гейт расширен на python_deps.py (test_apt_timeouts_use_canon) — канон APT_TIMEOUT
# ·   enforce-ится для ВСЕЙ apt bootstrap-цепи; новые apt-вызовы обязаны импортировать константу.

# 🧐 TRAP[DECISION] · 2026-08-26 · — · F-019 self-heal: корень платформы в core_dir → WARN + канонический путь
# · Rejected: строгий fail-loud без самолечения (оператор вручную cp requirements.txt —
#   именно обход, который план 012 устраняет) / тихий fallback без WARN (invisible failure)
# · Reason: one-command bootstrap (AC1) требует самолечения класса ошибок; WARN [IMP:9]
#   сохраняет честность диагноза для оператора
# · Rev: если появятся легитимные не-core каталоги с requirements.txt рядом с core_dir —
#   заменить эвристику «core.name == 'core'» на явный контракт вызывающего
_MARKER_PARTS: int = 2  # маркер: <requirements-hash>\n<python-version>

# Plan 012 T2 (F-019): критичные модули для import-probe при marker-match.
# Расширяется по мере роста критичности (первый элемент — boto3: S3-cache/certs).
CRITICAL_IMPORT_PROBES: tuple[str, ...] = ("boto3",)

HASH_DIR = "/var/lib/platform/.bootstrap"
HASH_FILE = os.path.join(HASH_DIR, "python-deps.hash")


# region FUNC__resolve_requirements_path
## @purpose  Каноническое разрешение requirements.txt: ТОЛЬКО `core_dir`/requirements.txt
##           (канон доставки core на ноду). Самолечение F-019: если caller передал корень
##           платформы вместо core/ — WARN с точным диагнозом + канонический путь.
## @io       core_dir: str → str (путь к requirements.txt)
## @complexity O(1) — 1-2 filesystem checks
## @invariants
##   - Файл берётся ИЗ каталога core (канон доставки), никогда не из корня платформы
## @changes 2026-08-26 | Plan 012 T2 (F-019) — created
def _resolve_requirements_path(core_dir: str) -> str:
    # endregion FUNC__resolve_requirements_path
    # abspath (лексическая нормализация «..») БЕЗ resolve(): resolve() трогает симлинки
    # (/var → /private/var на macOS) и ломает сравнение путей в тестах/логах.
    # ruff: ignore[PTH100] — намеренный lexical-normalize без симлинк-резолва
    core = pathlib.Path(os.path.abspath(core_dir))
    req = core / "requirements.txt"
    if req.is_file():
        logger.info("[IMP:9][_resolve_requirements_path] Canonical requirements: %s", req)
        return str(req)

    # F-019 self-heal: caller передал КОРЕНЬ платформы (root без /core).
    candidate_core = core if core.name == "core" else core / "core"
    healed = candidate_core / "requirements.txt"
    if healed.is_file():
        logger.warning(
            "[IMP:9][_resolve_requirements_path] requirements.txt НЕ найден в %s, но найден в %s "
            "— в core_dir передан КОРЕНЬ платформы вместо core/ (инцидент F-019). "
            "Использую канонический путь; почини вызывающего (CORE_DIR должен указывать на core/).",
            req,
            healed,
        )
        return str(healed)

    logger.warning("[IMP:7][_resolve_requirements_path] requirements.txt not found at canonical path %s", req)
    return str(req)


# region FUNC__probe_one
## @purpose  Import-probe одного модуля в целевом интерпретаторе: rc==0 → ок.
## @io       module: str, python_bin: str, runner DI → bool
## @complexity O(1) — single subprocess
def _probe_one(module: str, python_bin: str, *, runner: CommandRunner | None = None) -> bool:
    # endregion FUNC__probe_one
    cmd = [python_bin, "-c", f"import {module}"]
    try:
        if runner is None:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
            rc = result.returncode
        else:
            rc = runner.run(cmd, timeout=60).returncode
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0


# region FUNC__probe_critical_imports
## @purpose  Import-probe критичных модулей (boto3 минимум) в АКТИВНОМ интерпретаторе
##           платформы. Инвалидатор маркера: marker-match + проваленный probe → reinstall.
## @io       facts/runner DI → tuple[bool, list[str]] — (все ок, список проваленных модулей)
## @complexity O(P) subprocess probes, P = len(CRITICAL_IMPORT_PROBES)
## @rationale Q: почему subprocess, а не importlib.util.find_spec? A: маркер проверяет
##            интерпретатор /usr/local/bin/python3 (3.14), а сам python_deps может исполняться
##            другим python — probe через целевой бинарь честнее in-process find_spec.
## @changes 2026-08-26 | Plan 012 T2 (F-019) — created
def _probe_critical_imports(
    *,
    facts: EnvironmentFacts | None = None,
    runner: CommandRunner | None = None,
) -> tuple[bool, list[str]]:
    # endregion FUNC__probe_critical_imports
    python_bin = _resolve_python_bin(facts=facts)
    failed: list[str] = []
    for module in CRITICAL_IMPORT_PROBES:
        if _probe_one(module, python_bin, runner=runner):
            logger.info("[IMP:8][_probe_critical_imports] Import-probe ok: %s (%s)", module, python_bin)
        else:
            failed.append(module)
            logger.warning("[IMP:7][_probe_critical_imports] Import-probe FAILED for %r via %s", module, python_bin)
    return (not failed, failed)


# region FUNC__load_saved_hash
## @purpose  Read previously saved content hash from disk
## @io       hash_file: str | None (None = HASH_FILE) → str | None
## @complexity O(1)
## @changes 2026-08-13 | E1 (160): +hash_file параметр (тесты передают tmp_path без monkeypatch HASH_FILE)
def _load_saved_hash(hash_file: str | None = None) -> str | None:
    # endregion FUNC__load_saved_hash
    path = HASH_FILE if hash_file is None else hash_file
    try:
        with pathlib.Path(path).open(encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("[IMP:7][_load_saved_hash] Cannot read hash file %s", path)
        return None


# region FUNC__compute_content_hash
## @purpose  Compute sha256 hex digest of a file
## @io       path → str | None (None if file missing/unreadable)
## @complexity O(n) in file size, O(1) in logic
def _compute_content_hash(req_path: str) -> str | None:
    # endregion FUNC__compute_content_hash
    try:
        h = hashlib.sha256()
        with pathlib.Path(req_path).open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("[IMP:7][_compute_content_hash] Cannot read %s", req_path)
        return None


# region FUNC__save_content_hash
## @purpose  Persist sha256 hex digest to HASH_FILE
## @io       hash_str: str, hash_file: str | None (None = HASH_FILE) → bool
## @complexity O(1)
## @changes 2026-08-13 | E1 (160): +hash_file параметр (тесты передают tmp_path без monkeypatch HASH_FILE/HASH_DIR)
def _save_content_hash(hash_str: str, hash_file: str | None = None) -> bool:
    # endregion FUNC__save_content_hash
    path = HASH_FILE if hash_file is None else hash_file
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with pathlib.Path(path).open("w", encoding="utf-8") as f:
            f.write(hash_str + "\n")
    except OSError:
        logger.warning("[IMP:7][_save_content_hash] Cannot write hash to %s", path)
        return False
    else:
        return True


# region FUNC__compute_python_version
## @purpose  Report the version of /usr/local/bin/python3 (the interpreter that will run
##           platform code after 3.14 install). 'unknown' if not yet installed.
## @io       → str like '3.14.5' or 'unknown'
## @complexity O(1) — single subprocess
## @changes 2026-08-13 | E1 (160): +runner DI — runner=None → subprocess.run (default),
##            runner задан → runner.run (fake scripted; rc!=0 → "unknown" сохранено)
def _compute_python_version(*, runner: CommandRunner | None = None) -> str:
    # endregion FUNC__compute_python_version
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        if runner is None:
            result = subprocess.run(
                ["/usr/local/bin/python3", "--version"], capture_output=True, text=True, timeout=30, check=False
            )
        else:
            result = runner.run(["/usr/local/bin/python3", "--version"], timeout=30, check=False)
        if result.returncode != 0:
            return "unknown"
        version_str = (result.stdout or result.stderr).strip()
        match = re.match(r"Python (\d+\.\d+(?:\.\d+)?)", version_str)
        if match:
            return match.group(1)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        logger.info("[IMP:7][_compute_python_version] /usr/local/bin/python3 not available yet")
        return "unknown"
    else:
        return "unknown"


# region FUNC__python314_installed
## @purpose  Idempotent quick check: is /usr/local/bin/python3 already Python 3.14.x?
## @io       → bool (True = 3.14 active, skip install)
## @complexity O(1) — delegates to _compute_python_version
def _python314_installed(*, runner: CommandRunner | None = None) -> bool:
    # endregion FUNC__python314_installed
    version = _compute_python_version(runner=runner)
    installed = version.startswith("3.14")
    if installed:
        logger.info("[IMP:9][_python314_installed] /usr/local/bin/python3 = Python %s — no-op", version)
    else:
        logger.info(
            "[IMP:8][_python314_installed] /usr/local/bin/python3 not 3.14 (got %r) — install required", version
        )
    return installed


# region FUNC__detect_ubuntu_version
## @purpose  Read VERSION_ID from /etc/os-release (e.g. '24.04'). None if unreadable/not Ubuntu.
## @io       os_release_path: str | None (None = /etc/os-release) → str | None
## @complexity O(n) — line scan of /etc/os-release
## @changes 2026-08-13 | E1 (160): +os_release_path параметр (тесты передают tmp_path без monkeypatch)
def _detect_ubuntu_version(os_release_path: str | None = None) -> str | None:
    # endregion FUNC__detect_ubuntu_version
    path = "/etc/os-release" if os_release_path is None else os_release_path
    try:
        with pathlib.Path(path).open(encoding="utf-8") as f:
            for line in f:
                if line.startswith("VERSION_ID="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        logger.warning("[IMP:7][_detect_ubuntu_version] Cannot read %s", path)
    return None


# region FUNC__check_content_hash
## @purpose  Compare (requirements.txt hash + python version) against saved marker.
##           Old-format markers (hash only, pre-3.14 era) are treated as mismatch →
##           forces reinstall, which is the correct transition for 3.12→3.14.
## @io       req_path → bool (True=matches, skip install)
## @complexity O(n) file read + hexdigest + version probe
## @changes 2026-08-13 | E1 (160): +runner/hash_file DI (threads to _compute_python_version/_load_saved_hash)
def _check_content_hash(
    req_path: str,
    *,
    runner: CommandRunner | None = None,
    hash_file: str | None = None,
) -> bool:
    # endregion FUNC__check_content_hash
    saved = _load_saved_hash(hash_file)
    if saved is None:
        logger.info("[IMP:9][_check_content_hash] No saved marker — install required")
        return False

    # New marker format: "<sha256>\n<python_version>". Old format = hash only → mismatch.
    parts = saved.split("\n")
    if len(parts) != _MARKER_PARTS or not parts[0] or not parts[1]:
        logger.info(
            "[IMP:9][_check_content_hash] Old-format marker (no python version) — reinstall required (3.12→3.14)"
        )
        return False

    saved_hash, saved_pyver = parts[0], parts[1]

    current = _compute_content_hash(req_path)
    if current is None:
        logger.info("[IMP:9][_check_content_hash] Cannot compute hash — install required")
        return False

    current_pyver = _compute_python_version(runner=runner)
    if saved_hash == current and saved_pyver == current_pyver:
        logger.info("[IMP:9][_check_content_hash] Hash + python version match — skipping pip install")
        return True

    logger.info(
        "[IMP:9][_check_content_hash] Hash or python version mismatch (saved=%s, current=%s) — install required",
        saved_pyver,
        current_pyver,
    )
    return False


# region FUNC__run
## @purpose  Run a subprocess with uniform error handling
## @io       cmd list → bool (True=exit 0)
## @complexity O(1) — wraps subprocess.run
## @changes 2026-08-13 | E1 (160): +runner DI — runner=None → subprocess.run (default, env
##            пробрасывается), runner задан → runner.run (fake; env/прочие kwargs не передаются,
##            W4d-канон core_deliverer._run_cmd)
def _run(
    cmd: list[str],
    label: str = "",
    env: dict[str, str] | None = None,
    timeout: int = 120,
    *,
    runner: CommandRunner | None = None,
) -> bool:
    # endregion FUNC__run
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        if runner is None:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        else:
            result = runner.run(cmd, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr.strip()[:500]
            logger.warning(
                "[IMP:7][_run] %s failed (rc=%d): %s",
                label or cmd[0],
                result.returncode,
                stderr,
            )
            return False
    except FileNotFoundError:
        logger.warning("[IMP:7][_run] %s — command not found", label or cmd[0])
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][_run] %s — timed out after %ds", label or cmd[0], timeout)
        return False
    except OSError as exc:
        logger.warning("[IMP:7][_run] %s — OS error: %s", label or cmd[0], exc)
        return False
    else:
        return True


# region FUNC__install_pip3
## @purpose  FALLBACK branch (Ubuntu != 24.04): install python3-pip and python3-venv
##           via apt-get for the SYSTEM python3.12. Not used on 24.04 — there the
##           canonical path is _install_python314() (deadsnakes PPA).
## @io       → bool
## @complexity O(1) — 2 subprocess calls max
## @changes 2026-08-13 | E1 (160): +runner/facts DI — which pip3 через facts.which (shutil.which
##            вместо subprocess ["which", "pip3"]), apt-команды через runner
def _install_pip3(*, runner: CommandRunner | None = None, facts: EnvironmentFacts | None = None) -> bool:
    # endregion FUNC__install_pip3
    # Quick check — if pip3 is already available, skip apt-get (facts.which = shutil.which канон)
    if (facts or default_env_facts()).which("pip3"):
        logger.info("[IMP:9][_install_pip3] pip3 already installed")
        return True

    logger.info("[IMP:9][_install_pip3] pip3 not found — installing python3-pip + python3-venv")

    if not _run(
        ["apt-get", "update", "-qq"],
        label="apt-get update",
        runner=runner,
    ):
        return False

    return _run(
        ["apt-get", "install", "-y", "-qq", "python3-pip", "python3-venv"],
        label="apt-get install python3-pip python3-venv",
        runner=runner,
    )


# region FUNC__resolve_python_bin
## @purpose  Resolve the python interpreter used for pip: prefer /usr/local/bin/python3
##           (the 3.14 symlink) when present, else bare system python3 (non-24.04 fallback).
## @io       → str (binary path or bare name)
## @complexity O(1)
## @changes 2026-08-13 | E1 (160): +facts DI — os.path.isfile → facts.path_isfile
def _resolve_python_bin(*, facts: EnvironmentFacts | None = None) -> str:
    # endregion FUNC__resolve_python_bin
    if (facts or default_env_facts()).path_isfile("/usr/local/bin/python3"):
        return "/usr/local/bin/python3"
    logger.info("[IMP:8][_resolve_python_bin] /usr/local/bin/python3 absent — using system python3")
    return "python3"


# region FUNC__apt_env
## @purpose  DEBIAN_FRONTEND=noninteractive env для apt-операций (bare-server bootstrap,
##           никаких интерактивных промптов). Вынесен для тестируемости (E1): инвариант
##           «noninteractive на каждом apt-шаге» проверяется юнит-тестом без subprocess.
## @io       → dict[str, str] (копия os.environ + DEBIAN_FRONTEND=noninteractive)
## @complexity O(1) — dict merge
def _apt_env() -> dict[str, str]:
    return {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}


# endregion FUNC__apt_env


# region FUNC__install_python314
## @purpose  Install Python 3.14 from deadsnakes PPA on Ubuntu 24.04. Idempotent:
##           fast no-op if /usr/local/bin/python3 already reports 3.14.x.
##           Non-24.04 → WARN + fallback to _install_pip3() (system python).
## @io       → bool
## @complexity O(1) probe + O(6) apt/ensurepip/symlink subprocess calls
## @invariants
##   - /usr/bin/python3 (system 3.12) is NEVER modified — only /usr/local/bin/python3 symlink
##   - DEBIAN_FRONTEND=noninteractive for all apt operations (bare-server bootstrap)
##   - ensurepip --upgrade guarantees pip for 3.14 (deadsnakes has no pip package)
## @changes 2026-08-13 | E1 (160): +runner/facts/os_release_path DI (threads to _python314_installed/_run/_install_pip3)
def _install_python314(
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    os_release_path: str | None = None,
) -> bool:
    # endregion FUNC__install_python314
    if _python314_installed(runner=runner):
        return True

    version_id = _detect_ubuntu_version(os_release_path)
    if version_id != "24.04":
        logger.warning(
            "[IMP:7][_install_python314] Ubuntu version %r != 24.04 — falling back to system python3-pip "
            "(Python 3.14 PPA install skipped)",
            version_id,
        )
        return _install_pip3(runner=runner, facts=facts)

    logger.info("[IMP:9][_install_python314] Ubuntu 24.04 — installing Python 3.14 from deadsnakes PPA")

    # DEBIAN_FRONTEND=noninteractive prevents interactive prompts on a bare server.
    env: dict[str, str] = _apt_env()

    # 1. add-apt-repository lives in software-properties-common
    if not _run(
        ["apt-get", "install", "-y", "-qq", "software-properties-common"],
        label="apt-get install software-properties-common",
        env=env,
        timeout=APT_TIMEOUT,
        runner=runner,
    ):
        return False

    # 2. Add deadsnakes PPA
    if not _run(
        ["add-apt-repository", "-y", "ppa:deadsnakes/ppa"],
        label="add-apt-repository ppa:deadsnakes/ppa",
        env=env,
        timeout=APT_TIMEOUT,
        runner=runner,
    ):
        return False

    # 3. Refresh package index with the new PPA
    if not _run(
        ["apt-get", "update", "-qq"],
        label="apt-get update",
        env=env,
        timeout=APT_TIMEOUT,
        runner=runner,
    ):
        return False

    # 4. Install Python 3.14 interpreter + venv module
    if not _run(
        ["apt-get", "install", "-y", "-qq", "python3.14", "python3.14-venv"],
        label="apt-get install python3.14 python3.14-venv",
        env=env,
        timeout=APT_TIMEOUT,
        runner=runner,
    ):
        return False

    # 5. ensurepip — deadsnakes does not guarantee a pip package; ensurepip is the safe path
    if not _run(
        ["/usr/bin/python3.14", "-m", "ensurepip", "--upgrade"],
        label="python3.14 -m ensurepip --upgrade",
        env=env,
        timeout=300,
        runner=runner,
    ):
        return False

    # 6. Symlink /usr/local/bin/python3 → /usr/bin/python3.14 (PATH: /usr/local/bin precedes /usr/bin)
    if not _run(
        ["ln", "-sfn", "/usr/bin/python3.14", "/usr/local/bin/python3"],
        label="ln -sfn /usr/bin/python3.14 /usr/local/bin/python3",
        env=env,
        timeout=60,
        runner=runner,
    ):
        return False

    if not _python314_installed(runner=runner):
        logger.warning("[IMP:7][_install_python314] Post-install verification failed — 3.14 not active")
        return False

    logger.info("[IMP:9][_install_python314] Python 3.14 installed and active at /usr/local/bin/python3")
    return True


# region FUNC__install_requirements
## @purpose  Install typing_extensions (--ignore-installed) then -r requirements.txt
##           into the ACTIVE platform interpreter via `python -m pip` (no pip3 binaries).
## @io       core_dir → bool
## @complexity O(n) — 2 pip subprocess calls
## @changes 2026-08-13 | E1 (160): +runner/facts DI — os.path.isfile → facts.path_isfile,
##            pip-команды через runner
def _install_requirements(
    core_dir: str, *, runner: CommandRunner | None = None, facts: EnvironmentFacts | None = None
) -> bool:
    # endregion FUNC__install_requirements
    req_path = os.path.join(core_dir, "requirements.txt")
    if not (facts or default_env_facts()).path_isfile(req_path):
        logger.warning("[IMP:7][_install_requirements] No requirements.txt at %s", req_path)
        return False

    python_bin = _resolve_python_bin(facts=facts)
    logger.info(
        "[IMP:9][_install_requirements] Using interpreter %s for pip (bare `python3` resolves here)", python_bin
    )

    # Step 1: typing_extensions with --ignore-installed (Debian conflict workaround)
    logger.info("[IMP:9][_install_requirements] Installing typing_extensions (--ignore-installed)")
    pip_typing = [
        python_bin,
        "-m",
        "pip",
        "install",
        "typing_extensions",
        "--ignore-installed",
        "--break-system-packages",
    ]
    if not _run(pip_typing, label="pip typing_extensions", runner=runner):
        return False

    # Step 1b: jsonschema with --ignore-installed — RC-сессия 2026-08-03 (e2e φ1 fail на bare VPS)
    # · Symptom: pip -r requirements.txt: "Cannot uninstall jsonschema 4.10.3 (installed by debian,
    #   no RECORD)" — φ1 ставит apt python3-jsonschema (4.10.3, без RECORD), requirements требует
    #   >=4.17 → pip не может заменить debian-пакет.
    # · Fix: тот же паттерн, что typing_extensions (--ignore-installed) — ставит свежую версию
    #   поверх, не трогая debian-пакет.
    logger.info("[IMP:9][_install_requirements] Installing jsonschema (--ignore-installed)")
    pip_jsonschema = [
        python_bin,
        "-m",
        "pip",
        "install",
        "jsonschema",
        "--ignore-installed",
        "--break-system-packages",
    ]
    if not _run(pip_jsonschema, label="pip jsonschema", runner=runner):
        return False

    # Step 1c: pyopenssl with --ignore-installed — RC-сессия 2026-08-03 (e2e preflight fail)
    # · Symptom: preflight PanicException pyo3_runtime — import OpenSSL (debian pyOpenSSL 23.2)
    #   падает с cryptography 41.0.7 (pip, --break-system-packages) на Python 3.14
    # · Root: boto3 (dist-packages) → botocore → pyopenssl (debian 23.2) — несовместим с новой
    #   cryptography + 3.14. requirements.txt не пинит pyopenssl → остаётся debian-версия.
    # · Fix: тот же паттерн --ignore-installed (свежий pyopenssl поверх debian-пакета).
    logger.info("[IMP:9][_install_requirements] Installing pyopenssl (--ignore-installed)")
    pip_openssl = [
        python_bin,
        "-m",
        "pip",
        "install",
        "pyopenssl",
        "--ignore-installed",
        "--break-system-packages",
    ]
    if not _run(pip_openssl, label="pip pyopenssl", runner=runner):
        return False

    # Step 2: full requirements.txt — аудит DevPlan 123 T11 (FL7): остальные пакеты НЕ
    # конфликтуют с apt python3-* пакетами bare Ubuntu 24.04 (φ1 + cloud-init):
    #   · python3-yaml (6.0.1, φ1 system.py:76) / python3-jinja2 (3.1.4, cloud-init) /
    #     python3-cryptography (41.0.7, cloud-init via python3-openssl) — УДОВЛЕТВОРЯЮТ пины
    #     pyyaml>=6.0 / jinja2>=3.1.0 / cryptography>=41.0.0 → pip "already satisfied",
    #     uninstall не пытается → RECORD-конфликт невозможен
    #   · jsonschema — apt 4.10.3 < >=4.17 → RECORD-конфликт → уже вынесен в Step 1b
    #   · boto3/botocore/httpx/pydantic — apt-аналогов НЕТ на bare VPS (не в φ1, не в cloud-init)
    #   · httpcore/requests/python-dotenv не входят в requirements.txt: httpcore —
    #     транзитивная зависимость httpx (поставится автоматически), requests/dotenv — dev-only
    logger.info("[IMP:9][_install_requirements] Installing -r requirements.txt")
    pip_reqs = [
        python_bin,
        "-m",
        "pip",
        "install",
        "-r",
        req_path,
        "--break-system-packages",
    ]
    return _run(pip_reqs, label="pip -r requirements.txt", runner=runner)


# region FUNC_ensure_python_deps
## @purpose  Idempotent install of Python 3.14 + platform Python dependencies on VPS.
##           Port of node-lifecycle.sh:_ensure_python_deps(), extended with 3.14 (deadsnakes).
## @io       core_dir → bool
## @complexity O(1) marker check + O(P) interpreter install + O(n) pip install on mismatch
## @changes 2026-08-13 | E1 (160): +runner/facts/hash_file DI (threads по всей цепочке)
def ensure_python_deps(
    core_dir: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    hash_file: str | None = None,
    os_release_path: str | None = None,
) -> bool:
    """
    Idempotent Python dependency installer.

    Parameters
    ----------
    core_dir : str
        Path to the platform core directory containing requirements.txt.
    runner : CommandRunner | None
        DI: fake-раннер для тестов (None = реальный subprocess).
    facts : EnvironmentFacts | None
        DI: fake-факты для тестов (None = реальные системные).
    hash_file : str | None
        DI: путь маркера content-hash (None = HASH_FILE; тесты передают tmp_path).
    os_release_path : str | None
        DI: путь /etc/os-release (None = реальный; тесты передают tmp_path).

    Returns
    -------
    bool
        True if all deps are satisfied (installed or already present).
    """
    # endregion FUNC_ensure_python_deps

    # Plan 012 T2 (F-019): каноническое разрешение requirements (core-dir, не корень платформы)
    req_path = _resolve_requirements_path(core_dir)
    logger.info("[IMP:9][ensure_python_deps] Start — core_dir=%s req=%s", core_dir, req_path)

    # ── Content-hash guard (requirements hash + python version + import-probe) ──
    if _check_content_hash(req_path, runner=runner, hash_file=hash_file):
        ok_probe, failed_modules = _probe_critical_imports(facts=facts, runner=runner)
        if ok_probe:
            logger.info(
                "[IMP:9][ensure_python_deps] Hash + python version match + import-probe OK — deps already up to date"
            )
            return True
        logger.warning(
            "[IMP:9][ensure_python_deps] Marker matched but import-probe FAILED for %s — reinstalling "
            "(marker does not block, F-019: boto3 отсутствовал при ложном «match»)",
            failed_modules,
        )

    # ── Install Python 3.14 (deadsnakes PPA on Ubuntu 24.04) ───────────────
    if not _install_python314(runner=runner, facts=facts, os_release_path=os_release_path):
        logger.warning("[IMP:7][ensure_python_deps] Python 3.14 / pip installation failed")
        return False

    # ── Install Python requirements into the active interpreter ────────────
    resolved_core = str(pathlib.Path(req_path).parent)
    if not _install_requirements(resolved_core, runner=runner, facts=facts):
        logger.warning("[IMP:7][ensure_python_deps] Requirements installation failed")
        return False

    # ── Persist marker (requirements hash + python version) ────────────────
    current_hash = _compute_content_hash(req_path)
    if current_hash is None:
        logger.warning("[IMP:7][ensure_python_deps] Cannot compute hash after install — skipping persist")
        return False

    marker = f"{current_hash}\n{_compute_python_version(runner=runner)}"
    if not _save_content_hash(marker, hash_file):
        logger.warning("[IMP:7][ensure_python_deps] Failed to persist content hash")
        # Not fatal — deps are installed, hash is a cache optimization

    logger.info("[IMP:9][ensure_python_deps] Complete — Python 3.14 + dependencies installed")
    return True


# region FUNC_CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Idempotent Python dependency installer for platform VPS")
    parser.add_argument("action", choices=["ensure"])
    parser.add_argument("--core-dir", required=True)

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.action: str
            self.core_dir: str

    args = parser.parse_args(namespace=_CliArgs())

    success = ensure_python_deps(args.core_dir)
    sys.exit(0 if success else 1)
# endregion FUNC_CLI
