#!/usr/bin/env bash
# Validate all TEI XML files in data/ against the EpiDoc RELAX NG schema using jing.
#
# Usage: scripts/validate.sh [--manifest FILE]
#
# By default this exits non-zero if any file has a confirmed schema
# violation (see below). With --manifest FILE, it instead always exits 0
# and writes the relative paths of confirmed-failing files to FILE, one per
# line (empty file if none) -- for CI workflows that want to prune failing
# files at release time rather than block on them.
#
# jing's multi-file batch mode shares one compiled grammar (with internal
# derivative caches) across every file in a single JVM invocation. That is
# normally fine, but on rare occasions it has been observed to report a
# false-positive error on content that is actually valid (confirmed by
# hand and by re-validating the same file on its own). To get both the
# speed of batch validation and single-file reliability, this script:
#
#   1. Validates every file in one batch pass (fast: a few seconds for the
#      whole corpus).
#   2. Re-validates, one file at a time in its own JVM invocation, only the
#      files the batch pass flagged.
#   3. Reports as real failures only files that fail step 2.
#
# This means a transient batch-mode glitch never fails CI, while a genuine
# schema violation always will.
set -euo pipefail

MANIFEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --manifest)
      MANIFEST="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${EPIDOC_SCHEMA:-https://epidoc.stoa.org/schema/latest/tei-epidoc.rng}"
JING="${JING:-jing}"

cd "$REPO_ROOT"

mapfile -t FILES < <(find data -name "*.xml" ! -name "__cts__.xml" | sort)

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "No files found under data/ to validate." >&2
  exit 1
fi

echo "Validating ${#FILES[@]} files against $SCHEMA" >&2

BATCH_OUT="$(mktemp)"
trap 'rm -f "$BATCH_OUT"' EXIT

# Step 1: fast batch pass. jing exits non-zero on any error; don't let
# set -e stop us before we get to the confirmation pass.
"$JING" "$SCHEMA" "${FILES[@]}" > "$BATCH_OUT" 2>&1 || true

mapfile -t FLAGGED < <(grep -oE '^[^:]+\.xml' "$BATCH_OUT" | sort -u)

CONFIRMED_FAILURES=()

if [ "${#FLAGGED[@]}" -gt 0 ]; then
  echo "Batch pass flagged ${#FLAGGED[@]} file(s); re-validating individually to confirm..." >&2

  for f in "${FLAGGED[@]}"; do
    if ! "$JING" "$SCHEMA" "$f"; then
      CONFIRMED_FAILURES+=("${f#"$REPO_ROOT"/}")
    else
      echo "Note: $f was flagged in the batch pass but is valid on its own (spurious batch-mode error, ignoring)." >&2
    fi
  done
fi

if [ -n "$MANIFEST" ]; then
  : > "$MANIFEST"
  if [ "${#CONFIRMED_FAILURES[@]}" -gt 0 ]; then
    printf '%s\n' "${CONFIRMED_FAILURES[@]}" > "$MANIFEST"
  fi
  echo "Wrote ${#CONFIRMED_FAILURES[@]} failing file path(s) to $MANIFEST." >&2
  exit 0
fi

if [ "${#CONFIRMED_FAILURES[@]}" -gt 0 ]; then
  echo "FAILED: ${#CONFIRMED_FAILURES[@]} file(s) have confirmed schema violations (see errors above)." >&2
  exit 1
fi

echo "OK: all ${#FILES[@]} files are valid." >&2
