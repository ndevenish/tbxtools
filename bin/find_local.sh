#!/bin/bash
#
# Find all local repositories, then show changes that are not upstream

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  echo "Find all local repositories, then list changes that are not upstream"
  exit 0
fi

RED=$(tput setaf 1)
BOLD=$(tput bold)
NC=$(tput sgr0)

printf "Finding repos...."
repos=$(find -L . -name ".git" -type d | xargs dirname)
printf "$(echo $repos | wc -l) found\n"

get_remote_diff_log() {
  git log --branches --not --remotes --oneline --decorate --graph --color=always
}

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

