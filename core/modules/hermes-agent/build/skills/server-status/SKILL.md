# GREP_SUMMARY: SKILL server-status base-agent health uptime
# STRUCTURE: ▶ triggers:/status/health/uptime → response:operational → ⎋
# region MODULE_CONTRACT
## @purpose     Report Hermes agent container health, uptime, and version
## @scope       Responds to /status, health, uptime queries from users
## @invariants  Lightweight status check — no external dependencies
## @changes     LAST_CHANGE: 2026-06-23 | Phase A initial creation
# endregion MODULE_CONTRACT

# server-status
## @purpose Report Hermes agent container health, uptime, and version
triggers:
  - "/status"
  - "status"
  - "health"
  - "uptime"
  - "agent status"

response: |
  Agent is operational.
  - Uptime: \<uptime\>
  - Version: base-image latest
  - Container: hermes-base-agent
