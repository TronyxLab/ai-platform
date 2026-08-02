#!/usr/bin/env python3
# GREP_SUMMARY: hermes-init s6 cont-init profile-creation context-overlay guard idempotent chown volume-permissions
# STRUCTURE: ▶ read CONTEXT (env|s6 file) → setup_dirs (templates→profiles, idempotent) → check_config (context config rsync) → init_state (profiles overlay + guard + volume perms + chown) → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  s6 cont-init.d бизнес-логика hermes-agent: создание профилей из шаблонов + context overlay
##           (DevPlan 119 D5, AUDIT-1 F7). Перенос init.sh (157 LOC) → init.py; init.sh — тонкий wrapper.
## @scope    Запускается init.sh (exec python3 /usr/local/bin/init.py) в /etc/cont-init.d/02-platform-init.
##           Все пути инжектируются через конструктор (defaults = /opt/* контейнерные пути) — тестируемо.
## @invariants
##   - Идемпотентность: существующий dest/config.yaml → SKIP; guard-файл → overlay не повторяется
##   - Profile creation: копирование ТОЛЬКО при отсутствии dest/config.yaml (пользовательские правки сохраняются)
##   - Context config overlay: rsync -a CONTEXT_DIR/config/ → /opt/hermes/config/
##   - Context profile overlay: rsync -a --ignore-existing + touch guard (базовые профили приоритетны)
##   - Volume permissions: тест записи /opt/data; chown 10000:10000 если root (non-fatal)
##   - Exits 0 всегда при успехе; CONTEXT не валидируется как строка (только INFO/WARN лог)
##   - main() -> int канон (core/AGENTS.md); OSError при rsync → лог ERROR + exit 1 (fail-fast)
## @rationale D5 (DevPlan 119): init.sh (157 LOC) — cont-init бизнес-логика без unit-тестов.
##   Python-класс + тонкий wrapper (5 LOC) + unit-тесты идемпотентности/guard (R5 parity).
## @changes  2026-08-02 | DevPlan 119 D5 — Created (test-first: tests/unit/test_hermes_init.py)
## @see      core/modules/hermes-agent/build/scripts/init.sh (тонкий wrapper)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Канонические пути контейнера (defaults — переопределяются в тестах через конструктор)
DEFAULT_TEMPLATES = Path("/opt/hermes/templates/profiles")
DEFAULT_DATA = Path("/opt/data/profiles")
DEFAULT_CONTEXT_DIR = Path("/opt/hermes/context")
DEFAULT_CONTEXT_GUARD = Path("/opt/data/.context-overlay-applied")
DEFAULT_HERMES_CONFIG = Path("/opt/hermes/config")
DEFAULT_S6_ENV_DIR = Path("/run/s6/container_environment")
HERMES_UID = 10000
HERMES_GID = 10000


# region FUNC__default_rsync
def _default_rsync(src: Path, dest: Path, ignore_existing: bool = False) -> None:
    """rsync -a [--ignore-existing] src/ → dest/ (субпроцесс; контейнерный канон init.sh).

    ## @purpose — I/O-примитив overlay (инжектируется в HermesInit для тестов).
    ## @raises — subprocess.CalledProcessError при rc!=0 (fail-fast)
    """
    cmd = ["rsync", "-a"]
    if ignore_existing:
        cmd.append("--ignore-existing")
    subprocess.run([*cmd, f"{src}/", f"{dest}/"], check=True)


# endregion FUNC__default_rsync


