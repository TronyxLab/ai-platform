#!/usr/bin/env python3
# GREP_SUMMARY: checklist-generator, scaffold, setup-checklist, github-repo, ci-secrets, md-writer
# STRUCTURE: ▶ generate_checklist ┌dry-run? → no-op┘ → ◇ domain/database-секции → ⊕ _SETUP_CHECKLIST.md write → ⎋ True
# region MODULE_CONTRACT
## @purpose  Генератор _SETUP_CHECKLIST.md для new-project (T3.7 god-file trim) — точные
##           GitHub/psql/nginx команды для ручных шагов оператора после scaffold.
## @scope    Единственный потребитель — project_scaffolder.main (step 8). Публичное API:
##           generate_checklist(project_dir, name, org, template, domain, database, dry_run).
## @invariants
##   - dry_run=True → no-op с [DRY-RUN] логом, файл НЕ пишется
##   - Содержимое секций — контракт onboarding'а (CI secrets-таблица = матрица ключей
##     core/AGENTS.md §Ротация SSH/CI-ключей); менять только осознанно
##   - template-параметр зарезервирован (сейчас не влияет на вывод — ARG001)
## @rationale God-file trim: чеклист-генератор — самостоятельная единица вывода, не логика
##            оркестрации scaffold; вынос снижает project_scaffolder до координатора.
## @changes  2026-08-22 · T3.7 — извлечён из project_scaffolder.py verbatim
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# region FUNC_generate_checklist
def generate_checklist(
    project_dir: str,
    name: str,
    org: str,
    template: str,  # ruff: ignore[ARG001]
    domain: str = "",
    database: str = "",
    dry_run: bool = False,
) -> bool:
    """Generate _SETUP_CHECKLIST.md with exact GitHub commands.

    ## @purpose  Mirror of generate_checklist() from add-project.sh:511-587.
    ## @io        ⇥ project_dir, name, org, ... → ⎋ bool
    """
    if dry_run:
        logger.info("[IMP:7][scaffold][cl] [DRY-RUN] Would generate: %s/_SETUP_CHECKLIST.md", project_dir)
        return True

    logger.info("[IMP:7][scaffold][cl] Generating setup checklist")

    checklist_path = Path(project_dir) / "_SETUP_CHECKLIST.md"

    lines: list[str] = [
        f"# Setup Checklist: {name}",
        "",
        "> ⚠️ Выполните шаги по порядку. Команды можно копировать и вставлять.",
        "",
        "## 1. Создать репозиторий на GitHub",
        "",
        "```bash",
        f'gh repo create {org}/{name} --private --description "{name} project"',
        "```",
        "",
        "## 2. Добавить remote и запушить",
        "",
        "```bash",
        f"cd {project_dir}",
        f"git remote add origin git@github.com:{org}/{name}.git",
        "git push -u origin main",
        "```",
        "",
        "## 3. CI/CD secrets (org-level — NODE_HOST_MAP, CI_DEPLOY_KEY)",
        "",
        "| Secret | Назначение |",
        "|--------|-----------|",
        "| `CI_DEPLOY_KEY` | SSH private key для ci-deploy forced-command deploy |",
        "| `MIRROR_SSH_KEY` | SSH private key для mirror push (Tronyx161 → TronyxLab; 177 W2.1 — GIT_MIRROR_TOKEN удалён) |",
        "",
        "Org variable `NODE_HOST_MAP` (JSON) — разрешение нод в SSH-хосты.",
        "",
        "## 4. Настроить Docker Registry",
        "",
        "Registry `ghcr.io` уже прописан в `docker-compose.yml`.",
        "GitHub Actions использует `GITHUB_TOKEN` (доступен автоматически).",
    ]

    if domain:
        lines.extend([
            "",
            "## 5. TLS-сертификат выпускается автоматически",
            "",
            "## 6. Применить nginx overlay на сервере",
            "",
            "```bash",
            "sudo nginx -t && sudo nginx -s reload",
            "```",
        ])

    if database:
        lines.extend([
            "",
            "## 7. Создать базу данных",
            "",
            "```bash",
            f'sudo -u postgres psql -c "CREATE DATABASE {database};"',
            "```",
        ])

    lines.extend([
        "",
        "---",
        f"> Сгенерировано `add-project.sh` ({datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})",
    ])

    checklist_path.write_text("\n".join(lines) + "\n")
    logger.info("[IMP:7][scaffold][cl] Setup checklist generated: %s", checklist_path)
    return True


# endregion FUNC_generate_checklist
