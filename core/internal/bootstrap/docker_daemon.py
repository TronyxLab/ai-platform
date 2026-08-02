#!/usr/bin/env python3
# GREP_SUMMARY: docker-daemon daemon.json live-restore merge json install-docker python-port
# STRUCTURE: ▶ merge_live_restore(daemon_json) → ○ load json → ○ set live-restore → ○ atomic write → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Docker daemon.json live-restore merge (Strangler-порт inline python3 из install-docker.sh).
## @scope    Вызывается install-docker.sh configure_daemon() при существующем /etc/docker/daemon.json.
## @invariants
##   - Не создаёт файл с нуля (создание — heredoc в install-docker.sh)
##   - live-restore: true всегда включён (не перезаписывает остальные ключи)
##   - Atomic write: tmp + os.replace (безопасно при прерывании)
##   - Exit 1 при не-JSON содержимом с диагностикой в stderr
## @rationale Языковая политика: inline python3 -c → отдельный модуль (Strangler Tier-1).
##            Устраняет TRAP[BUG]: f.write() после закрытия файла — ValueError на merge-пути.
## @changes 2026-07-31 | Создан (debt S-2)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import sys

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared.atomic_writer import atomic_write_json as _atomic_write_json

logger = logging.getLogger("docker_daemon")


def merge_live_restore(daemon_json: str) -> bool:
    """Merge live-restore: true into existing daemon.json (atomic write).

    ▶ ┌daemon_json path┐ → ○ json.load → ○ config['live-restore']=True → ○ atomic replace → ⎋ bool
    """
    try:
        with open(daemon_json, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("[IMP:10][docker_daemon] Cannot read %s: %s", daemon_json, e)
        return False

    if not isinstance(config, dict):
        logger.error("[IMP:10][docker_daemon] %s is not a JSON object", daemon_json)
        return False

    config["live-restore"] = True

    # Atomic write: shared atomic_writer canon (E5 — tempfile + fsync + os.replace)
    try:
        _atomic_write_json(daemon_json, config)
    except OSError as e:
        logger.error("[IMP:10][docker_daemon] Cannot write %s: %s", daemon_json, e)
        return False

    logger.info("[IMP:9][docker_daemon] live-restore: true merged into %s", daemon_json)
    return True


def main() -> int:
    """CLI: docker_daemon.py merge-live-restore <path>."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Docker daemon.json live-restore merge")
    parser.add_argument("daemon_json", help="Path to /etc/docker/daemon.json")
    args = parser.parse_args()
    return 0 if merge_live_restore(args.daemon_json) else 1


if __name__ == "__main__":
    sys.exit(main())
