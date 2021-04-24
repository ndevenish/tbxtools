#!/bin/bash

# Simple script to updates all sub-repositories in the current folder
# Put in <DIST>/modules
# use by:
#   cd <DIST>/modules
#   ./update_all.sh

RED=$(tput setaf 1)
YELLOW=$(tput setaf 3)
BOLD=$(tput bold)
NC=$(tput sgr0)

print_usage() {
    printf 'Usage: %s [-v|--verbose] [-h|--help]\n' "$0"
}
print_help() {
    printf 'Update all DIALS modules in current folder\n'
    print_usage
    printf '\t%s\n' "-v, --verbose: More detailed debugging output"
    printf '\t%s\n' "-h, --help: Prints this message"
}
git-get-main-branch() {
    dir="${1:-.}"
    if git -C "$dir" show-ref -q --verify refs/remotes/origin/main; then
        printf "main"
    else
        printf "master"
    fi
}

while test $# -gt 0; do
    _key="$1"
    case "$_key" in
    -v | --verbose)
        _arg_verbose=1
        shift
        ;;
    -h | --help)
        print_help
        exit 0
        ;;
    -h*)
        print_help
        exit 0
        ;;
    *)
        _last_positional="$1"
        _positionals+=("$_last_positional")
        _positionals_count=$((_positionals_count + 1))
        ;;
    esac
    shift
done
if [[ $_positionals_count -gt 0 ]]; then
    print_usage
    echo "${RED}${BOLD}Error${NC}: unknown arguments" "${_positionals[@]}"
    exit 1
fi

fail() {
    name=$1
    shift
    reason=$*
    message="$BOLD$RED$name$NC$RED: $reason$NC"
    echo "$message"
    not_updated="${not_updated}\n  ${message}"
}
warn() {
    name=$1
    shift
    reason=$*
    message="$YELLOW$name: $reason$NC"
    echo "$message"
    not_updated="${not_updated}\n  ${message}"
}

# Check for control master configurations
if ssh -G github.com | grep -q controlpath; then
    # Make sure we don't have a control running
    if ! ssh -qO check git@github.com 1>/dev/null 2>&1; then
        # Start the control
        ssh -MN git@github.com &
        GITHUBMASTER_PID=$!
        if [[ -n $_arg_verbose ]]; then
            echo "Started GitHub ControlMaster with PID ${GITHUBMASTER_PID}"
            trap 'printf "Cleaning up GitHub ControlMaster..."; ssh -O exit git@github.com; wait $GITHUBMASTER_PID; printf "done\n"' EXIT
        else
            trap 'ssh -qO exit git@github.com || kill $GITHUBMASTER_PID; wait $GITHUBMASTER_PID;' EXIT
        fi
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
        # Work out the branch/remote information
        main="$(git-get-main-branch .)"
        upstream_branch="$(git rev-parse --abbrev-ref --symbolic-full-name "$main@{u}")"
        tracking_remote="${upstream_branch%/*}"
        tracking_branch="${upstream_branch#*/}"

        # Detect if this is a git-svn repository
        if [[ -d .git/svn && -n "$(ls -A .git/svn/)" ]]; then
            update_command='git svn rebase'
            echo "Updating $dir (git-svn)"
        else
            echo "Updating $dir "
            update_command="git pull --ff-only --no-rebase $tracking_remote"
        fi

        # Conditions for trying are the same for normal/svn
        if [[ $(git rev-parse --abbrev-ref HEAD) != "$main" ]]; then
            # Not on main branch - we want to update it (ff only) but warn
            # that we aren't on the main branch

            # Try to fetch this straight to the branch
            if ! git fetch "$tracking_remote" "$tracking_branch:$main"; then
                fail "$name" "Not on $main branch, and could not update"
            else
                warn "$name" "Not on $main branch, but updated the local pointer"
            fi
        elif ! git diff --no-ext-diff --quiet; then
            # Let's try an update anyway - but be safe and stash it
            original_changes="$(git stash create)"
            git stash store "$original_changes"
            original_commit="$(git rev-parse HEAD)"
            if ! ${update_command}; then
                fail "$name" "Tried to smartly update changed working directory but failed"
                # Restore the original state
                git reset --hard "$original_commit"
                git stash pop
            else
                git stash drop
            fi
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
