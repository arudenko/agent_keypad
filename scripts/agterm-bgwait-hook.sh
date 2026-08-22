#!/usr/bin/env bash
# agterm-bgwait-hook -- make "waiting on a background subagent" visible in agterm.
#
# Claude Code fires its Stop hook whenever a turn ends, INCLUDING when the turn ends
# because the agent is parked waiting for a background subagent. The stock wiring maps
# Stop to `completed`, so a waiting agent shows green/idle. Stop's input carries nothing
# about pending background work (verified against the docs), but SubagentStart and
# SubagentStop do fire around subagents -- so we keep marker files per live subagent and
# let the turn-end decide between "still waiting" and "actually done".
#
# Wiring (in ~/.claude/settings.json):
#   SubagentStart  -> agterm-bgwait-hook.sh start      # add a marker
#   SubagentStop   -> agterm-bgwait-hook.sh stop       # remove it
#   PostToolUse    -> agterm-bgwait-hook.sh posttool   # track backgrounded Bash launches
#   Stop           -> agterm-bgwait-hook.sh turn-end   # markers left? waiting : completed
#   SessionStart   -> agterm-bgwait-hook.sh clean      # drop stale markers from a crash
#
# Backgrounded Bash has no completion hook event, but the harness appends an
# "[exited with code N]" line to the task's output file when it finishes -- so its marker
# stores that file's path and turn-end reaps markers whose file carries the exit line
# (or is gone). A format change upstream degrades gracefully to the MAX_AGE expiry.
#
# Like the agterm status hook, this must never interfere with the agent: it stays silent
# and always exits 0. Statuses are set through the installed agterm-agent-status.sh so
# socket/pane resolution lives in one place.

set -u

STATUS_HOOK="$HOME/.config/agterm/agent-status/agterm-agent-status.sh"
# Markers older than this are ignored and pruned: a crashed session must not leave the
# glyph "waiting" forever. Long-running background agents beyond this simply degrade to
# the stock completed/idle behaviour.
MAX_AGE_MINUTES=240

[ -n "${AGTERM_SESSION_ID:-}" ] || exit 0   # not inside agterm: nothing to do
MARK_DIR="$HOME/.cache/agterm-keypad/bgwait/$AGTERM_SESSION_ID"

# The hook payload arrives on stdin; the subagent id names the marker file. Reading
# stdin must never block the hook: python exits fast whether or not JSON arrives.
agent_id_from_stdin() {
    python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("agent_id", ""))
except Exception:
    print("")' 2>/dev/null || true
}

# The path a backgrounded Bash launch will stream its output to, or nothing if this
# PostToolUse payload is not a background launch. Read from the raw JSON defensively:
# the response shape is not documented, but the output path phrasing is stable.
background_output_from_stdin() {
    python3 -c 'import json, re, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)
if d.get("tool_name") != "Bash":
    sys.exit(0)
blob = json.dumps(d.get("tool_response", "")) + json.dumps(d.get("tool_input", ""))
m = re.search(r"Output is being written to: ([^\s\"\\\\]+)", blob)
if m and ("run_in_background" in blob or "background" in blob):
    print(m.group(1))' 2>/dev/null || true
}

prune_stale() {
    [ -d "$MARK_DIR" ] || return 0
    find "$MARK_DIR" -type f -mmin +"$MAX_AGE_MINUTES" -delete 2>/dev/null || true
}

# Remove bash markers whose task has finished: the harness appends "[exited with code N]"
# to the output file at completion, and a vanished file also means done.
reap_finished_bash() {
    [ -d "$MARK_DIR" ] || return 0
    for marker in "$MARK_DIR"/bash-*; do
        [ -e "$marker" ] || continue
        out="$(cat "$marker" 2>/dev/null)"
        if [ -z "$out" ] || [ ! -e "$out" ] \
                || tail -c 4000 "$out" 2>/dev/null | grep -q '\[exited with code'; then
            rm -f "$marker" 2>/dev/null || true
        fi
    done
}

live_markers() {
    [ -d "$MARK_DIR" ] || { echo 0; return; }
    find "$MARK_DIR" -type f 2>/dev/null | wc -l | tr -d ' '
}

case "${1:-}" in
    start)
        mkdir -p "$MARK_DIR" 2>/dev/null || exit 0
        agent_id="$(agent_id_from_stdin)"
        [ -n "$agent_id" ] || agent_id="anon-$$-$(date +%s)"
        : > "$MARK_DIR/$agent_id" 2>/dev/null || true
        ;;
    stop)
        agent_id="$(agent_id_from_stdin)"
        if [ -n "$agent_id" ] && [ -e "$MARK_DIR/$agent_id" ]; then
            rm -f "$MARK_DIR/$agent_id" 2>/dev/null || true
        else
            # Unidentifiable subagent: drop one marker so the count still drains.
            oldest="$(find "$MARK_DIR" -type f 2>/dev/null | head -1)"
            [ -n "$oldest" ] && rm -f "$oldest" 2>/dev/null || true
        fi
        ;;
    posttool)
        out="$(background_output_from_stdin)"
        [ -n "$out" ] || exit 0
        mkdir -p "$MARK_DIR" 2>/dev/null || exit 0
        # Name the marker by the output path so relaunches dedupe naturally.
        printf '%s' "$out" > "$MARK_DIR/bash-$(printf '%s' "$out" | shasum | cut -c1-12)" 2>/dev/null || true
        ;;
    turn-end)
        prune_stale
        reap_finished_bash
        if [ "$(live_markers)" -gt 0 ]; then
            # Still waiting on background work: stay visibly alive instead of "done".
            # The colour tints the sidebar glyph so a parked-waiting agent reads
            # differently from one actively running tools.
            "$STATUS_HOOK" active --blink --color "#aa66ff" || true
        else
            "$STATUS_HOOK" completed --auto-reset || true
        fi
        ;;
    clean)
        rm -rf "$MARK_DIR" 2>/dev/null || true
        ;;
esac
exit 0
