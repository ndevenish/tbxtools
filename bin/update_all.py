#!/usr/bin/env python3
import functools
import os
import sys
import textwrap
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from queue import Queue
from subprocess import PIPE, STDOUT, CalledProcessError, Popen, check_output
from typing import Dict, List, NamedTuple

R = "\033[31m"
G = "\033[32m"
B = "\033[34m"
NC = "\033[0m"
BOLD = "\033[1m"
UP_AND_CLEAR = "\033[F\033[2K"

MAX_CONCURRENT = 5


class TaskUpdate(NamedTuple):
    path: Path
    running: bool
    error: bool
    status: str


class Task(NamedTuple):
    kind: str
    path: Path


class TaskError(NamedTuple):
    path: str
    message: str


class GitBranch(NamedTuple):
    name: str
    sha: str
    upstream: str = None


EXIT_THREADS = False


class ExitThread(Exception):
    """Thrown if we are deep in a thread and encountered an exit request"""


class Git(object):
    def __init__(self, repo, updater=None):
        self.repo = repo
        self.last_output = []
        if updater:
            self.updater = updater
        else:
            self.updater = lambda *args, **kwargs: None

    def check_output(self, command, **kwargs):
        try:
            cmd = ["git"] + list(command)
            output = check_output(
                cmd,
                cwd=kwargs.pop("cwd", self.repo),
                stderr=kwargs.pop("stderr", STDOUT),
                encoding=kwargs.pop("encoding", "utf-8"),
                **kwargs,
            ).strip()
        except CalledProcessError as e:
            self.last_output = e.output
            raise
        self.last_output = output
        return output

    def check_call(self, command: List[str], sticky_lines: List[str] = [], **kwargs):
        """Run check_call on git, but get output as updates.

        Logic being: You are calling this command for it's action, not
        to parse it's output. If we weren't in a multi-threaded scenario
        we would be letting this print straight to the terminal.

        Args:
            command: The argv list to pass to git
            sticky_lines:
                If specified, only lines that contain one or more of
                these strings will be sent to the updater. Otherwise,
                all lines will be sent.
        """
        assert kwargs.pop("stderr", STDOUT) == STDOUT
        assert kwargs.pop("stdout", PIPE) == PIPE
        self.updater("Running git", *command)
        cmd = ["git"] + command
        process = Popen(
            cmd,
            stderr=STDOUT,
            stdout=PIPE,
            encoding=kwargs.pop("encoding", "utf-8"),
            bufsize=kwargs.pop("bufsize", 1),
            cwd=kwargs.pop("cwd", self.repo),
            universal_newlines=kwargs.pop("universal_newlines", True),
            **kwargs,
        )
        lines = []
        for line in process.stdout:
            # Good place to check for early exit
            if EXIT_THREADS:
                process.terminate()
                process.poll()
                raise ExitThread
            lines.append(line)
            if line:
                # Ignore lines with file changes
                if sticky_lines and any(x in line for x in sticky_lines):
                    self.updater(line)
                elif not sticky_lines:
                    self.updater(line)

        self.last_output = "".join(lines)
        # Finished output, wait for the process to finish
        code = process.poll()
        if code != 0:
            raise CalledProcessError(code, cmd)

    def call(self, *args, **kwargs):
        """Run check_call, but explicitly for the return value"""
        try:
            self.check_call(*args, **kwargs)
        except CalledProcessError:
            return False
        else:
            return True

    def get_current_branch(self):
        return self.check_output(["rev-parse", "--abbrev-ref", "HEAD"])

    def get_main_branch(self):
        if self.call(["show-ref", "-q", "--verify", "refs/remotes/origin/main"]):
            return "main"
        return "master"

    def get_upstream_branch(self, branch):
        upstream = self.check_output(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{u}}"]
        )
        return upstream.strip().split("/", maxsplit=1)

    def rev_parse(self, reference):
        return self.check_output(["rev-parse", reference])

    def get_all_branches(self):
        """Get information about all branches"""

        # Get a list of all local branches in form
        # branchname SHA1 remote/remotebranch
        # branchname SHA1 remote/remotebranch
        # ....
        output = self.check_output(
            ["branch", "--format='%(refname:short) %(objectname) %(upstream:short)'"]
        )
        branches = {}
        for line in output.splitlines():
            branch = GitBranch(*line.split())
            branches[branch.name] = branch
        return branches


