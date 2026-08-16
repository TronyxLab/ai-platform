#!/usr/bin/env python3
# GREP_SUMMARY: pre-push branch-detect stdin remote-ref refs/heads PRE_COMMIT_REMOTE_BRANCH git-fallback deleted-branch hybrid-hook
# STRUCTURE: ▶ stdin pre-push lines → ○ parse (remote ref refs/heads/<branch>) → ◇ найдено? → ⎋ branch │ ⊕ PRE_COMMIT_REMOTE_BRANCH env → ◇ → ⎋ │ ⊕ git rev-parse HEAD → ⎋ "unknown"
# NOTE doxygen: '<branch>' в комментариях-строках выше НЕ парсится (комментарий, не docstring); в docstring ниже — backticks.
# region MODULE_CONTRACT
## @purpose  Branch-detection для hybrid pre-push hook (DevPlan 160 W6 T6.2 D4): извлекает целевую
##           ветку push'а из stdin git pre-push (`<local ref> <local sha> <remote ref> <remote sha>`),
##           с fallback на env PRE_COMMIT_REMOTE_BRANCH (pre-commit 4.x) и git HEAD. Вынесен из
##           pre-push-gate.sh:81-100 (DevPlan 170 W9-F2, research-A §9) — чистая функция + CLI.
## @scope    core/internal/lint/pre_push_branch_detect.py — вызывается pre-push-gate.sh (script-path
##           self-bootstrap) или `python3 -m core.internal.lint.pre_push_branch_detect`.
##           Вывод: одна строка branch в stdout (exit 0).
## @invariants
##   - Формат строки stdin: `<local ref> <local sha> <remote ref> <remote sha>`; берётся ТРЕТИЙ токен
##     (remote ref); интересует refs/heads/‹branch› (игнорируются refs/tags/*, refs/remotes/*)
##   - deleted-branch push (`(delete)` local ref, zero sha) — remote ref ВСЁ ЕЩЁ refs/heads/<b> →
##     ветка детектится (deletion main/release гейтится, как было в shell)
##   - Пусто → env PRE_COMMIT_REMOTE_BRANCH → git rev-parse --abbrev-ref HEAD → "unknown" (последний
##     дефолт; "unknown" ≠ main/release → feature quick-check — shell-семантика сохранена)
##   - `|| [[ -n "$_line" ]]` TRAP[BUG] (2026-08-13, bash read при EOF без \n) НЕ воспроизводится:
##     Python str.splitlines() обрабатывает финальную частичную строку корректно — закрыт @changes
##   - source lib/paths.sh TRAP[BUG] (2026-08-06, PLATFORM_ROOT export) неприменим: чистый Python,
##     0 shell-библиотек — закрыт @changes
## @rationale Strangler-Fig: pre-push-gate.sh:81-100 (branch-detect + 2 TRAP[BUG]) → тестируемая
##            чистая функция detect_branch() с unit-тестами (main/feature/release/deleted-branch,
##            R5-negative: пустой stdin → корректный дефолт).
## @changes 2026-08-15 | DevPlan 170 W9-F2 — извлечён из pre-push-gate.sh:81-100; закрыты
##           TRAP[BUG] 2026-08-13 (while read no-\n) и 2026-08-06 (paths.sh PLATFORM_ROOT)
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence

# ── self-bootstrap (script-path канон): hook-окружение может не иметь PYTHONPATH — корень
# вставляется явно (идемпотентно; python3 -m / pytest — no-op).
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

_PRE_COMMIT_REMOTE_BRANCH_ENV = "PRE_COMMIT_REMOTE_BRANCH"
_REF_PREFIX = "refs/heads/"
_DEFAULT_BRANCH = "unknown"
_GIT_TIMEOUT_SECONDS = 10
# Минимум токенов в строке pre-push stdin: <local ref> <local sha> <remote ref> <remote sha>
_MIN_TOKENS = 3


