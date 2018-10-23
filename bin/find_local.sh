#!/bin/bash
#
# Finds and lists commits that are not present upstream.
#
# Searches in and under the current directory for all .git repositories.
# These are then checked for any local commits that are not present in
# any of the upstream repositories, e.g. commits that have been made
# locally and do not have a known backup anywhere else.
#
# This is helpful to know if e.g. a repository can be safely deleted
# without manually checking every branch for upstream matches.
#

print_usage() {
  echo "Usage: find_local.sh [-h | --help] [search_dir]"
}
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  print_usage
  echo
  echo "Find all local repositories, then list changes that are not upstream."
  exit 0
fi

RED=$(tput setaf 1)
BOLD=$(tput bold)
NC=$(tput sgr0)

if [[ -n "$1" ]]; then
  SEARCHDIR=${1%/}
  if [[ ! -d "$SEARCHDIR" ]]; then
    print_usage
    echo "Error: $SEARCHDIR is not a directory"
    exit 1
  fi
else
  SEARCHDIR="."
fi

printf "Finding repos...."
repo_dirs=$(find -L $SEARCHDIR -name "*.git" -type d)
count=$(echo "$repo_dirs" | wc -l)
printf "${count//[[:space:]]/} found\n"
repos=$(echo $repo_dirs | xargs -n 1 dirname)

get_remote_diff_log() {
  git log --branches --not --remotes --oneline --decorate --graph --color=always
}

changed_repos=0
for repo in $repos; do
  ( cd $repo
    log_text=$(get_remote_diff_log)
    no_up=""
    workchange=""
    if [[ -n "$log_text" ]]; then #"$(echo '$log_text' | sed 's/\\s+//g')" ]]; then
      no_up=yes
    fi
    #set +x
    if ! git diff-index --quiet HEAD --; then
      workchange=yes
    fi
    # Output, if needed
    if [[ -n "$no_up" || -n "$workchange" ]]; then
      changed_repos=$((changed_repos+1))
      echo "$BOLD$RED$repo$NC:"
    fi
    if [[ -n "$no_up" ]]; then
      get_remote_diff_log
    fi
    if [[ -n "$workchange" ]]; then
      echo "- Uncomitted changes to working directory"
    fi
  )
done

if [[ $changed_repos -eq 0 ]]; then
  echo "No local-only commits found."
fi
