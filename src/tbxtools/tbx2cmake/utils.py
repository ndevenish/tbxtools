# coding: utf-8
from __future__ import annotations

import contextlib
import imp
import os


class AttrDict(dict):
    """Object that can access dictionary elements as keys or attributes"""

    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


def return_as_list(f):
    """Decorates a function to convert a generator to a list"""

    def _wrap(*args, **kwargs):
        return list(f(*args, **kwargs))

    return _wrap


def fully_split_path(path):
    "Splits a path until there is nothing left to split"
    parts = []
    head = path
    tail = path
    while head and tail:
        head, tail = os.path.split(head)
        parts.insert(0, tail)
    if head:
        parts.insert(0, head)
    return parts


class InjectableModule(object):
    """Load and run a python script with an injected globals dictionary.
    This is to emulate what it appears libtbx/scons does to run refresh scripts.
    Allows injecting whilst the module is running e.g. via callbacks.
    """

    def __init__(self, module_path):
        """Create an InjectableModule.

        :param pathlib.Path module_path: The python script to load
        """
        module = imp.new_module(module_path.stem)
        module.__file__ = str(module_path.parent)
        with module_path.open() as f:
            self.bytecode = compile(f.read(), str(module_path), "exec")
        self.module = module

    def inject(self, globals):
        vars(self.module).update(globals)

    def execute(self):
        exec(self.bytecode, vars(self.module))

    def getvar(self, name):
        """Return a variable from inside the module's globals"""
        return getattr(self.module, name)


@contextlib.contextmanager
def monkeypatched(object, name, patch):
    """Temporarily monkeypatches an object."""
    pre_patched_value = getattr(object, name)
    setattr(object, name, patch)
    yield object
    setattr(object, name, pre_patched_value)
