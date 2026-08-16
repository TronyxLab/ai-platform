#!/usr/bin/env bash
# GREP_SUMMARY: hermes-agent s6 cont-init platform-init thin-wrapper python
# STRUCTURE: ▶ exec python3 /usr/local/bin/init.py → ⎋ pass-through exit
# region MODULE_CONTRACT
## @purpose  Тонкий wrapper (DevPlan 119 D5): бизнес-логика (профили/overlay/guard) — в init.py.
## @changes  2026-08-02 · D5 — 157→7 LOC wrapper (логика → init.py)
# endregion MODULE_CONTRACT
exec python3 /usr/local/bin/init.py "$@"
