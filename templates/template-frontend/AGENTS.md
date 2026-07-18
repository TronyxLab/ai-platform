# GREP_SUMMARY: project AGENTS.md platform-context commands domain {{PROJECT_NAME}}
# STRUCTURE: ┌platform context┐ → ◇ what platform provides → ◇ commands → ◇ DO NOT

# Project {{PROJECT_NAME}}

## Platform context
- Domain: {{DOMAIN}}
- CI/CD: {{ORG_NAME}}/ai-platform/.github/workflows/deploy-project.yml@main

## What platform provides
Run `grep PLATFORM_ .env.platform` for full service list.

## Commands
- `make sync-env` — regenerate .env.platform
- `make status`  — check deployment status

## DO NOT
- Edit `.env.platform` manually — it is auto-generated
- Store secrets in git
