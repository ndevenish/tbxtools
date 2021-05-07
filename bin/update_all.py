#!/usr/bin/env python3

import random
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from queue import Queue
from typing import Dict, List, NamedTuple

R = "\033[31m"
G = "\033[32m"
B = "\033[34m"
NC = "\033[0m"
UP_AND_CLEAR = "\033[F\033[2K"

MAX_CONCURRENT = 10


class TaskUpdate(NamedTuple):
    path: Path
    running: bool
    error: bool
    status: str


class Task(NamedTuple):
    kind: str
    path: Path


EXIT_THREADS = False


def update_git_repo(path: Path, comms: Queue, exit_notice: Queue):
    # global EXIT_THREADS
    # comms.put(TaskUpdate(path, True, False, "Starting"))
    # Start with .git
    n = random.randint(4, 10)
    for i in range(n):
        # if not exit_notice.empty():
        #     return
        if EXIT_THREADS:
            return
        # print(path.name, i, EXIT_THREADS)
        comms.put(TaskUpdate(path, True, False, str(n - i)))
        time.sleep(1)
    if n >= 8:
        comms.put(TaskUpdate(path, False, False, "Completed"))
    else:
        comms.put(TaskUpdate(path, False, True, "Failed"))


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
exit_comms = Queue()

# How many active tasks did we have last time
highest_task = 0
task_status = {}
with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
    try:
        # Submit all tasks at once
        print("Submitting")
        for kind, path in tasks:
            active_tasks[path] = pool.submit(
                updaters[kind], path, task_comms, exit_comms
            )
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
                        task_status[path] = status._replace(running=False)
                        earliest_task_updated = min(
                            earliest_task_updated, paths.index(update.path)
                        )
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
