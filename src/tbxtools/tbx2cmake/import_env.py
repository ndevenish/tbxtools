# coding: utf-8

"""
Prepares the import environment for running the tbx SCons scripts
"""

import logging
import os
import re
import sys
from types import ModuleType

# from .sconsemu import no_intercept_os
from .intercept import no_intercept_os
from .utils import AttrDict

try:
    from unittest.mock import Mock
except ImportError:
    from mock import Mock


logger = logging.getLogger(__name__)


class MissingDistError(RuntimeError):
    pass


class FakePath(object):
    pass


class UnderBuild(FakePath):
    def __init__(self, path):
        self.path = path

    def __repr__(self):
        return "UnderBuild({})".format(repr(self.path))

    def __abs__(self):
        return os.path.join("UNDERBUILD", self.path)

    # def endswith(self, text):


class UnderBase(FakePath):
    def __init__(self, path):
        self.path = path

    def __repr__(self):
        return "UnderBuild({})".format(repr(self.path))

    def find(self, substr):
        return self.path.find(substr)


class libtbxBuildOptions(object):
    build_boost_python_extensions = True
    scan_boost = False
    compiler = "default"
    static_exe = False
    debug_symbols = True
    force_32bit = False
    warning_level = 0
    optimization = False
    use_environment_flags = False
    enable_cxx11 = False
    enable_openmp_if_possible = True
    enable_cuda = True
    enable_boost_threads = True
    boost_python_no_py_signatures = False
    precompile_headers = False
    boost_python_bool_int_strict = True  # Undocumented in boost::python and whether
    # anything depends on this is long lost.... but
    # since it's just a define keep in for parsing scons
    mode = "invalid"  # AFAICT this is only tested as mode == "profile" (linux only)
    static_libraries = False
    use_conda = False


class libtbxEnv(object):
    boost_version = 107200

    def __init__(self, dist_path):
        self.build_options = libtbxBuildOptions()
        self._dist_path = dist_path

    def under_build(self, path):
        return os.path.join("UNDERBUILD", path)  # UnderBuild(path)

    def under_base(self, path):
        return os.path.join("BASEDIR", path)  # UnderBase(path)

    def dist_path(self, module):
        logger.debug("Asked for dist path {} under {}".format(module, self._dist_path))

        with no_intercept_os():
            for repo in [".", "cctbx_project"]:
                path = os.path.normpath(os.path.join(self._dist_path, repo, module))
                if os.path.isdir(path):
                    logger.debug("  found exact {}".format(path))
                    # import pdb
                    # pdb.set_trace()
                    return path
        # With no_intercept_os we can now fall through to this. But we should
        # never be asked for the path of a module that doesn't exist - if we
        # do, we should probably return something but this is a relatively
        # untested path
        raise MissingDistError("Could not find dist path for module " + module)
        assert False, "Couldnt find real dist path"
        ret = "DISTPATH[{}]/".format(module)
        logger.debug("   returning {} ".format(ret))
        return ret

    def under_dist(self, module_name, path):
        return os.path.join("DISTPATH[{}]".format(module_name), path)

    @property
    def build_path(self):
        return "UNDERBUILD"

    @property
    def lib_path(self):
        # This returns a path object
        return UnderBuild("lib")

    def find_in_repositories(self, relative_path, **kwargs):
        return os.path.join("REPOSITORIES", relative_path)

    def has_module(self, module):
        return True

    def write_dispatcher_in_bin(self, source_file, target_file):
        logger.debug(
            "Called to write dispatcher {} to {}".format(target_file, source_file)
        )


class libtbxIncludeRegistry(list):
    def scan_boost(self, *args, **kwargs):
        return self

    def set_boost_dir_name(self, *args, **kwargs):
        return self

    def append(self, env, paths):
        # This function does some logic to prevent dependency scanning of boost
        # path building. Just ignore this as we are building boost externally.
        for path in paths:
            env.Append(CPPPATH=[path])

    def prepend(self, env, paths):
        # This function does some logic to prevent dependency scanning of boost
        # path building. Just ignore this as we are building boost externally.
        for path in paths:
            env.Prepend(CPPPATH=[path])


def new_module(name, doc=None):
    """Create a new module and inject it into sys.modules.

  :param name: Fully qualified name (including parent .)
  :returns:  A module, injected into sys.modules
  """
    m = ModuleType(name, doc)
    m.__file__ = name + ".py"
    sys.modules[name] = m
    return m


# Common functions
def _fail(*args, **kwargs):
    raise NotImplementedError("Not Implemented")


def _wtf(*args, **kwargs):
    assert False, "WTF? {}, {}".format(args, kwargs)


def _unique_paths(paths):
    return list(set(paths))


def _libtbx_select_matching(key, choices, default=None):
    # Too complex to try shortcutting the test; just replicate and catch results
    for key_pattern, value in choices:
        m = re.search(key_pattern, key)
        if m is not None:
            return value
    return default


