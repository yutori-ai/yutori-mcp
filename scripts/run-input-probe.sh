#!/bin/zsh

set -euo pipefail

repo_path="${0:A:h:h}"
app_path="$($repo_path/scripts/build-input-probe.sh debug)"
session_id="manual-$(date -u +%Y%m%dT%H%M%SZ)"
log_path="$repo_path/.context/input-probe/$session_id/app-events.jsonl"

mkdir -p "${log_path:h}"
open -n "$app_path" --args --session-id "$session_id" --log-path "$log_path"

print "Opened Yutori Input Probe"
print "Session: $session_id"
print "Events:  $log_path"