def update_git_repo(path: Path, update, error_comms):
    git = Git(path, updater=update)
    # branch = git.get_current_branch()
    main = git.get_main_branch()

    # Save the current commit of this branch so we know if we changed
    original_commit = git.rev_parse(main)
    tracking_remote, tracking_branch = git.get_upstream_branch(main)
    update(f"main branch is {main}")
    update("upstream is", tracking_remote, tracking_branch)

    # Decide which command to run to update
    if (path / ".git" / "svn").is_dir() and git.get_current_branch() == main:
        # This is a git-svn repository - just carry over the legacy
        # logic, this isn't very well tested and might not work
        update("Is git-svn repository")

        def git_do_update():
            return git.call(["svn", "rebase"])

    else:

        def git_do_update():
            return git.call(
                ["pull", "--ff-only", "--no-rebase", tracking_remote],
                sticky_lines={
                    "insertions",
                    "changed",
                    "deletions",
                    "Fast-forward",
                    "Updating",
                },
            )

    # Allow overriding of messages
    success_message = "Updated {0}...{1}."
    nochange_message = "Already up to date."

    # If not on main branch, then try to update it in the background
    if not git.get_current_branch() == main:
        update(f"{main} not actively checked out branch. Updating background pointer")
        if not git.call(["fetch", tracking_remote, f"{tracking_branch}:{main}"]):
            error_comms.put(TaskError(path, git.last_output))
            update(f"Not on {main} branch, and could not update.", error=True)
            return
        success_message = f"Updated non-checked-out branch {main} {{0}}...{{1}}."
        nochange_message = f"Non-checked-out branch {main} already up to date."
    elif not git.call(["diff", "--no-ext-diff", "--quiet"]):
        # We have a dirty working area. Let's stash this and try anyway
        update("Working directory is dirty: Creating stash to preserve state")
        stash = git.check_output(["stash", "create"])
        git.check_call(["stash", "store", stash])
        original_branch_commit = git.check_output(["rev-parse", "HEAD"])

        if not git_do_update():
            update(
                "Tried to smartly update dirty working directory but failed", error=True
            )
            error_comms.put(TaskError(path, git.last_output))
            # Restore the original state before we tried this
            git.check_call(["reset", "--hard", original_branch_commit])
            git.check_call(["stash", "pop"])
            return

        # We succeeded - did anything change?
        nochange_message = "Already up to date, with working changes"
        success_message = f"Merged branch {main} {{0}}..{{1}} into working changes"
    else:
        if not git_do_update():
            update("Failed to update.", error=True)
            error_comms.put(TaskError(path, git.last_output))
            return

    # Decide which message to send - did we actually make a change?
    new_commit = git.rev_parse(main)
    if original_commit == new_commit:
        update(nochange_message)
    else:
        update(success_message.format(original_commit[:6], new_commit[:6]))


def update_repo(update_function, path, communicator, error_comms):
    "Shim function to make catching/diagnosing exceptions easier"
    try:
        update_function(path, communicator, error_comms)
    except ExitThread:
        # We raised to escape a thread because we are shutting down
        return
    except Exception:
        error_comms.put(TaskError(path, traceback.format_exc()))
        raise


def _update_comms_queue(comms, path, *args, running=True, error=False):
    """Convenience function for sending an update"""
    message = " ".join(str(x) for x in args).rstrip().splitlines()
    if message:
        message = message[-1]
    comms.put(TaskUpdate(path, running, error, message))


