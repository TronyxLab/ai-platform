#!/usr/bin/env python3
# GREP_SUMMARY: age-decrypt-stdout restore drain-to-pipe age-cipher backup-cron F-19d 017
# STRUCTURE: ▶ ┌AGE_SECRET_KEY ┐ → ◇ файл .age? → ⚡ age -d -i <(env-key) → stdout → ⎋ rc {0,1}
# region MODULE_CONTRACT
## @purpose  Расшифровка age-файла В STDOUT (drain-to-pipe) для restore-канала postgres:
##           plaintext НИКОГДА не пишется на диск ноды (tmpfs-класс контракт DR;
##           mirrors decrypt_secrets.py tmpfs-инвариант). Потребитель — Makefile restore.
## @scope    Вызывается ТОЛЬКО из core/modules/postgres/Makefile restore-рецепта (.age ветка).
## @io       ⇥ argv[1] = путь к .age-файлу; env AGE_SECRET_KEY (identity) или AGE_IDENTITY_FILE
##           ⎋ stdout = расшифрованный поток; exit 0/1; stderr — только LDD-логи без содержимого
## @invariants
##   - Ключ НИКОГДА не в argv и не попадает в логи (env/файл + `set +x` по умолчанию)
##   - Identity передаётся age через temp-fd... упрощение канона: age поддерживает
##     AGE identities file; env-ключ (первая строка) пишется во временный ФАЙЛ 0600 на
##     /dev/shm (tmpfs), dd-wipe после чтения (тот же класс защиты, что decrypt_secrets.py)
##   - Никаких секретов в exceptions/logging; failure = IMP:10 + exit 1 без payload
## @rationale Makefile не умеет безопасно дешифровать .age → без этого шага ручной restore
##            из S3 артефактов падал «invalid command» (бинарник скормлен psql сырьём).
## @changes 2026-08-27 | DevPlan 017 F-19d — created (restore round-trip drill finding)
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger_name = "age-decrypt"


def _log(level: int, msg: str) -> None:
    print(f"[IMP:{level}][{logger_name}] {msg}", file=sys.stderr)  # ruff: ignore[T201] — CLI stdout/stderr контракт


def main(argv: list[str]) -> int:
    if len(argv) != 2:  # ruff: ignore[PLR2004] — usage-арность CLI
        _log(10, "usage: age_decrypt_stdout.py <file.age>")
        return 2
    src = Path(argv[1])
    if not src.is_file():
        _log(10, f"Input not found: {src.name}")
        return 1
    key_env = os.environ.get("AGE_SECRET_KEY", "")
    identity_file = os.environ.get("AGE_IDENTITY_FILE", "")
    tmp_path: str | None = None
    try:
        if not identity_file and key_env:
            # Identity на tmpfs 0600, dd-wipe после (секреты вне диска вне окна).
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="ageid-",
                suffix=".txt",
                delete=False,
            ) as tf:
                tf.write(key_env)
                if not key_env.endswith("\n"):
                    tf.write("\n")
                tmp_path = tf.name
            Path(tmp_path).chmod(0o600)
        elif identity_file:
            tmp_path = identity_file
        else:
            _log(10, "No AGE key source (AGE_SECRET_KEY env or AGE_IDENTITY_FILE)")
            return 1
        cmd = [
            "age",
            "-d",
            "-i",
            tmp_path,
            str(src),
        ]  # входной файл ПОЗИЦИОННО — без него age читает закрытый stdin (EOF-intro)
        out = subprocess.run(cmd, capture_output=True, check=False)
        if out.returncode != 0:
            err = out.stderr.decode(errors="replace")
            _log(10, f"age exited {out.returncode}: {err[:160]}")
            return 1
        sys.stdout.buffer.write(out.stdout)
    finally:
        if tmp_path and tmp_path != identity_file and Path(tmp_path).exists():
            with Path(tmp_path).open("rb") as fh:
                fh.read()
            Path(tmp_path).unlink()
            _log(8, "Identity temp wiped (dd-class)")
    _log(9, "Decrypted stream delivered to stdout")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
