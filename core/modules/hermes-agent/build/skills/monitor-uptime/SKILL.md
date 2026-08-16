# GREP_SUMMARY: SKILL monitor-uptime platform stub healthcheck
# STRUCTURE: ▶ triggers:uptime/alive → response:STUB → ⎋
# region MODULE_CONTRACT
## @purpose     Agent gateway uptime monitoring skill
## @scope       Checks whether the Hermes agent gateway is responsive
## @invariants  STUB — to be implemented with cron-based periodic checks
## @changes     LAST_CHANGE: 2026-06-23 | Phase A initial creation
# endregion MODULE_CONTRACT

# monitor-uptime
## @purpose Check and report whether the agent gateway is responsive
triggers:
  - "uptime check"
  - "is agent alive"
  - "monitor uptime"
  - "gateway status"

response: |
  [STUB] Uptime monitoring skill. To be implemented with cron-based periodic checks.
