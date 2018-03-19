#!/bin/bash

# Simple script to updates all sub-repositories in the current folder
# Put in <DIST>/modules
# use by:
#   cd <DIST>/modules
#   ./update_all.sh

RED=$(tput setaf 1)
BOLD=$(tput bold)
NC=$(tput sgr0)

fail() {
  name=$1
  shift
  reason=$*
  message="$BOLD$RED$name$NC$RED: $reason$NC"
  echo $message
  not_updated="${not_updated}\n  ${message}"
}

# Save module root so that we can come back
MODULE_ROOT=$(pwd)
# Make a list of modules not updated
not_updated=""

# Find the subdirectories; for both GNU and BSD find variants
if ! subdirs=$(find . -type d -depth 1 2>/dev/null); then
  if ! subdirs=$(find . -maxdepth 1 -type d 2>/dev/null); then
    echo "Error: Could not call 'find' in a platform-valid way."
    exit 1
  fi
fi

for dir in $subdirs; do
  name=$(basename $dir)
  if [[ -d ${MODULE_ROOT}/$dir/.git ]]; then
    echo "Updating $dir "

    cd ${MODULE_ROOT}/$dir
    if [[ $(git rev-parse --abbrev-ref HEAD) != "master" ]]; then
      git fetch || true;
      fail $name "Not on master branch. Not attempting update."
    elif ! git diff-index --quiet HEAD --; then
      git fetch || true;
      fail $name "Changes to working directory; cannot update."
    else
      if ! git pull --ff-only origin; then
        fail $name "git command failed."
      fi
    fi
    echo ""
  fi
  if [[ -d ${MODULE_ROOT}/$dir/.svn ]]; then
    echo "Updating $dir (svn)"
    cd ${MODULE_ROOT}/$dir
    svn update
    exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
      fail $name "svn exited with status $exit_code"
    fi
    echo ""
  fi
done

# Make sure the user is told about anything not updated
if [[ -n "$not_updated" ]]; then
  printf "Some modules were not updated: ${not_updated}$NC\n"
fi
