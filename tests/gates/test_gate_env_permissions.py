# GREP_SUMMARY: gate env-permissions .env 0600 world-readable H2 R5-negative security
# STRUCTURE: ┌_scan_env_permissions(root)┐ → ◇ .env существует? → ◇ mode & 0o077 != 0 → ⊕ violations → RED ‖ ▶ R5-negative (tmp .env 0644 → flagged) → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate (security hardening H2): локальный `.env` с реальными секретами обязан быть
##           0600 (owner rw, без group/other бит). Файл gitignored (не доставляется в CI) —
##           гейт проверяет права ТОЛЬКО при наличии файла в корне репо.
## @scope    Корень репо (`.env`). CI fresh checkout не имеет `.env` → гейт no-op.
## @invariants
##   - Файл `.env` отсутствует → PASS (no-op; гейт не падает в CI)
##   - Файл есть и mode & 0o077 == 0 (только owner rw) → PASS
##   - Файл есть и mode & 0o077 != 0 (group/other чтение) → RED
##   - R5-negative: tmp .env с 0644 детектируется
## @rationale  Аудит 2026-08-15 (H2): `.env` декларирует 600, фактически 644 — реальные
##             ключи world-readable на dev-машине. Гейт ловит возврат класса дефекта.
## @changes  2026-08-16 · Created (security hardening H2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import pathlib

import pytest

# Биты group/other доступа (rwx) — владельцу разрешены rw (0600), всё прочее запрещено.
_PERM_MASK = 0o077


# region FUNC_scan_env_permissions
## @purpose  Проверить права `.env` в корне репо (только при наличии файла).
## @io       ⇥ root: Path → ⎋ list[str] — нарушения (пусто = PASS)
## @complexity O(1) — single stat
def _scan_env_permissions(root: pathlib.Path) -> list[str]:
    """Return violations for `.env` at root: non-0600 (group/other bits set)."""
    env_path = root / ".env"
    if not env_path.is_file():
        return []
    mode = env_path.stat().st_mode
    if mode & _PERM_MASK:
        return [f"{env_path.name} mode 0o{mode & 0o777:o} — must be 0o600 (group/other bits set)"]
    return []


# endregion FUNC_scan_env_permissions


@pytest.mark.gate
def test_env_file_is_0600_when_present() -> None:
    """RED: локальный `.env` с group/other-битами (не 0600) — реальные ключи world-readable."""
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    violations = _scan_env_permissions(root)
    if violations:
        pytest.fail(
            "GATE_ENV_PERMISSIONS: .env не 0600 — реальные секреты читаемы group/other. "
            "Исправь: chmod 600 .env\n" + "\n".join(f"  • {v}" for v in violations)
        )


# 🧪 TRAP[TEST] · R5-negative · H2 · 0644 .env детектируется
# · Scenario: аудит 2026-08-15 — .env фактически 644 (декларировал 600).
# · Original form: реальный .env 644 с ключами на dev-машине.
@pytest.mark.gate
def test_env_permissions_negative_0644_detected(tmp_path: pathlib.Path) -> None:
    """R5-negative: .env с 0644 детектируется как нарушение."""
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=test\n", encoding="utf-8")
    env.chmod(0o644)

    violations = _scan_env_permissions(tmp_path)

    assert len(violations) == 1, f"Expected 1 violation for 0644 .env, got {violations}"


# 🧪 TRAP[TEST] · R5-negative · H2 · 0600 .env НЕ flagged
@pytest.mark.gate
def test_env_permissions_negative_0600_ok(tmp_path: pathlib.Path) -> None:
    """R5-negative: .env с 0600 не считается нарушением."""
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=test\n", encoding="utf-8")
    env.chmod(0o600)

    violations = _scan_env_permissions(tmp_path)

    assert violations == [], f"Expected no violations for 0600 .env, got {violations}"
