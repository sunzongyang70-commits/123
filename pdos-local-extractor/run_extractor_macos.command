#!/bin/bash

# PDoS Phase 1.2A local-only STL evidence extractor for macOS.
# This script performs no network access and reads no prior Phase 1.2 JSON.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

pause_before_close() {
  if [ "${PDOS_NO_PAUSE:-0}" != "1" ]; then
    echo
    read -n 1 -s -r -p "Press any key to close..."
    echo
  fi
}

fail() {
  echo
  echo "FAIL: $1"
  pause_before_close
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail \
  "Python 3 was not found. Please install Python 3.11+ from python.org."

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Python 3.11 or later is required."

echo "PDoS Phase 1.2A — Local Ground Truth Evidence Extractor"
echo "No files will be uploaded."
echo

if [ -n "${PDOS_STL_PATH:-}" ]; then
  STL_PATH="$PDOS_STL_PATH"
else
  echo "把 STL 文件拖进终端窗口，然后按 Enter"
  echo "Drag the STL file into this Terminal window, then press Enter:"
  IFS= read -r STL_INPUT
  [ -n "$STL_INPUT" ] || fail "No STL path was provided."

  # Finder/Terminal drag-and-drop may insert shell quoting or backslash escapes.
  # shlex accepts one path only, preventing accidental interpretation as commands.
  STL_PATH="$(python3 -c \
    'import shlex, sys; p=shlex.split(sys.argv[1]); print(p[0] if len(p) == 1 else "")' \
    "$STL_INPUT")"
fi

[ -n "$STL_PATH" ] || fail "The entered text did not resolve to one file path."
[ -f "$STL_PATH" ] || fail "STL file not found: $STL_PATH"

case "${STL_PATH##*.}" in
  [sS][tT][lL]) ;;
  *) fail "The selected file does not have an .stl extension." ;;
esac

OUTPUT_DIR="$(cd "$(dirname "$STL_PATH")" && pwd)"
OUTPUT_PATH="$OUTPUT_DIR/PRIMARY_MESH_EVIDENCE.json"
VALIDATION_PATH="$OUTPUT_DIR/PRIMARY_MESH_EVIDENCE.validation.json"

echo
echo "Input:  $STL_PATH"
echo "Output: $OUTPUT_PATH"
echo

cd "$SCRIPT_DIR" || fail "Could not enter the extractor directory."
python3 -m pdos_extractor \
  --input "$STL_PATH" \
  --output "$OUTPUT_PATH" \
  --topology-mode both \
  --symmetry-mode off \
  --fail-on-validation-error
EXTRACTOR_EXIT=$?

if [ -f "$VALIDATION_PATH" ]; then
  OVERALL_STATUS="$(python3 -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("overall_status", "FAIL"))' \
    "$VALIDATION_PATH" 2>/dev/null)"
else
  OVERALL_STATUS="FAIL"
fi

case "$OVERALL_STATUS" in
  PASS|PASS_WITH_WARNINGS) ;;
  *) OVERALL_STATUS="FAIL" ;;
esac

echo
echo "Final validation status: $OVERALL_STATUS"
echo "Evidence:   $OUTPUT_PATH"
echo "Validation: $VALIDATION_PATH"

pause_before_close

if [ "$EXTRACTOR_EXIT" -ne 0 ] || [ "$OVERALL_STATUS" = "FAIL" ]; then
  exit 1
fi
exit 0
