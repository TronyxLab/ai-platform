"""Unit-тесты security-префикса T2.0c: pg_hba scram-sha-256 для RFC1918."""
# GREP_SUMMARY: test_pg_hba_scram pg_hba md5 scram-sha-256 rfc1918 password_encryption security-prefix
# STRUCTURE: ▶ read configs → ◇ assert no md5 on RFC1918 host lines → ⊕ scram present → ∑ password_encryption → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 010 T2.0c: pg_hba не содержит md5 для RFC1918-диапазонов; scram-sha-256
##           активирован (security-префикс Волны 2 — до публикации pgbouncer 6432 пирам)
## @scope    core/modules/postgres/config/{pg_hba.conf,postgresql.conf}
## @invariants
##   - Ни одна host-строка для RFC1918 не использует md5
##   - scram-sha-256 присутствует для всех трёх RFC1918-диапазонов
##   - postgresql.conf фиксирует password_encryption = scram-sha-256
##   - pgbouncer AUTH_TYPE=scram-sha-256 (совместимость auth_query)
## @rationale Test honesty R1: содержательные assert'ы на фактические файлы конфигурации;
##           LDD-телеметрия через печать проверенных строк (IMP-эквивалент траектории).
# endregion MODULE_CONTRACT

from pathlib import Path

POSTGRES_CONFIG = Path(__file__).resolve().parents[2] / "core" / "modules" / "postgres" / "config"
RFC1918 = ("172.16.0.0/12", "10.0.0.0/8", "192.168.0.0/16")


# region FUNC_test_no_md5_for_rfc1918
def test_no_md5_for_rfc1918() -> None:
    """Ни одна host-строка RFC1918 в pg_hba.conf не использует md5."""
    lines = (POSTGRES_CONFIG / "pg_hba.conf").read_text(encoding="utf-8").splitlines()
    print("--- LDD TRAJECTORY (pg_hba RFC1918 lines) ---")
    md5_hits: list[str] = []
    rfc_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith("host"):
            continue
        if any(cidr in stripped for cidr in RFC1918):
            rfc_lines.append(stripped)
            print(stripped)
            if "md5" in stripped.split():
                md5_hits.append(stripped)
    print("--- END LDD TRAJECTORY ---")
    # [IMP:9] бизнес-инвариант: все 3 RFC1918-диапазона покрыты и без md5
    assert len(rfc_lines) == 3, f"ожидались 3 RFC1918 host-строки, найдено {len(rfc_lines)}"
    assert not md5_hits, f"md5 найден для RFC1918 (запрещён T2.0c): {md5_hits}"
    assert all("scram-sha-256" in line for line in rfc_lines), "scram-sha-256 отсутствует для RFC1918"


# endregion FUNC_test_no_md5_for_rfc1918


# region FUNC_test_password_encryption_scram
def test_password_encryption_scram() -> None:
    """postgresql.conf явно фиксирует password_encryption = scram-sha-256."""
    conf = (POSTGRES_CONFIG / "postgresql.conf").read_text(encoding="utf-8")
    setting = [ln for ln in conf.splitlines() if ln.strip().startswith("password_encryption")]
    print("--- LDD TRAJECTORY ---")
    print("\n".join(setting))
    print("--- END LDD TRAJECTORY ---")
    # [IMP:9] бизнес-инвариант: новые роли хешируются SCRAM
    assert setting, "password_encryption не задан в postgresql.conf"
    assert "scram-sha-256" in setting[0]


# endregion FUNC_test_password_encryption_scram


# region FUNC_test_pgbouncer_auth_type_scram
def test_pgbouncer_auth_type_scram() -> None:
    """pgbouncer AUTH_TYPE=scram-sha-256 — auth_query совместим с scram-hba."""
    compose = (
        Path(__file__).resolve().parents[2] / "core" / "modules" / "postgres" / "docker-compose.base.yml"
    ).read_text(encoding="utf-8")
    # [IMP:9] бизнес-инвариант: wildcard auth-делегация в режиме scram
    assert "AUTH_TYPE: scram-sha-256" in compose, "pgbouncer AUTH_TYPE не scram-sha-256"


# endregion FUNC_test_pgbouncer_auth_type_scram
