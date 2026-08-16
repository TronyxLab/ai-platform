#!/usr/bin/env python3
# GREP_SUMMARY: app-config, AppConfig, from_env, composition-root, DI, import-time-env, config-dataclass, env-loader, W4a
# STRUCTURE: ▶ AppConfig dataclass (11 полей) → ○ AppConfig.from_env(environ=None) ┌os.environ|mapping┐ → ⊕ defaults (pure consts) → ⎋ AppConfig (frozen)
# region MODULE_CONTRACT
## @purpose  AppConfig — единая точка конфигурации приложений из env (DevPlan 160 W4a T4.1).
##           Устраняет import-time чтения env в production-модулях (project_scaffolder,
##           receive_flow, channels, deploy_orchestrator, sudoers_generator, runner_cli,
##           ssh_command_parser): вместо module-level `os.environ[...]` констант — composition
##           root в main()/CLI создаёт AppConfig (from_env) и прокидывает параметром/конструктором.
##           НИКАКОГО глобального синглтона — каждый composition root создаёт свой экземпляр.
## @scope    core/internal/shared/app_config.py — shared-модуль, потребляется deploy/,
##           scaffold/, loadtest/, bootstrap/. Только конфигурация: никакой бизнес-логики,
##           никаких побочных эффектов при импорте (проверяется тестом test_app_config).
## @invariants
##   - Импорт модуля НЕ читает os.environ (все env-доступы — внутри from_env, call-time)
##   - from_env(environ=None) — environ mapping для тестов; None = os.environ
##   - Дефолты — ЧИСТЫЕ константы модуля (никаких env-чтений на module level)
##   - int-поля парсятся из строк; нечисловое значение → ValueError (fail-fast, не маска)
##   - Dataclass frozen — иммутабельная конфигурация (никакой случайной мутации)
## @rationale DevPlan 160 W4a (AF-1): import-time env-чтения делают модули нететсируемыми
##           (monkeypatch.setenv до импорта) и скрывают конфигурацию. AppConfig собирает
##           фактический набор ключей 8 модулей (см. @modulemap) в одну dataclass + loader;
##           composition root создаёт его в main()/CLI. Параметр default=None + ленивый
##           fallback на from_env() сохраняет shell-фасады и существующие вызовы без правок.
## @changes  2026-08-13 · DevPlan 160 W4a — created (T4.1)
## @modulemap
##   AppConfig.projects_root          ← PROJECTS_BASE          (project_scaffolder)
##   AppConfig.platform_org           ← PLATFORM_ORG           (project_scaffolder)
##   AppConfig.platform_default_node  ← PLATFORM_DEFAULT_NODE  (project_scaffolder)
##   AppConfig.platform_domain        ← PLATFORM_DOMAIN        (project_scaffolder auto_domain/render)
##   AppConfig.ci_mode                ← CI_MODE                (project_scaffolder confirm)
##   AppConfig.max_payload_bytes      ← PLATFORM_MAX_PAYLOAD_BYTES (receive_flow)
##   AppConfig.deploy_timeout         ← PLATFORM_DEPLOY_TIMEOUT    (channels)
##   AppConfig.projects_base          ← PROJECTS_BASE          (deploy_orchestrator)
##   AppConfig.platform_root          ← PLATFORM_ROOT          (sudoers_generator sys.path bootstrap)
##   AppConfig.ssh_user               ← SSH_USER               (loadtest/runner_cli)
##   AppConfig.platform_remote_base   ← PLATFORM_REMOTE_BASE   (ssh_command_parser deploy.sh path)
## @usecases
##   - project_scaffolder.main(argv, config=None) → argparse defaults
##   - channels.DeliveryChannel.__init__(timeout=None) → config.deploy_timeout (lazy)
##   - deploy_orchestrator.DeployOrchestrator.__init__(projects_base=None) → config.projects_base
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Дефолты числовых/путевых полей — из shared-канонов (timeouts/deploy_paths): НИКАКИХ env-чтений.
from core.internal.shared.deploy_paths import DEFAULT_PLATFORM_BASE, DEFAULT_PROJECTS_BASE
from core.internal.shared.timeouts import DEPLOY_TIMEOUT