def find_all_repos(path: Path) -> List[Task]:
    """Find all repository-like folders that we can update"""
    repos = []
    for subdir in path.glob("*/"):
        if (subdir / ".git").is_dir():
            repos.append(Task("git", subdir))
        if (subdir / ".svn").is_dir():
            repos.append(Task("svn", subdir))
    return sorted(repos)


updaters = {"git": update_git_repo}
tasks = find_all_repos(Path.cwd())
paths = [t.path for t in tasks]

task_name_width = max(len(p.path.name) for p in tasks)

active_tasks: Dict[Path, Future] = {}
task_comms: "Queue[TaskUpdate]" = Queue()
# Feedback channel for errors. Any log output will be sent here
error_comms = Queue()

# Keep track of how many active tasks we had last time
highest_task = 0
task_status = {}
with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
    try:
        # Submit all tasks at once
        print(f"Running update for {len(tasks)} repositories:")
        for kind, path in tasks:
            active_tasks[path] = pool.submit(
                update_repo,
                updaters[kind],
                path,
                functools.partial(_update_comms_queue, task_comms, path),
                error_comms,
            )
            task_status[path] = TaskUpdate(path, True, False, "")

        # Pre-print as many as we can fit on the screen without going over
        _, tlines = os.get_terminal_size()
        highest_task = min(len(tasks) - 1, tlines - 3)
        print("\n".join([f"    {path.name}:" for _, path in tasks]))

        while active_tasks:
            time.sleep(0.1)
            # Start this with a value outside the highest task, so we know nothing updated
            earliest_task_updated = highest_task + 1
            # Pull down all the update messages
            while not task_comms.empty():
                update = task_comms.get()
                # Keep track of the earliest task so we can update efficiently
                earliest_task_updated = min(
                    earliest_task_updated, paths.index(update.path)
                )
                task_status[update.path] = update

            # Check to see if the future ended (maybe we got no message yet?)
            # - if it did finish, then update if necessary
            to_remove = []
            for path, fut in active_tasks.items():
                status = task_status[path]
                if fut.done():
                    to_remove.append(path)
                    if status.running:
                        status = status._replace(running=False)
                        earliest_task_updated = min(
                            earliest_task_updated, paths.index(status.path)
                        )
                    exception = fut.exception()
                    if exception:
                        status = status._replace(
                            status=f"{type(exception).__name__}: {exception}",
                            error=True,
                        )
                    task_status[path] = status

            for path in to_remove:
                del active_tasks[path]

            # If we didn't update anything, go back to sleeping
            if earliest_task_updated > highest_task:
                continue
            # Now, work out how far we need to rewind to redraw the table
            update_message = StringIO()

            # We are on highest_task+1 line
            rewind_by = highest_task - earliest_task_updated + 1
            update_message.write(UP_AND_CLEAR * rewind_by)

            # Now, rewrite all of the status messages
            for path in paths[earliest_task_updated:]:
                # if path in active_tasks and not (
                #     active_tasks[path].running() or active_tasks[path].done()
                # ):
                #     continue
                task_index = paths.index(path)
                highest_task = max(highest_task, task_index)
                status = task_status[path]
                if status.running:
                    linecolour = ""
                    colour = B
                elif status.error:
                    linecolour = R
                    colour = ""
                else:
                    linecolour = G
                    colour = ""
                update_message.write(
                    f"{linecolour}    {path.name+':':{task_name_width+1}} {colour}{status.status}{NC}\n"
                )
            print(update_message.getvalue(), flush=True, end="")

        # Now, handle printing any error logs - print in sort order
        errors = {}
        while not error_comms.empty():
            error = error_comms.get()
            errors[error.path] = error.message

        for path in sorted(errors.keys()):
            print(f"{BOLD}{R}Error: Could not update {path}:{NC}{R}")
            print(textwrap.indent(errors[path], "    ") + NC)

        if errors:
            sys.exit(1)
    except KeyboardInterrupt:
        # Make sure that our threads all shut down
        EXIT_THREADS = True
    finally:
        EXIT_THREADS = True
