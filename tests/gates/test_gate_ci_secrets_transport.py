#!/usr/bin/env python3
# GREP_SUMMARY: gate ci-secrets-transport core-deploy stdin-prelude bash-s AGE_SECRET_KEY argv cmdline secrets-interpolation REF-0007 DevPlan-16-T1F R5-negative
# STRUCTURE: ▶ read core-deploy.yml → ◇ (a) secrets.* вне run:-строк remote-команд (только env:) →
#            ◇ (b) prelude/bash -s паттерн присутствует → ⊕ R5-негатив (export-интерполяция fixture → RED) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  CI-секрет-транспорт гейт (DevPlan 16 T1.F, P0-7): мастер-ключ AGE не попадает в
##           командную строку remote-shell. В core-deploy.yml `${{ secrets.AGE_SECRET_KEY }}`
##           разрешён ТОЛЬКО в step-env блоке; внутри `run:`-строк remote-команд секретов нет —
##           транспорт через stdin-prelude (`bash -s`, контракт REF-0007 байт-в-байт с
##           remote_executor._ssh_exec / build-ssh-cmd.sh ssh_exec_stdin).
## @scope    .github/workflows/core-deploy.yml. Детекторы чистые (text → violations);
##           R5-негатив — tmp_path probe с исходным export-интерполяционным паттерном.
## @invariants
##   - `${{ secrets.* }}` внутри run:-блока → RED (ключ в /proc/*/cmdline весь прогон node-update)
##   - Отсутствие `bash -s` + printf-prelude паттерна в шаге Node update → RED (регрессия канала)
##   - Комментарии (# …) в run:-блоках не сканируются (TRAP-доки легальны)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_WORKFLOW = ROOT / ".github" / "workflows" / "core-deploy.yml"


def _run_block_lines(text: str) -> list[str]:
    """Извлечь строки run:-блоков (без комментариев) для скана интерполяций.

    ## @purpose  Общий детектор: секции `run: |` до следующего ключа верхнего уровня шага.
    ## @io — ⇥ text workflow YAML → ⎋ list[str]
    """
    lines: list[str] = []
    in_run = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if re.match(r"^(\s*)run:\s*[|>-]?\s*$", line) or re.match(r"^\s*run:\s*\S", line):
            in_run = True
            continue
        if in_run:
            # Конец run-блока: ключ шага/воркфлоу на том же или меньшем отступе без продолжения
            if re.match(r"^(\s*)-?\s*[\w.-]+:", line) and "echo" not in stripped:
                in_run = False
                continue
            lines.append(line)
    return lines


def _secret_interpolations(text: str) -> list[str]:
    """Найти ${{ secrets.* }} credential-класса внутри run:-строк (вне комментариев).

    ## @purpose  P0-7 сканит СЕКРЕТ-ЗНАЧЕНИЯ (ключи/токены/пароли). Инфраструктурные
    ##            идентификаторы (VPS_HOST/VPS_USER) не являются секрет-значениями и
    ##            легитимно живут в ssh-argv — allowlist.
    ## 🧐 TRAP[DECISION] · 2026-08-25 · DevPlan 16 T1.F · credential-allowlist гейта ·
    ##            Rejected: буквальный запрет ВСЕХ secrets.* в run:-строках ·
    ##            Reason: VPS_HOST/VPS_USER присутствуют в каждом ssh-вызове workflow;
    ##            запрет потребовал бы переписать весь файл вне скоупа T1.F, не усиливая
    ##            безопасность (хост ≠ credential) ·
    ##            Rev: появление второго credential-имени вне allowlist → добавить в
    ##            детект по умолчанию (deny-by-default, allowlist расширяется явно).
    ## @io — ⇥ text → ⎋ list[str] нарушающих строк
    """
    allowlist = {"VPS_HOST", "VPS_USER"}
    leaks: list[str] = []
    for line in _run_block_lines(text):
        for match in re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}", line):
            if match.upper() not in allowlist:
                leaks.append(line)
                break
    return leaks


# region TEST_gate_ci_secrets_transport


@pytest.mark.gate
def test_core_deploy_no_secret_interpolation() -> None:
    """P0-7: секреты вне remote-cmdline; stdin-prelude канал присутствует."""
    assert _WORKFLOW.is_file(), f"{_WORKFLOW} отсутствует"
    text = _WORKFLOW.read_text(encoding="utf-8")

    # Шаг Node update обязан существовать
    assert "Node update on VPS" in text, "опорная точка шага деплоя"

    # (a) Ни одного credential-secrets.* внутри run:-строк (AGE_SECRET_KEY — только step-env)
    leaks = _secret_interpolations(text)
    assert not leaks, (
        "credential-secrets интерполируются в run:-строках — значение попадает в "
        f"/proc/*/cmdline remote-shell (P0-7): {leaks[:3]}"
    )
    # Секрет при этом объявлен в step-env (канал не потерян)
    assert re.search(r"^\s+AGE_SECRET_KEY:\s+\$\{\{ secrets\.AGE_SECRET_KEY \}\}", text, re.M), (
        "AGE_SECRET_KEY обязан читаться в step-env"
    )

    # (b) stdin-prelude паттерн: printf '%s\n' + bash -s
    assert "bash -s" in text, "транспорт bash -s обязателен (REF-0007)"
    assert "build_update_secret_prelude" in text, "prelude-билдер обязателен"
    assert re.search(r"printf\s+'%s\\n'.*PRELUDE", text), "prelude уходит первой строкой stdin"

    logger.info("[IMP:9][gate][ok] core-deploy.yml: 0 secret-интерполяций в run:, prelude/bash -s присутствует")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.F · откат к export-интерполяции → RED
# · Last fail: аудит 15 P0-7 — `export AGE_SECRET_KEY="${{ secrets… }}"` + конкатенация в
#   remote-строку держали ключ в cmdline remote-shell весь прогон node-update
# · Scenario: probe-fixture с ИСХОДНЫМ нарушающим паттерном детектируется сканером
#   (детектор живой: если перестанет ловить — тест падает первым)
# · Remove if: транспорт заменён на b64-v2 и детектор переписан синхронно
@pytest.mark.gate
def test_regression_export_interpolation_red(tmp_path: pathlib.Path) -> None:
    regression = (
        "      - name: Node update on VPS\n"
        "        run: |\n"
        '          export AGE_SECRET_KEY="${{ secrets.AGE_SECRET_KEY }}"\n'
        '          ssh opts user@host "cd /opt/platform && AGE_SECRET_KEY=\\"$AGE_SECRET_KEY\\" make node-update"\n'
    )
    leaks = _secret_interpolations(regression)
    assert any("export AGE_SECRET_KEY" in line for line in leaks), (
        "R5 FAIL: детектор пропустил исходный баг P0-7 (export-интерполяция)"
    )
    logger.info("[IMP:9][gate][r5-negative] export-интерполяция детектируется")


# endregion TEST_gate_ci_secrets_transport
