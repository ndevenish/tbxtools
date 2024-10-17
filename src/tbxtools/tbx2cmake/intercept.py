from __future__ import annotations

import contextlib
from collections import defaultdict


@contextlib.contextmanager
def no_intercept_os():
    "Contextmanager to temporarily suspend the OS interception checks"
    with SystemEnvInterceptor.current.suspend():
        yield


# sample intercept class
# class SomeSampleIntercept(object):
## List of things to rewrite
## Form:
## [ (module_name, { NAME, NAME } ), ... ]
# to_rewrite = [
#   (os, {"mkdir", "name", "path"}),
#   (posixpath, {"isdir", "isfile", "exists"}),
#   (sys, {"platform"})
# ]
# Class attributes will be looked up with the NAME e.g.
# def _fake_NAME(self, ...):
# _fake_NAME = something


class _undo_fake_system_env(object):
    """Created from a fake env, context manager to suspend behaviour"""

    def __init__(self, original):
        self._orig = original

    def __enter__(self):
        self._orig.__exit__(None, None, None)

    def __exit__(self, *args):
        self._orig.__enter__()


class SystemEnvInterceptor(object):
    current = None

    def __init__(self, intercept_source):
        self.details = intercept_source
        self._orig = defaultdict(dict)

    def __enter__(self):
        assert SystemEnvInterceptor.current is None
        # logger.debug("Entering fake OS environment")
        # traceback.print_stack()
        SystemEnvInterceptor.current = self

        for module, names in self.details.to_rewrite:
            for name in names:
                self._orig[module][name] = getattr(module, name)
                setattr(module, name, getattr(self.details, "_fake_{}".format(name)))
        self.details._orig = self._orig

    def __exit__(self, type, value, tb):
        # logger.debug("Exiting fake OS environment")
        # traceback.print_stack()
        self.details._orig = None
        # Reset these values in reverse order
        for module, names in reversed(self.details.to_rewrite):
            for name in names:
                value = self._orig[module][name]
                setattr(module, name, value)
        self._orig.clear()
        SystemEnvInterceptor.current = None

    def suspend(self):
        return _undo_fake_system_env(self)


# @contextlib.contextmanager
# def intercept_os():
#   "Contextmanager to intercept system OS checks"
