#!/usr/bin/env bash
# GREP_SUMMARY: timing helper run measure tsv evidence night-session-141
# STRUCTURE: source timing.sh → run PHASE STEP CAUSE cmd... → append timings.tsv → ⎋ exit cmd code
# Usage: source timing.sh; run "<phase>" "<step>" "<cause>" <cmd...>
# Columns: phase, step, command, start_iso, end_iso, duration_s, exit_code, cache_hit, retries, cause
# Env overrides: RUN_CACHE=yes|no|na (default na), RUN_RETRIES=N (default 0)
# Note: long commands write sub-steps as separate rows with cause "sub: <detail>".
EVIDENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TIMINGS_TSV="${EVIDENCE_DIR}/timings.tsv"

_run_init() {
  if [[ ! -f "$TIMINGS_TSV" ]]; then
    printf 'phase\tstep\tcommand\tstart_iso\tend_iso\tduration_s\texit_code\tcache_hit\tretries\tcause\n' > "$TIMINGS_TSV"
  fi
}

# run PHASE STEP CAUSE cmd... — append timing row, propagate exit code
run() {
  local phase="$1" step="$2" cause="$3"
  shift 3
  _run_init
  local start_iso end_iso start_epoch dur rc cmd_str
  start_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  start_epoch=$(date +%s)
  cmd_str="$*"
  "$@"
  rc=$?
  end_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  dur=$(( $(date +%s) - start_epoch ))
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$phase" "$step" "$cmd_str" "$start_iso" "$end_iso" "$dur" "$rc" \
    "${RUN_CACHE:-na}" "${RUN_RETRIES:-0}" "$cause" >> "$TIMINGS_TSV"
  return $rc
}

# note PHASE STEP CAUSE — append a zero-duration informational row (e.g. sub-step of long command)
note() {
  _run_init
  local now
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$1" "$2" "[info]" "$now" "$now" "0" "0" "${RUN_CACHE:-na}" "${RUN_RETRIES:-0}" "$3" >> "$TIMINGS_TSV"
}

# summary_line PHASE — print aggregate for a phase (dur sum, count)
summary_line() {
  local phase="$1" total=0 n=0
  while IFS=$'\t' read -r p _ _ _ _ dur _ _ _ _; do
    [[ "$p" == "$phase" ]] && { total=$((total + dur)); n=$((n + 1)); }
  done < "$TIMINGS_TSV"
  echo "phase=$phase steps=$n total_s=$total"
}

_run_init
