#!/usr/bin/env bash
# Isolated checkouts for parallel agent tasks.
#
# Two coding agents editing one working tree overwrite each other, and you
# cannot tell afterwards which one produced what. A git worktree gives each
# task a real checkout on its own branch, sharing one .git directory — so
# several Freebuff sessions can run at once without interfering.
#
#   scripts/worktree.sh add 004 005      -> ../st-004, ../st-005 on task/004, task/005
#   scripts/worktree.sh list             -> what is live
#   scripts/worktree.sh rm 004           -> tear down (refuses if work is uncommitted)
#   scripts/worktree.sh rm --all
#
# Branch names match the spec numbers in .claude/tasks/, so a worktree is
# always traceable back to the spec that created it.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PARENT="$(dirname "$REPO_ROOT")"
PREFIX="st"
BASE="${WORKTREE_BASE:-origin/docs/claude-md}"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

slug_for() {  # spec number -> branch slug, read from the spec file if present
  local n="$1" f
  f="$(find "$REPO_ROOT/.claude/tasks" -maxdepth 1 -name "${n}-*.md" 2>/dev/null | head -1)"
  if [ -n "$f" ]; then basename "$f" .md; else echo "$n"; fi
}

cmd_add() {
  [ "$#" -gt 0 ] || { echo "usage: worktree.sh add <num> [num...]" >&2; exit 2; }
  git -C "$REPO_ROOT" fetch --quiet origin || echo "warning: fetch failed, using local refs" >&2
  for n in "$@"; do
    local slug dir branch
    slug="$(slug_for "$n")"
    dir="$PARENT/${PREFIX}-${n}"
    branch="task/${slug}"

    if [ -e "$dir" ]; then echo "skip  $dir already exists"; continue; fi
    if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$branch"; then
      git -C "$REPO_ROOT" worktree add "$dir" "$branch" >/dev/null
      echo "reuse $dir  ->  $branch (existing branch)"
    else
      git -C "$REPO_ROOT" worktree add -b "$branch" "$dir" "$BASE" >/dev/null
      echo "new   $dir  ->  $branch (from $BASE)"
    fi
  done
  echo
  echo "Run one agent per directory, e.g.:"
  echo "  cd $PARENT/${PREFIX}-$1/stratoscan && freebuff"
}

cmd_list() {
  git -C "$REPO_ROOT" worktree list | awk -v p="$PREFIX" '
    NR==1 { printf "%-44s %s  (main)\n", $1, $NF; next }
    { printf "%-44s %s\n", $1, $NF }'
}

cmd_rm() {
  [ "$#" -gt 0 ] || { echo "usage: worktree.sh rm <num>|--all" >&2; exit 2; }
  local targets=()
  if [ "$1" = "--all" ]; then
    while IFS= read -r d; do targets+=("$d"); done < <(
      git -C "$REPO_ROOT" worktree list --porcelain \
        | awk '/^worktree /{print $2}' | grep "/${PREFIX}-" || true)
  else
    for n in "$@"; do targets+=("$PARENT/${PREFIX}-${n}"); done
  fi

  for dir in "${targets[@]}"; do
    [ -d "$dir" ] || { echo "skip  $dir (not present)"; continue; }
    # Refuse to discard work that was never pushed anywhere.
    if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
      echo "KEEP  $dir has uncommitted changes — commit or discard them first" >&2
      continue
    fi
    git -C "$REPO_ROOT" worktree remove "$dir"
    echo "rm    $dir"
  done
  git -C "$REPO_ROOT" worktree prune
}

case "${1:-}" in
  add)  shift; cmd_add "$@" ;;
  list) cmd_list ;;
  rm)   shift; cmd_rm "$@" ;;
  ""|-h|--help) usage 0 ;;
  *) echo "unknown command: $1" >&2; usage 2 ;;
esac
