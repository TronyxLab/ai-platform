# 03-security — Pre-Launch Security Audit

## Scope

Pre-production-launch security audit of `ai-platform`: a DevOps platform holding SSH keys, age/sops secrets,
deploying to VPS via SSH forced-commands, running GitHub Actions CI, docker-compose stacks, nginx ingress,
LiteLLM proxy, monitoring, status-page (Jinja2). Read-only: no code changed. Method: 10 parallel subagents,
one per direction; findings require concrete file:line evidence and a realistic attack path — generic
recommendations excluded. Existing enforcement (security-scan.yml: trivy+pip-audit+gitleaks, tests/gates/,
.trivyignore policy) treated as context, not findings.

## Directions → files

| # | Direction | File |
|---|-----------|------|
| 1 | Authentication | `findings-01-authentication.md` |
| 2 | Authorization | `findings-02-authorization.md` |
| 3 | Privilege escalation | `findings-03-privesc.md` |
| 4 | Secrets | `findings-04-secrets.md` |
| 5 | PII/data leakage | `findings-05-pii-leakage.md` |
| 6 | Injection (SQL/cmd/path/SSTI/YAML) | `findings-06-injection.md` |
| 7 | Filesystem/subprocess | `findings-07-fs-subprocess.md` |
| 8 | Network/API boundaries | `findings-08-network-boundaries.md` |
| 9 | Webhooks/external input | `findings-09-external-input.md` |
| 10 | DoS/rate limiting | `findings-10-dos.md` |

Checklist applied per direction: SQL/command injection, path traversal, SSRF, unsafe deserialization,
eval/exec, subprocess abuse, arbitrary file access, credential leakage, logging secrets, broken access
control, IDOR/BOLA, tenant isolation, replay, unsafe webhook handling, debug/admin endpoints.

## Finding format

`SEC-XXXX`: Severity · Attack surface · File · Symbol · Preconditions · Attack path · Impact · Evidence ·
Confidence · Minimal fix · Regression test · Must fix before launch (YES/NO).

## Verdict

Real launch blockers only: `BLOCKERS.md`. A blocker = exploitable preconditions reachable in the default
deployment, high confidence, material impact.
