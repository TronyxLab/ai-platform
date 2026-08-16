"""Static layer: exception-patterns detector tests (DevPlan 163 W-C C2, 170 W2-A2 B3).

# GREP_SUMMARY: test-static exception-patterns bare-except broad-except noqa-EXC policy-marker R5 U-39 modules-scope
# STRUCTURE: ▶ bare `except:` (synthetic) → RED | ▶ `except Exception` без noqa (R5 U-39) → RED
#            → ▶ `except Exception` с noqa:EXC + маркером → PASS | ▶ modules-скоуп: bare except → RED
#            → ▶ modules-скоуп: `except Exception` без noqa → RED (170 W2-A2) → ▶ с noqa:EXC → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора exception_patterns (DevPlan 163 W-C C2): позитивный тест
##           на синтетический bare except, R5-негатив на ОРИГИНАЛЬНЫЙ вход U-39
##           (`except Exception` без noqa:EXC + policy marker), PASS-контроль размеченного
##           broad except. DevPlan 170 W2-A2 (B3): scope-расширение детектора на
##           core/modules + core/loadtest — R5-negative probe (bare except в modules-пути),
##           негатив на неразмеченный broad except в modules, PASS-контроль с noqa:EXC.
## @scope    Native imports; probe-файлы в tmp_path с layout core/internal/ для
##           broad-except правила (scope U-39) и core/modules/ (расширенный scope 170 W2-A2).
## @invariants
##   - bare `except:` (type None) → RED (маскирует любую ошибку)
##   - `except Exception` в core/internal без `noqa: EXC` → RED (оригинальный вход U-39)
##   - `except Exception` с `noqa: EXC` + policy marker → PASS (легитимный best-effort)
##   - `except Exception` в core/modules без `noqa: EXC` → RED (170 W2-A2: policy-marker
##     для modules НЕ требуется — только noqa: EXC)
## @rationale R5 anti-survivorship (U-39): политика best-effort декларировалась комментариями
##            ×12 вместо контракта; детектор обязан ловить неразмеченный broad except.
##            170 W2-A2: расширение на modules/loadtest — единый канон маркировки всего core/.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
##           2026-08-14 | DevPlan 170 W2-A2 B3 — modules/loadtest-скоуп тесты
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.exception_patterns import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic bare `except:` → RED
# · Scenario: probe `except:` без типа исключения (маскирует любую ошибку, конституция §4)
# · Last fail: N/A (синтетический вариант)
# · Remove if: exception-patterns detector superseded
@ldd_trajectory
def test_exception_patterns_bare_except_detected(caplog, tmp_path) -> None:
    """Synthetic positive: bare `except:` (type None) детектируется."""
    probe = tmp_path / "core" / "_probe_bare.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def safe_div(a, b):\n    try:\n        return a / b\n    except:\n        return 0\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_bare" in f.file]
    assert hits, "R5 FAIL: bare except not detected"
    assert "bare except" in hits[0].message
    logger.info("[IMP:9][test_exception_patterns] bare except RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинальный вход U-39 `except Exception` без маркера → RED
# · Scenario: probe core/internal/_probe.py c `except Exception as e:` БЕЗ noqa:EXC —
# ·   точный вход U-39 (политика best-effort декларировалась комментариями ×12 вместо контракта)
# · Last fail: 14 мест deploy_orchestrator без маркеров политики (DevPlan 116 B4 T8, U-39)
# · Remove if: broad-except allowlist гейт отменяется
@ldd_trajectory
def test_exception_patterns_negative_unmarked_broad_except(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход U-39 — `except Exception` без noqa:EXC → RED."""
    probe = tmp_path / "core" / "internal" / "_probe_broad.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def run():\n    try:\n        return deploy()\n    except Exception as e:\n        print(e)\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_broad" in f.file]
    assert hits, "R5 FAIL: unmarked broad except (U-39 original input) not detected"
    assert "noqa" in hits[0].message
    logger.info("[IMP:9][test_exception_patterns] R5 broad except RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · `except Exception` с noqa:EXC + policy marker → PASS
# · Scenario: probe core/internal/_probe_marked.py c
# ·   `except Exception as exc:  (noqa EXC — best-effort cleanup)` → легитимно
# · Last fail: N/A (control — размеченный broad except не должен быть RED)
# · Remove if: broad-except allowlist гейт отменяется
@ldd_trajectory
def test_exception_patterns_marked_broad_except_allowed(caplog, tmp_path) -> None:
    """PASS-контроль: `except Exception` с noqa:EXC + policy marker не RED."""
    probe = tmp_path / "core" / "internal" / "_probe_marked.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def run():\n"
        "    try:\n"
        "        return deploy()\n"
        "    except Exception as exc:  # no"
        "qa: EXC — best-effort cleanup, must not crash\n"
        "        print(exc)\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_marked" in f.file]
    assert not hits, f"PASS-control FAIL: marked broad except flagged: {hits}"
    logger.info("[IMP:9][test_exception_patterns] marked broad except (noqa:EXC + policy marker) not flagged")