def _tbx_darwin_shlinkcom(env_etc, env, lo, dylib):
    # Don't fully understand the purpose or intent of this functions - but
    # seems related to an old way of building shared libraries on OSX - that
    # used to require a custom link step. This function would set up the custom
    # link step on the environment object.
    if "libboost_thread.lo" in lo:
        return
    if "libboost_python.lo" in lo:
        return
    if "libboost_system.lo" in lo:
        return
    if "libboost_numpy.lo" in lo:
        return
    if "libboost_filesystem.lo" in lo:
        return
    _wtf(env_etc, env, lo, dylib)


class EasyRunResult(object):
    """Simple wrapper to pretend to be the output from an easy_run"""

    def __init__(self, output):
        self.stdout_lines = list(output)

    def raise_if_errors(self):
        return self


def _tbx_easyrun_fully_buffered(command, **kwargs):
    """Check what the caller was trying to run, and return pretend data"""
    # if command == "/usr/bin/uname -p":
    #   return EasyRunResult(["i386"])
    # elif command == "/usr/bin/sw_vers -productVersion":
    #   return EasyRunResult(["10.12.0"])
    if command == "nvcc --version":
        return EasyRunResult(["Cuda compilation tools, release 8.0, V8.0.61"])
    assert False, "No command known; {}".format(command)


# Only allow this to be done once, until we may add e.g. context-patching
_patching_done = False


def _get_gcc_version_50400(*args, **kwargs):
    return 50400


# getenv_bool(variable_name="LIBTBX_CPP0X", default=None)
def _getenv_bool(variable_name, default=False):
    results = {"LIBTBX_CPP0X": False}  # Should we add a c++0x flag
    assert variable_name in results, "Unknown getenv_bool {}".format(variable_name)
    return results[variable_name]


def do_import_patching(dist_path):
    # Only do this once
    global _patching_done
    if _patching_done:
        return

    # Create the libtbx environment
    libtbx = new_module("libtbx")
    libtbx.load_env = new_module("libtbx.load_env")
    libtbx.env_config = new_module("libtbx.env_config")
    libtbx.utils = new_module("libtbx.utils")
    libtbx.str_utils = new_module("libtbx.str_utils")
    libtbx.path = new_module("libtbx.path")

    libtbx.manual_date_stamp = 20090819  # I don't even
    libtbx.utils.getenv_bool = _getenv_bool
    libtbx.str_utils.show_string = _fail
    libtbx.path.norm_join = lambda a, b: os.path.normpath(os.path.join(a, b))
    libtbx.path.full_command_path = _fail
    libtbx.group_args = AttrDict

    libtbx.env_config.include_registry = libtbxIncludeRegistry
    libtbx.env_config.is_64bit_architecture = lambda: True
    libtbx.env_config.python_include_path = lambda: "PYTHON/INCLUDE/PATH"
    libtbx.env_config.unique_paths = _unique_paths
    libtbx.env_config.darwin_shlinkcom = _tbx_darwin_shlinkcom
    libtbx.env_config.get_gcc_version = _get_gcc_version_50400
    libtbx.env_config.get_boost_library_with_python_version = lambda m, _: m
    # def get_gcc_version(command_name="gcc"):

    libtbx.utils.select_matching = _libtbx_select_matching
    libtbx.utils.warn_if_unexpected_md5_hexdigest = Mock()
    libtbx.utils.write_this_is_auto_generated = Mock()
    libtbx.utils.Sorry = RuntimeError

    libtbx.env = libtbxEnv(dist_path)
    libtbx.easy_run = new_module("libtbx.easy_run")
    libtbx.easy_run.fully_buffered = _tbx_easyrun_fully_buffered

    # data module used during it's sconscript
    fftw3tbx = new_module("fftw3tbx")
    fftw3tbx.fftw3_h = "fftw3.h"

    # Occasionally we access some SCons API to do... something
    SCons = new_module("SCons")
    SCons.Action = new_module("SCons.Action")
    SCons.Scanner = new_module("SCons.Scanner")
    SCons.Scanner.C = new_module("SCons.Scanner.C")

    SCons.Action.FunctionAction = Mock()
    SCons.Scanner.C.CScanner = Mock()

    # Fake numpy so that we always get it's dependents
    numpy = new_module("numpy")
    numpy.get_include = lambda: "NUMPY_INCLUDE"

    # In order to avoid libtbx.env_etc dependency, fable's SConscript
    # now imports fable...
    fable = new_module("fable")
    fable.__path__ = "DISTPATH[fable]/"


# def monkeypatched(object, name, patch):
#   """ Temporarily monkeypatches an object. """
#   pre_patched_value = getattr(object, name)
#   setattr(object, name, patch)
#   yield object
#   setattr(object, name, pre_patched_value)