# region CLASS_HermesInit
class HermesInit:
    """s6 cont-init.d логика (DevPlan 119 D5) — профили + context overlay + ownership.

    ▶ ┌paths┐ → ○ read_s6_env(CONTEXT) → ○ setup_dirs() → ○ check_config() → ○ init_state() → ⎋ run() -> int
    """

    # region FUNC___init__
    def __init__(
        self,
        *,
        templates: Path = DEFAULT_TEMPLATES,
        data: Path = DEFAULT_DATA,
        context_dir: Path = DEFAULT_CONTEXT_DIR,
        context_guard: Path = DEFAULT_CONTEXT_GUARD,
        hermes_config: Path = DEFAULT_HERMES_CONFIG,
        s6_env_dir: Path = DEFAULT_S6_ENV_DIR,
        uid: int = HERMES_UID,
        gid: int = HERMES_GID,
        rsync=None,
    ) -> None:
        ## @purpose — инжекция путей (defaults = контейнерные /opt/*) — тестируемость без root.
        self.templates = Path(templates)
        self.data = Path(data)
        self.context_dir = Path(context_dir)
        self.context_guard = Path(context_guard)
        self.hermes_config = Path(hermes_config)
        self.s6_env_dir = Path(s6_env_dir)
        self.uid = uid
        self.gid = gid
        self.rsync = rsync or _default_rsync

    # endregion FUNC___init__

    # region FUNC_read_s6_env
    def read_s6_env(self, varname: str, current_env: str | None = None) -> str:
        """Прочитать переменную s6-overlay окружения (fallback на файл).

        ## @purpose — эквивалент _read_s6_env из init.sh.
        ## ⚠️ TRAP[BUG] · 2026-07-10 · P1 · CONTEXT не виден в s6-overlay cont-init.d
        ## · s6-overlay хранит env в /run/s6/container_environment/ но НЕ экспортирует в cont-init.d.
        ## · Fallback: чтение из файла напрямую.
        ## @io — ⇥ varname: str, current_env: str | None → ⎋ str (значение или "")
        """
        if current_env:
            return current_env
        env_file = self.s6_env_dir / varname
        if env_file.is_file():
            value = env_file.read_text(encoding="utf-8").strip()
            logger.info("[IMP:8][INIT][S6ENV] Read %s from %s: %s", varname, env_file, value)
            return value
        return ""

    # endregion FUNC_read_s6_env

    # region FUNC_setup_dirs
    def setup_dirs(self) -> None:
        """Step 1: создать профили из шаблонов (идемпотентно — только если нет config.yaml).

        ## @purpose — profile creation (init.sh Step 1).
        ## @io — ⇥ None → ⎋ None (side-effect: копирование шаблонов в data)
        ## @complexity O(P) — P = шаблонные профили
        """
        if not self.templates.is_dir():
            logger.warning("[IMP:7][INIT][TEMPLATES] No templates at %s — skipping profile creation", self.templates)
            return
        self.data.mkdir(parents=True, exist_ok=True)
        for template_dir in sorted(self.templates.iterdir()):
            if not template_dir.is_dir():
                continue
            dest = self.data / template_dir.name
            if (dest / "config.yaml").is_file():
                logger.info("[IMP:7][INIT][%s] SKIP: profile already exists — idempotent", template_dir.name)
                continue
            shutil.copytree(template_dir, dest)
            logger.info("[IMP:9][INIT][%s] Profile created from template", template_dir.name)

    # endregion FUNC_setup_dirs

    # region FUNC_check_config
    def check_config(self) -> None:
        """Step 2: context config overlay (rsync CONTEXT_DIR/config/ → hermes_config/).

        ## @purpose — context config overlay (init.sh Step 2).
        ## @io — ⇥ None → ⎋ None
        ## @raises — subprocess.CalledProcessError при провале rsync (fail-fast)
        """
        config_src = self.context_dir / "config"
        if not config_src.is_dir():
            return
        logger.info("[IMP:8][INIT][CONTEXT] Applying context config overlay from %s/", config_src)
        self.rsync(config_src, self.hermes_config)
        logger.info("[IMP:9][INIT][CONTEXT] Context config overlay applied")

    # endregion FUNC_check_config

    # region FUNC_init_state
    def init_state(self) -> None:
        """Step 3-4: context profile overlay (guard) + volume permissions + ownership.

        ## @purpose — финальное состояние: overlay профилей с guard-файлом, права на volume (init.sh Step 3-4).
        ## @io — ⇥ None → ⎋ None
        ## @invariants
        ##   - guard-файл существует → overlay пропускается (идемпотентность)
        ##   - overlay: rsync -a --ignore-existing (базовые профили приоритетны при re-init)
        ##   - chown -R uid:gid на data — non-fatal при провале (как shell || true)
        ##   - volume permissions: тест записи + chown если root — non-fatal (TRAP[BUG] 2026-07-06)
        """
        profiles_src = self.context_dir / "templates" / "profiles"
        if profiles_src.is_dir():
            if self.context_guard.is_file():
                logger.info(
                    "[IMP:7][INIT][CONTEXT] Context overlay already applied (guard: %s) — idempotent",
                    self.context_guard,
                )
            else:
                logger.info("[IMP:8][INIT][CONTEXT] Applying context profile overlay from %s/", profiles_src)
                self.data.mkdir(parents=True, exist_ok=True)
                self.rsync(profiles_src, self.data, ignore_existing=True)
                logger.info("[IMP:9][INIT][CONTEXT] Context profile overlay applied")
                self.context_guard.parent.mkdir(parents=True, exist_ok=True)
                self.context_guard.touch()
                logger.info("[IMP:8][INIT][CONTEXT] Guard file created: %s", self.context_guard)

        # ── Volume permissions (non-fatal; TRAP[BUG] 2026-07-06 P0) ──
        self._validate_volume_permissions()
        try:
            subprocess.run(["chown", "-R", f"{self.uid}:{self.gid}", str(self.data.parent)], check=False)
        except OSError as exc:
            logger.warning("[IMP:7][INIT][OWNERSHIP] chown failed (non-fatal): %s", exc)
        logger.info("[IMP:8][INIT][OWNERSHIP] %s ownership set to %d:%d", self.data.parent, self.uid, self.gid)

    # endregion FUNC_init_state

    # region FUNC__validate_volume_permissions
    def _validate_volume_permissions(self) -> None:
        """Тест записи /opt/data + chown fix если root (non-fatal, TRAP[BUG] 2026-07-06 P0).

        ## @purpose — volume-права: hermes user (UID 10000) не мог писать /opt/data на первом старте.
        ## @io — ⇥ None → ⎋ None (always, non-fatal)
        """
        data_root = self.data.parent
        data_root.mkdir(parents=True, exist_ok=True)
        test_file = data_root / ".write_test"
        try:
            test_file.touch(exist_ok=True)
            test_file.unlink(missing_ok=True)
            logger.info("[IMP:7][VOLUME][CHECK] Write access to %s confirmed", data_root)
        except OSError:
            logger.warning(
                "[IMP:9][VOLUME][CHECK] Cannot write to %s — volume permission mismatch (owner is not UID %d)",
                data_root,
                self.uid,
            )
            if os.geteuid() == 0:
                logger.info(
                    "[IMP:8][VOLUME][FIX] Running as root — attempting chown %d:%d %s", self.uid, self.gid, data_root
                )
                try:
                    subprocess.run(["chown", f"{self.uid}:{self.gid}", str(data_root)], check=True)
                    logger.info("[IMP:9][VOLUME][FIX] Volume ownership fixed for %s", data_root)
                except (OSError, subprocess.CalledProcessError):
                    logger.warning("[IMP:7][VOLUME][FIX] chown failed — continuing anyway (non-fatal)")
            else:
                logger.warning(
                    "[IMP:7][VOLUME][FIX] Not running as root — cannot fix ownership, continuing anyway (non-fatal)"
                )

    # endregion FUNC__validate_volume_permissions

    # region FUNC_run
    def run(self, context: str | None = None) -> int:
        """Оркестрация init (эквивалент init.sh main flow).

        ▶ ┌context┐ → ○ read CONTEXT → ○ setup_dirs → ○ check_config → ○ init_state → ⎋ 0

        ## @purpose — точка входа CLI (init.sh exec python3 init.py).
        ## @io — ⇥ context: str | None → ⎋ int (0 = ok, 1 = OSError)
        """
        context_value = self.read_s6_env("CONTEXT", current_env=context)
        if context_value:
            logger.info("[IMP:9][INIT][CONTEXT] Context specified: %s", context_value)
        else:
            logger.warning("[IMP:7][INIT][CONTEXT] No CONTEXT set — running base-only mode")

        logger.info("=== Platform init started ===")
        try:
            self.setup_dirs()
            self.check_config()
            self.init_state()
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.error("[IMP:10][INIT][FATAL] Init failed: %s", exc)
            return 1
        logger.info("[IMP:9][INIT][COMPLETE] Platform init finished")
        return 0

    # endregion FUNC_run


# endregion CLASS_HermesInit


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: `python3 init.py` — запуск HermesInit с контейнерными defaults.

    ## @purpose — интерфейс для тонкого wrapper init.sh (exec python3 /usr/local/bin/init.py).
    ## @io — ⇥ argv (не используется — paths контейнерные) → ⎋ int (0 = ok, 1 = провал)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return HermesInit().run()


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