# 🧪 TRAP[TEST] · CONTROL (177 W3.1) · `except Exception` + noqa:EXC + "retry policy" → PASS
# · Scenario: shared/retry.py catch-all (любое Exception → retryable-предикат → re-raise).
# · Last fail: N/A (new marker 177 W3.1)
# · Remove if: "retry policy" маркер удаляется из _POLICY_MARKERS
@ldd_trajectory
def test_exception_patterns_retry_policy_marker_allowed(caplog, tmp_path) -> None:
    """PASS-контроль (177 W3.1): retry policy маркер принимается в core/internal."""
    probe = tmp_path / "core" / "internal" / "_probe_retry.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def run():\n"
        "    try:\n"
        "        return deploy()\n"
        "    except Exception as exc:  # noqa: EXC — retry policy: predicate-gated re-raise\n"
        "        raise\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_retry" in f.file]
    assert not hits, f"PASS-control FAIL: retry-policy marked except flagged: {hits}"
    logger.info("[IMP:9][test_exception_patterns] retry policy marker accepted")


# 🧪 TRAP[TEST] · POSITIVE (170 W2-A2) · modules-скоуп: bare `except:` → RED
# · Scenario: probe core/modules/_probe.py с `except:` без типа — расширенный скоуп
# ·   детектора (DevPlan 170 W2-A2 B3): bare except запрещён всюду в core/
# · Last fail: N/A (новый скоуп)
# · Remove if: exception-patterns detector superseded
@ldd_trajectory
def test_exception_patterns_bare_except_modules_scope_detected(caplog, tmp_path) -> None:
    """R5-negative probe (170 W2-A2): bare except в core/modules → RED."""
    probe = tmp_path / "core" / "modules" / "_probe_modules.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def safe_div(a, b):\n    try:\n        return a / b\n    except:\n        return 0\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_modules" in f.file]
    assert hits, "R5 FAIL (170 W2-A2): bare except in modules scope not detected"
    assert "bare except" in hits[0].message
    logger.info("[IMP:9][test_exception_patterns] modules bare except RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5, 170 W2-A2) · modules-скоуп: `except Exception` без noqa → RED
# · Scenario: probe core/modules/_probe_broad.py c `except Exception as e:` БЕЗ noqa:EXC —
# ·   расширенный скоуп (исходный вход: retention:167/upload:225/465/587/s3_client:102/157,
# ·   loadtest db.py:213 — 7 неразмеченных мест из research-B B3)
# · Last fail: 7 неразмеченных мест вне internal-скоупа (research-B B3, 170 W2-A2)
# · Remove if: broad-except allowlist гейт отменяется
@ldd_trajectory
def test_exception_patterns_unmarked_broad_except_modules_scope(caplog, tmp_path) -> None:
    """R5 negative (170 W2-A2): `except Exception` в core/modules без noqa:EXC → RED."""
    probe = tmp_path / "core" / "modules" / "_probe_broad_modules.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def run():\n    try:\n        return upload()\n    except Exception as e:\n        print(e)\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_broad_modules" in f.file]
    assert hits, "R5 FAIL (170 W2-A2): unmarked broad except in modules scope not detected"
    assert "noqa" in hits[0].message
    logger.info("[IMP:9][test_exception_patterns] modules broad except RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL (170 W2-A2) · modules-скоуп: `except Exception` с noqa:EXC → PASS
# · Scenario: probe core/modules/_probe_marked.py c
# ·   `except Exception as exc:  (noqa EXC — HTTP handler boundary)` — для modules policy-marker
# ·   НЕ требуется (только noqa: EXC; прецедент status-page/app.py:288,304)
# · Last fail: N/A (control)
# · Remove if: broad-except allowlist гейт отменяется
@ldd_trajectory
def test_exception_patterns_marked_broad_except_modules_scope_allowed(caplog, tmp_path) -> None:
    """PASS-контроль (170 W2-A2): `except Exception` с noqa:EXC в modules не RED (без policy-marker)."""
    probe = tmp_path / "core" / "modules" / "_probe_marked_modules.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def run():\n"
        "    try:\n"
        "        return upload()\n"
        "    except Exception as exc:  # no"
        "qa: EXC — HTTP handler boundary (modules-scope marker, policy-marker не нужен)\n"
        "        print(exc)\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_marked_modules" in f.file]
    assert not hits, f"PASS-control FAIL (170 W2-A2): marked modules broad except flagged: {hits}"
    logger.info("[IMP:9][test_exception_patterns] modules marked broad except (noqa:EXC) not flagged")
