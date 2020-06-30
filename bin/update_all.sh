#!/bin/bash

# Simple script to updates all sub-repositories in the current folder
# Put in <DIST>/modules
# use by:
#   cd <DIST>/modules
#   ./update_all.sh

RED=$(tput setaf 1)
BOLD=$(tput bold)
NC=$(tput sgr0)
GRAY=$(tput setaf 7)

fail() {
  name=$1
  shift
  reason=$*
  message="$BOLD$RED$name$NC$RED: $reason$NC"
  echo "$message"
  not_updated="${not_updated}\n  ${message}"
}

# Check for control master configurations
if ssh -G github.com | grep -q controlpath; then
    # HAVE_GITHUBCONTROL=yes
    # Make sure we don't have a control running
    if ! ssh -qO check git@github.com 1>/dev/null 2>&1; then
      # Start the control
      ssh -MN git@github.com&
      GITHUBMASTER_PID=$!
      echo "Started GitHub ControlMaster with PID ${GITHUBMASTER_PID}"
      trap 'printf "${GRAY}Cleaning up GitHub ControlMaster..."; ssh -qO exit git@github.com; wait $GITHUBMASTER_PID; printf "done${NC}\n"' EXIT
    fi
fi

# Save module root so that we can come back
MODULE_ROOT=$(pwd)
# Make a list of modules not updated
not_updated=""

# Find the subdirectories; for both GNU and BSD find variants
if ! subdirs=$(find -L . -type d -depth 1 2>/dev/null); then
  if ! subdirs=$(find -L . -maxdepth 1 -type d 2>/dev/null); then
    echo "Error: Could not call 'find' in a platform-valid way."
    exit 1
  fi
fi

for dir in $subdirs; do
  name=$(basename "$dir")

  if [[ -d ${MODULE_ROOT}/$dir/.git ]]; then
    cd "${MODULE_ROOT}"/"$dir" || continue
    # Detect if this is a git-svn repository
    if [[ -d .git/svn && -n "$(ls -A .git/svn/)" ]]; then
      update_command='git svn rebase'
      echo "Updating $dir (git-svn)"
    else
      echo "Updating $dir "
      update_command='git pull --ff-only origin'
    fi
    # Conditions for trying are the same for normal/svn
    if [[ $(git rev-parse --abbrev-ref HEAD) != "master" ]]; then
      git fetch || true;
      fail "$name" "Not on master branch. Not attempting update."
    elif ! git diff-index --quiet HEAD --; then
      git fetch || true;
      fail "$name" "Changes to working directory; cannot update."
    else
      if ! ${update_command}; then
        fail "$name" "git command failed."
      fi
    fi

    echo ""
  elif [[ -d ${MODULE_ROOT}/$dir/.svn ]]; then
    echo "Updating $dir (svn)"
    cd "${MODULE_ROOT}"/"$dir" || continue
    svn update
    exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
      fail "$name" "svn exited with status $exit_code"
    fi
    echo ""
  fi
done

# Make sure the user is told about anything not updated
if [[ -n "$not_updated" ]]; then
  printf "Some modules were not updated: %b%b\n" "${not_updated}" "$NC"
fi
