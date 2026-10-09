#!/bin/sh
# Session-start reminder for the Vammo Work Tracker.
#
# Tracking scope: the optional file ~/.vammo/tracked-folders (or $VAMMO_TRACKED_FOLDERS) lists
# folders, one per line. A plain line tracks the folder and everything below it; a line starting
# with "!" excludes it; "#" starts a comment. The longest matching line wins. Without the file,
# every folder is tracked. With the file, folders that match no line are not tracked.

scope_file="${VAMMO_TRACKED_FOLDERS:-$HOME/.vammo/tracked-folders}"
dir="${CLAUDE_PROJECT_DIR:-$(pwd)}"

# Compare paths as lowercase, forward-slash, without a trailing slash; C:\x becomes /c/x.
norm() {
  printf '%s\n' "$1" | sed -e 's#\\#/#g' -e 's#^\([A-Za-z]\):#/\1#' -e 's#/*$##' | tr '[:upper:]' '[:lower:]'
}

tracked=yes
if [ -f "$scope_file" ]; then
  tracked=no
  best=-1
  target="$(norm "$dir")"
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(printf '%s' "$line" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$line" in ''|'#'*) continue ;; esac
    verdict=yes
    case "$line" in '!'*) verdict=no; line="${line#!}" ;; esac
    case "$line" in '~'*) line="$HOME${line#\~}" ;; esac
    rule="$(norm "$line")"
    case "$target/" in
      "$rule/"*)
        if [ "${#rule}" -gt "$best" ]; then best=${#rule}; tracked=$verdict; fi
        ;;
    esac
  done < "$scope_file"
fi

if [ "$tracked" = yes ]; then
  echo 'Vammo Work Tracker is connected (MCP server vammo). For any coding task, follow the vammo-work-tracking skill: get_context, then ensure_work_item and start_work_session, link_code after each push or pull request, record_progress at milestones and finish_work_session at the end. If a vammo tool reports that sign-in is needed, ask the person to run /mcp and choose Authenticate for vammo.'
else
  echo "Vammo Work Tracker: tracking is off for this folder ($dir) by $scope_file. Do not track this session (no ensure_work_item, start_work_session, link_code, record_progress or finish_work_session) unless the person explicitly asks to track this work; if they ask to track this folder from now on, follow the Tracking scope section of the vammo-work-tracking skill. The vammo tools stay available for anything else the person asks."
fi