# region FUNC_detect_branch
## @purpose  Извлечь целевую ветку из строк stdin pre-push (семантика 1:1 с shell-блоком
##           pre-push-gate.sh:82-92). Формат строки: `<local ref> <local sha> <remote ref> <remote sha>`.
## @io       ⇥ stdin_lines: Sequence[str] (сырые строки stdin, с/без завершающего \n)
##           → ⎋ branch: str ("" = не найдена)
## @complexity  O(n) — один проход строк; парсинг третьего токена
## @invariants  Пустые строки игнорируются; строки <3 токенов игнорируются;
##              первый refs/heads/‹branch› wins (break-семантика shell);
##              deleted-branch (local ref "(delete)") детектится по remote ref (строки верны)
## @rationale  Чистая функция — unit-тесты без subprocess (main/feature/release/deleted-branch).
def detect_branch(stdin_lines: Sequence[str]) -> str:
    """Detect target branch from pre-push stdin lines. Returns "" if not found."""
    for raw in stdin_lines:
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < _MIN_TOKENS:
            # shell: _remote_ref="${_rest%% *}" — после дропа 2 токенов <3 токена → пусто
            continue
        remote_ref = parts[2]
        if remote_ref.startswith(_REF_PREFIX):
            return remote_ref[len(_REF_PREFIX) :]
    return ""


# endregion FUNC_detect_branch


# region FUNC__git_head_branch
## @purpose  git rev-parse --abbrev-ref HEAD fallback (shell pre-push-gate.sh:99).
## @io       ⇥ нет → ⎋ branch: str (HEAD-ветка или "unknown" при ошибке)
## @complexity  O(1) — один git-вызов
## @invariants  Любая ошибка (git отсутствует, не репозиторий, timeout) → "unknown" (shell `|| echo unknown`)
def _git_head_branch() -> str:
    """Resolve current HEAD branch via git. Returns 'unknown' on any failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return _DEFAULT_BRANCH
    branch = result.stdout.strip()
    return branch or _DEFAULT_BRANCH


# endregion FUNC__git_head_branch


# region FUNC__print_usage
## @purpose  Печать usage (--help) — тот же текст, что pre-push-gate.sh --help (сокращённо).
## @io       ⇥ нет → ⎋ None (stdout)
## @complexity  O(1)
def _print_usage() -> None:
    """Print CLI usage for --help (mirror pre-push-gate.sh --help)."""
    sys.stdout.write(
        "Usage: pre_push_branch_detect.py [--help]\n\n"
        "Detect target branch from git pre-push stdin (DevPlan 170 W9-F2):\n"
        "  stdin:  <local ref> <local sha> <remote ref> <remote sha> per line\n"
        "  order:  stdin (remote ref refs/heads/*) → PRE_COMMIT_REMOTE_BRANCH env (pre-commit 4.x)\n"
        "          → git rev-parse --abbrev-ref HEAD → 'unknown'\n"
        "  output: single branch name to stdout, exit 0\n"
    )


# endregion FUNC__print_usage


# region FUNC_main
## @purpose  CLI entrypoint: stdin → detect_branch → env fallback → git fallback → stdout branch.
## @io       ⇥ argv: Sequence[str] | None, stdin_lines: Sequence[str] | None (DI),
##              environ: Mapping[str, str] | None (DI), git_fn: Callable[[], str] | None (DI)
##           → ⎋ int exit code (0)
## @complexity  O(n) — чтение stdin + detect
## @invariants  DI-швы (stdin_lines/environ/git_fn) — тесты без subprocess/env-патчей;
##              prod-deфолты: sys.stdin / os.environ / _git_head_branch — поведение не меняется
def main(
    argv: Sequence[str] | None = None,
    *,
    stdin_lines: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    git_fn: Callable[[], str] | None = None,
) -> int:
    """Detect branch and print it. Returns 0 — sys.exit handled by __main__."""
    if argv is None:
        argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        _print_usage()
        return 0

    env = os.environ if environ is None else environ
    lines = sys.stdin.read().splitlines() if stdin_lines is None else list(stdin_lines)
    branch = detect_branch(lines)

    # pre-commit 4.x: env-подсказка при pre-push hook (fallback, если stdin недоступен)
    if not branch:
        branch = env.get(_PRE_COMMIT_REMOTE_BRANCH_ENV, "")
    if not branch:
        branch = (git_fn or _git_head_branch)()
    logger.info("[IMP:7][pre_push_branch_detect][branch] Target branch: %s", branch)
    sys.stdout.write(branch + "\n")
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
