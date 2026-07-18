#!/usr/bin/env python3
# GREP_SUMMARY: contract test — every .conf in nginx dev-config/ must contain a server block
# STRUCTURE: test_every_dev_config_has_server_block → glob *.conf → assert "server {" in each

import glob
import logging
import os

import pytest

from tests.conftest import ldd_trajectory

# region MODULE_CONTRACT
## @purpose  Инвариантный тест: каждый .conf в core/modules/nginx/dev-config/ содержит server-блок
## @scope    Проверяет архитектурный инвариант после C5.1 — все vhost-конфиги имеют server {
## @invariants
##   - Тест не создаёт Docker-ресурсы, не требует nginx на хосте
##   - Работает только с файловой системой (статический анализ)
##   - IMP:9 логирование для LDD-трассировки
## @rationale Предотвращает регресс: если кто-то удалит server-блок из конфига, тест упадёт
# endregion MODULE_CONTRACT

logger = logging.getLogger(__name__)

# Путь к директории dev-config относительно корня проекта
DEV_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "modules",
    "nginx",
    "dev-config",
)

# Файлы, которые не обязаны содержать server-блоки (main nginx.conf, шаблоны и т.д.)
# nginx.conf — основной конфиг (events/http block, без server)
# platform-http.conf — HTTP-шаблон для platform-default.conf, не содержит server {}
EXCLUDE_FROM_SERVER_CHECK: set[str] = {
    "nginx.conf",
    "platform-http.conf",
    "security-headers.conf",  # Wave 1: include snippet, no server block
}


@ldd_trajectory
def test_every_dev_config_has_server_block(caplog: pytest.LogCaptureFixture) -> None:
    """
    Инвариантный тест: каждый .conf в dev-config/ содержит директиву server {.
    """

    # ── Собираем все .conf файлы ──
    conf_files = sorted(glob.glob(os.path.join(DEV_CONFIG_DIR, "*.conf")))
    assert len(conf_files) > 0, f"[IMP:9] No .conf files found in {DEV_CONFIG_DIR}"

    logger.info("[IMP:7][nginx-config] Found %d .conf files in dev-config", len(conf_files))
    for f in conf_files:
        logger.info("[IMP:7][nginx-config]   %s", os.path.basename(f))

    # ── Фильтруем exclude ──
    target_files = [f for f in conf_files if os.path.basename(f) not in EXCLUDE_FROM_SERVER_CHECK]

    # ── Проверяем каждый файл ──
    missing_server: list[str] = []

    for filepath in target_files:
        basename = os.path.basename(filepath)
        with open(filepath, encoding="utf-8") as fh:
            content = fh.read()

        if "server {" in content:
            logger.info(
                "[IMP:8][nginx-config] %s — contains 'server {' block",
                basename,
            )
        else:
            logger.warning(
                "[IMP:9][nginx-config] %s — MISSING 'server {' block",
                basename,
            )
            missing_server.append(basename)

    # ── Итог ──
    if missing_server:
        msg = f"[IMP:9][nginx-config] FAIL: {len(missing_server)} file(s) missing 'server {{' block:\n" + "\n".join(
            f"  - {f}" for f in missing_server
        )
        logger.error(msg)
        pytest.fail(msg)

    logger.info(
        "[IMP:9][nginx-config] PASS: All %d dev-config files contain 'server {' block",
        len(target_files),
    )
