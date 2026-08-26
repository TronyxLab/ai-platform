# GREP_SUMMARY: dev-cert-timeouts subprocess-bounded introspection AI-0018 DEV_CERT_CMD_TIMEOUT
# STRUCTURE: ▶ AST-парс dev_cert_generator.py → ○ каждый Call subprocess.run → ◇ keyword timeout? → ⎋ 0 без timeout
# region MODULE_CONTRACT
## @purpose  AI-0018 (DevPlan 17 T3.5, $TEST_SPEC row): каждый subprocess.run в
##           dev_cert_generator несёт timeout (DEV_CERT_CMD_TIMEOUT) — интроспекция AST.
##           Специфицированный артефакт $TEST_SPEC.
## @scope    tests/unit: ast-интроспекция исходника; без subprocess/openssl.
## @invariants
##   - Ни одного subprocess.run без timeout в модуле (зависший openssl висел вечно)
# endregion MODULE_CONTRACT

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_GENERATOR = Path(__file__).resolve().parents[2] / "core" / "modules" / "nginx" / "dev_cert_generator.py"


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · все subprocess.run ограничены таймаутом (AI-0018)
# · Regression: 4 вызова openssl/mkcert без timeout — зависший процесс висел вечно
# · Scenario: AST-обход модуля: каждый subprocess.run имеет keyword timeout;
#   константа DEV_CERT_CMD_TIMEOUT определена локально (кросс-слой: modules↛internal)
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0018)
# · Remove if: генератор переезжает на run_subprocess-канон с централизованным бюджетом
def test_all_subprocess_bounded() -> None:
    src = _GENERATOR.read_text(encoding="utf-8")
    tree = ast.parse(src)
    unbounded = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "run"
        and "timeout" not in {kw.arg for kw in node.keywords}
    ]
    assert not unbounded, f"subprocess.run без timeout на строках: {unbounded}"
    assert "DEV_CERT_CMD_TIMEOUT" in src, "канон-константа обязана присутствовать"
    logger.critical("[IMP:9][test] every subprocess bounded by DEV_CERT_CMD_TIMEOUT — OK (AI-0018)")
