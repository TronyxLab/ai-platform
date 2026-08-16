#!/usr/bin/env bash
# GREP_SUMMARY: install-acme facade acme.sh thin-wrapper python3-m exec exit-code
# STRUCTURE: ▶ ┌ACME_HOME env┐ → export PYTHONPATH (repo-root) → exec python3 -m core.internal.bootstrap.install_acme "$@" → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  Тонкий shell-фасад (<100 LOC) над core/internal/bootstrap/install_acme.py (DevPlan 164 W3.5-1).
##           Вся бизнес-логика (apt-get git, git clone + merge-fallback, dnsapi_ext) — в Python-модуле.
## @scope    Вызывается lifecycle/phases/certs.py::_install_acme (subprocess ["bash", install-acme.sh])
##           и тестами (test_nginx_acme.py D4 merge-fallback). Имя/путь/аргументы сохранены (прямое
##           замещение); exit-код Python-модуля пробрасывается через exec.
## @invariants
##   - PYTHONPATH экспортируется (repo-root) — канон issue-cert.sh:43: `python3 -m core.internal.*`
##     из фасада обязан сам устанавливать PYTHONPATH (add-vhost.sh:33 паттерн)
##   - exec — процесс заменяется (нет двойного слоя); exit-код = exit-код модуля
##   - Аргументы "$@" пробрасываются без изменений (модуль их игнорирует — env-контракт ACME_HOME)
## @rationale Языковая политика: фасады — тонкие обёртки над Python-модулями (паттерн
##            install-tor-proxy.sh / node-resolver.sh). Ключевой принцип прямого замещения:
##            фасад сохраняет имя/путь/аргументы — меняется только содержимое.
## @changes  2026-08-14 | DevPlan 164 W3.5-1 — 93 LOC shell → фасад (логика в install_acme.py)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/../../..:${PYTHONPATH:-}"

exec python3 -m core.internal.bootstrap.install_acme "$@"