# ── Чистые дефолты (module-level НЕ читают env) ─────────────────────────────────

# ~/projects — родитель репозитория (тот же канон, что scaffold 5×parent от project_scaffolder.py)
_DEFAULT_PROJECTS_ROOT: str = str(Path(__file__).resolve().parents[4])
# Репозиторий ai-platform — платформенный root (4×parent от shared/app_config.py)
_DEFAULT_PLATFORM_ROOT: str = str(Path(__file__).resolve().parents[3])
_DEFAULT_ORG: str = "personal"
_DEFAULT_NODE: str = "tronyx-vps"
_DEFAULT_MAX_PAYLOAD_BYTES: int = 1024**3  # 1 GiB (receive_flow T9.9)
_DEFAULT_SSH_USER: str = "root"


@dataclass(frozen=True)
class AppConfig:
    """Конфигурация приложения из env — иммутабельный снимок (composition root → параметр).

    ## @purpose — Единая dataclass всех env-значений конфигурации приложения.
    ##            Инстанцируется в main()/CLI через
    ##            from_env() и прокидывается параметром/конструктором — без синглтона.
    ## @io — ⇥ from_env(environ) → ⎋ AppConfig (frozen dataclass, 11 полей)
    ## @complexity — O(1) — конструкция dataclass
    ## @invariants
    ##   - frozen: конфигурация неизменна после создания (никакой случайной мутации)
    ##   - Значения — РАЗРЕШЁННЫЕ env-строки или канонические дефолты (см. @modulemap)
    """

    projects_root: str
    platform_org: str
    platform_default_node: str
    platform_domain: str
    ci_mode: str
    max_payload_bytes: int
    deploy_timeout: int
    projects_base: str
    platform_root: str
    ssh_user: str
    platform_remote_base: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AppConfig:
        """Загрузить конфигурацию из env (или переданного mapping — для тестов).

        ▶ ┌environ┐ → ◇ None → os.environ → ⊕ по каждому ключу: value|default → ⎋ AppConfig

        ## @purpose — Единственный loader: читает os.environ (или mapping) в момент вызова.
        ##            Лениво и просто — без магии, без файлов, без кэширования.
        ## @io — ⇥ environ: Mapping[str, str] | None (None = os.environ) → ⎋ AppConfig
        ## @complexity — O(K) — K = число полей (11)
        ## @invariants
        ##   - Ключи: PROJECTS_BASE, PLATFORM_ORG, PLATFORM_DEFAULT_NODE, PLATFORM_DOMAIN,
        ##     CI_MODE, PLATFORM_MAX_PAYLOAD_BYTES, PLATFORM_DEPLOY_TIMEOUT, PROJECTS_BASE,
        ##     PLATFORM_ROOT, SSH_USER, PLATFORM_REMOTE_BASE
        ##   - int-поля: int(source.get(...)) — нечисловой env → ValueError (fail-fast)
        ##   - Дефолты — константы этого модуля (никаких env-чтений вне from_env)
        """
        source: Mapping[str, str] = os.environ if environ is None else environ
        return cls(
            projects_root=source.get("PROJECTS_BASE", _DEFAULT_PROJECTS_ROOT),
            platform_org=source.get("PLATFORM_ORG", _DEFAULT_ORG),
            platform_default_node=source.get("PLATFORM_DEFAULT_NODE", _DEFAULT_NODE),
            platform_domain=source.get("PLATFORM_DOMAIN", ""),
            ci_mode=source.get("CI_MODE", ""),
            max_payload_bytes=int(source.get("PLATFORM_MAX_PAYLOAD_BYTES", str(_DEFAULT_MAX_PAYLOAD_BYTES))),
            deploy_timeout=int(source.get("PLATFORM_DEPLOY_TIMEOUT", str(DEPLOY_TIMEOUT))),
            projects_base=source.get("PROJECTS_BASE", DEFAULT_PROJECTS_BASE),
            platform_root=source.get("PLATFORM_ROOT", _DEFAULT_PLATFORM_ROOT),
            ssh_user=source.get("SSH_USER", _DEFAULT_SSH_USER),
            platform_remote_base=source.get("PLATFORM_REMOTE_BASE", DEFAULT_PLATFORM_BASE),
        )
