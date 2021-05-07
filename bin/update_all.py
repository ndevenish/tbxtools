#!/usr/bin/env python3

import time
from concurrent.futures import Future, ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from queue import Queue
from subprocess import (
    DEVNULL,
    PIPE,
    STDOUT,
    CalledProcessError,
    check_call,
    check_output,
)
from typing import Dict, List, NamedTuple

R = "\033[31m"
G = "\033[32m"
B = "\033[34m"
NC = "\033[0m"
UP_AND_CLEAR = "\033[F\033[2K"

MAX_CONCURRENT = 3


class TaskUpdate(NamedTuple):
    path: Path
    running: bool
    error: bool
    status: str


class Task(NamedTuple):
    kind: str
    path: Path


EXIT_THREADS = False


class Git(object):
    def __init__(self, repo):
        self.repo = repo

    def check_output(self, command):
        return check_output(
            ["git"] + list(command), cwd=self.repo, stderr=DEVNULL, encoding="utf-8"
        ).strip()

    def check_call(self, command):
        check_call(
            ["git"] + list(command), cwd=self.repo, stderr=DEVNULL, stdout=DEVNULL
        )

    def call(self, command):
        try:
            self.check_call(command)
        except CalledProcessError:
            return False
        else:
            return True

    def get_current_branch(self):
        return self.check_output(["rev-parse", "--abbrev-ref", "HEAD"])

    def get_main_branch(self):
        if self.call(["show-ref", "-q", "--verify", "refs/remotes/origin/main"]):
            # self._main = "main"
            return "main"
        # self._master = "master"
        return "master"

    def get_upstream_branch(self, branch):
        upstream = self.check_output(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{u}}"]
        )
        return upstream.strip().split("/", maxsplit=1)


def update_git_repo(path: Path, comms: Queue):
    def update(*args, running=True, error=False):
        """Convenience function for sending an update"""
        comms.put(TaskUpdate(path, running, error, " ".join(str(x) for x in args)))

    git = Git(path)
    # Error out if git-svn repository
    if (path / ".git" / "svn").is_dir():
        raise NotImplementedError("git-svn not yet implemented. Manually update.")

    main = git.get_main_branch()
    tracking_remote, tracking_branch = git.get_upstream_branch(main)
    update(f"main branch is {main}")
    update("upstream is", tracking_remote, tracking_branch)
    update_command = ["pull", "--ff-only", "--no-rebase", tracking_remote]

    # If not on main branch, then try to update it in the background
    if not git.get_current_branch() == main:
        try:
            update(f"Running fetch for background branch {main}")
            # time.sleep(5)
            if not git.call(["fetch", tracking_remote, f"{tracking_branch}:{main}"]):
                update(f"Not on {main} branch and could not update.")
            else:
                update("Success.")
        except CalledProcessError:
            update(f"Not on {main} branch, and could not update", error=True)
        return

    # Check if we have local changes
    if not git.call(["diff", "--no-ext-diff", "--quiet"]):
        # We have a dirty working area. Let's stash this and try anyway
        stash = git.check_output(["stash", "create"])
        git.check_call(["stash", "store", stash])
        original_commit = git.check_output(["rev-parse", "HEAD"])
        if not git.call(update_command):
            update(
                "Tried to smartly update dirty working directory but failed", error=True
            )
            git.check_call(["reset", "--hard", original_commit])
            git.check_call(["stash", "pop"])
            return
        update("Success.")
        return

    if not git.call(update_command):
        update("Failed to update.", error=True)
        return

    update("Success.")


def find_all_repos(path: Path) -> List[Task]:
    repos = []
    for subdir in path.glob("**/"):
        if (subdir / ".git").is_dir():
            repos.append(Task("git", subdir))
    return sorted(repos)


updaters = {"git": update_git_repo}
tasks = find_all_repos(Path.cwd())
paths = [t.path for t in tasks]

task_name_width = max(len(p.path.name) for p in tasks)

active_tasks: Dict[Path, Future] = {}
task_comms: "Queue[TaskUpdate]" = Queue()

# How many active tasks did we have last time
highest_task = 0
task_status = {}
with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
    try:
        # Submit all tasks at once
        print("Submitting")
        for kind, path in tasks:
            active_tasks[path] = pool.submit(updaters[kind], path, task_comms)
            task_status[path] = TaskUpdate(path, True, False, "Starting")

        print("Starting")
        while active_tasks:
            time.sleep(0.1)
            earliest_task_updated = highest_task + 1
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
                if path in active_tasks and not (
                    active_tasks[path].running() or active_tasks[path].done()
                ):
                    continue
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
                    f"{linecolour}{path.name+':':{task_name_width+1}} {colour}{status.status}{NC}\n"
                )
            print(update_message.getvalue(), flush=True, end="")

    except KeyboardInterrupt:
        EXIT_THREADS = True
    finally:
        EXIT_THREADS = True
