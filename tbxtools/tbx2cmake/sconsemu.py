# coding: utf-8

import os
import sys
import inspect
import copy
import glob
import contextlib
import fnmatch
import traceback
from collections import defaultdict

from enum import Enum

from .utils import InjectableModule, monkeypatched
from .import_env import do_import_patching

import logging
logger = logging.getLogger(__name__)

class ProgramReturn(object):
  """Thin shim to represent the return from a Program builder.

  AFAICT this is only used once in the SConscripts, to find out the
  location of a target that has just been built, so doesn't need to be
  any more complicated than this."""
  def __init__(self, path):
    self.path = path
  def get_abspath(self):
    return self.path

class SConsConfigurationContext(object):
  """Represents the object returned by a Scons Environment's 'Configure'.

  This is used to run tests inside a configured environment to e.g. test if
  sample programs will compile and run with the environment configured in a
  certain way. Here we just short-circuit the answers by working out what parts
  of the code is doing the testing.
  """

  def __init__(self, env):
    self.env = env

  def TryRun(self, code, **kwargs):
    # Only for darwin; now we fake linux
    # if "__GNUC_PATCHLEVEL__" in code:
    #   # We are trying to extract compiler information. Just return constant and
    #   # we can change it later if this fucks up something.
    #   data = {"llvm":1, "clang":1, "clang_major":8, "clang_minor":1, 
    #           "clang_patchlevel":0, "GNUC":4, "GNUC_MINOR":2, 
    #           "GNUC_PATCHLEVEL":1, "clang_version": "8.1.0 (clang-802.0.42)", 
    #           "VERSION": "4.2.1 Compatible Apple LLVM 8.1.0 (clang-802.0.42)"}
    #   return (1, repr(data))

    # Get the name of the calling function
    with no_intercept_os():
      caller = inspect.stack()[1][3]
    
    # Yes, openMP works as far as libtbx configuration is concerned
    if caller == "enable_openmp_if_possible":
      return (1,"e=2.71828, pi=3.14159")
    # This writes out a file with information on size type equivalence.
    if caller == "write_type_id_eq_h":
      # This is what the mac returns, but we handle this already anyway
      return (1, "0010")
    # Tests to see if we can include the openGL headers
    if "gltbx/include_opengl.h" in code:
      return (1, "6912")

    assert False, "Unable to determine purpose of TryRun"

  def TryCompile(self, code, **kwargs):
    # This tests something to do with an old boost/clang bug apparently.
    # Assume this is long past and we no longer need the workaround. (certainly
    # anyone using clang is probably using something much more modern than our
    # average supported GCC installation)
    # if code == """\
    #   #include <boost/thread.hpp>

    #   struct callable { void operator()(){} };
    #   void whatever() {
    #     callable f;
    #     boost::thread t(f);
    #   }
    #   """:
    #   return 1


    # This appears.... to test that a compiler actually works.
    if code == "#include <iostream>":
      return 1
    # Is Python available?
    elif code == "#include <Python.h>":
      return 1
    # A second check of openGL inclusion
    elif code.strip() == "#include <gltbx/include_opengl.h>":
      return 1
    # Looks to see if the fftw3 library is importable
    elif code == "#include <fftw3.h>":
      return 1

    assert False, "Not recognised TryCompile"

  def Finish(self):
    """Closes a configuration context. Nullop here."""
    pass

class SharedObject(object):
  "Represents a shared object file that is compiled once and shared"
  def __init__(self, path, environment):
    self.path = path
    self.environment = environment
  def __repr__(self):
    return "<SharedObject {}>".format(self.path)
  def __iter__(self):
    return iter([self.path])

class SConsEnvironment(object):
  """Represents an object created by the scons Environment() call.

  Needs to be constructed separately so that it can be tracked by the
  SCons-emulation environment.
  """
  _DEFAULT_KWARGS = {
    "OBJSUFFIX": ".o",
    "SHLINKFLAGS": [],
    "BUILDERS": {},
    "SHLINKCOM": ["SHLINKCOMDEFAULT"],
    "LINKCOM": ["LINKCOMDEFAULT"],
    "CCFLAGS": [],
    "SHCCFLAGS": [],
    "CXXFLAGS": [],
    "SHCXXFLAGS": [],
    "PROGPREFIX": "",
    "PROGSUFFIX": "",
    "LIBPREFIX": "lib",
    "SHLIBPREFIX": "lib",
    "LIBS": [],
    "CPPPATH": [],
  }

  def __init__(self, emulator_environment, *args, **kwargs):
    self.runner = emulator_environment
    # self.parent = None
    self.args = args
    self.kwargs = copy.deepcopy(kwargs)
    for key in self.kwargs:
      self._update(key)

  def _update(self, key):
    pass

  def Append(self, **kwargs):
    for key, val in kwargs.items():
      if isinstance(val, basestring):
        val = [val]
      if not key in self.kwargs:
        self.kwargs[key] = []
      self.kwargs[key].extend(val)
      self._update(key)

  def Prepend(self, **kwargs):
    for key, val in kwargs.items():
      if isinstance(val, basestring):
        val = [val]
      if not key in self.kwargs:
        self.kwargs[key] = []
      self.kwargs[key][:0] = val
      self._update(key)

  def Replace(self, **kwargs):
    self.kwargs.update(kwargs)

  def Configure(self):
    return SConsConfigurationContext(self)

  def Clone(self, **kwargs):
    clone = type(self)(self.runner, **self.kwargs)
    clone.kwargs.update(kwargs)
    # clone.parent = self
    return clone

  # Some parts rely on old APIs and were never updated
  Copy = Clone

  def __setitem__(self, key, value):
    self.kwargs[key] = value
    self._update(key)

  def __getitem__(self, key):
    # Check the defaults first, so we only write to kwargs what isn't explicit
    if not key in self.kwargs:
      return self._DEFAULT_KWARGS[key]
    return self.kwargs[key]

  def __contains__(self, key):
    return self.has_key(key)

  def has_key(self, key):
    return key in self.kwargs or key in self._DEFAULT_KWARGS

  def Repository(self, path):
    self.Append(REPOSITORIES=path)

  def SConscript(self, name, exports=None):
    """Sometimes, sub-SConscripts are called from an environment. Appears to behave the same."""
    self.runner.sconscript_command(name, exports)

  def _create_target(self, targettype, target, source, **kwargs):
    """Gathers target information from the environment at the point of creation"""
    if isinstance(source, basestring):
      source = [source]
    if target.startswith("#lib"):
      target = "#/lib" + target[4:]
    target = Target(targettype, output_name=target, sources=source)
    target.origin_path = os.path.dirname(os.path.relpath(self.runner._current_sconscript, self.runner.dist_path))

    target.env = self.Clone()
    target.env.Append(**kwargs)

    # Massage lib list to flatten any odd sublists etc
    libs = set()
    for lib in target.env["LIBS"]:
      if isinstance(lib, basestring):
        libs.add(lib)
      elif isinstance(lib, list):
        libs |= set(lib)
      else:
        assert False

    # Now let's filter/reduce the libs set. We know:
    # - Everything gets boost_thread, boost_system if threading is available, so no special required.
    # - Everything gets lm in SCons, unnecessary to track as universal (and automatic in clang?)
    libs -= {"boost_thread", "boost_system", "m"}
    target.extra_libs = libs

    # Handle link flags
    linkflags = list(target.env["SHLINKFLAGS"])
    known_ignore_flags = {"-fopenmp", "-shared", "-rdynamic"}
    for flag in known_ignore_flags:
      while flag in linkflags:
        linkflags.remove(flag)
    assert not linkflags, "Unknown link flag: {}".format(linkflags)
    if linkflags:
      logger.debug("Unhandled link flags: ", linkflags)

    # Handle include directories.
    # import pdb
    # pdb.set_trace()
    if "CPPPATH" in target.env.kwargs:
      # Remove things we expect
      COMMON_INCLUDES = {
        ".",
        "DISTPATH",
        "PYTHON/INCLUDE/PATH",
        "UNDERBUILD/include",
        "DISTPATH/boost",
        "REPOSITORIES",
        "BASEDIR/include"
        }
      extra_paths = set(target.env["CPPPATH"]) - COMMON_INCLUDES
      if extra_paths:
        logger.debug ("Path: {}".format(sorted(extra_paths)))

    if targettype == Target.Type.SHARED:
      target.prefix = target.env["SHLIBPREFIX"]
    elif targettype == Target.Type.STATIC:
      target.prefix = target.env["LIBPREFIX"]

    target.module = self.runner._current_module
    target.module.targets.append(target)
    logger.debug(str(target))


    self.runner.targets.append(target)
    return target

  def SharedLibrary(self, target, source, **kwargs):
    self._create_target(Target.Type.SHARED, target, source, **kwargs)

    # Target(Target.Type.SHARED, environment=self, output_name=target, sources=source, **kwargs)
    # print("Shared lib: {} (relative to {})\n     sources: {}".format(target, self.runner._current_sconscript, source))

  def StaticLibrary(self, target, source, **kwargs):
    self._create_target(Target.Type.STATIC, target, source, **kwargs)

    # print("Static lib: {} (relative to {})\n     sources: {}".format(target, self.runner._current_sconscript, source))

  def Program(self, target, source):
    self._create_target(Target.Type.PROGRAM, target, source)
    # print("Program: {} (relative to {})\n     sources: {}".format(target, self.runner._current_sconscript, source))
    # Used at least once
    return [ProgramReturn(target)]

  def cudaSharedLibrary(self, target, source):
    target = self._create_target(Target.Type.CUDALIB, target, source)
    # print("CUDA program: {}, {}".format(target, source))

  def SharedObject(self,source):
    logger.debug("Shared object: {}".format(source))
    return SharedObject(source, self)






class Target(object):
  """Represents an output target, extracted information independent of SCons"""
  class Type(Enum):
    PROGRAM = "Program"
    SHARED  = "Shared"
    STATIC  = "Static"
    MODULE  = "Module"
    CUDALIB = "CUDALib"

  def __init__(self, targettype, output_name, sources):
    assert targettype in self.Type
    self.type = targettype

    self.name = os.path.basename(output_name)
    self.filename = self.name
    self.output_path = os.path.dirname(output_name)

    # self.output_name = output_name
    self.sources = [x for x in sources if not isinstance(x, SharedObject)]
    self.shared_sources = [x for x in sources if isinstance(x, SharedObject)]
    self.generated_sources = set()
    self.extra_libs = set()
    self.prefix = ""
    # The path the target was "created" from
    self.origin_path = ""
    self.module = None
    self.include_paths = set()

    # self.required_optional_extra_libs = set()
    # Extra libs that are used if present, but optional
    self.optional_extra_libs = set()

  @property
  def output_filename(self):
    return self.prefix + self.filename

  def __repr__(self):
    return "<Target {}:{}>".format(self.origin_path, self.name)

  def __str__(self):
    out = ""
    out += "{} Target {}:\n".format(self.type.value, self.name)
    out += "   Output:  {}\n".format(os.path.join(self.output_path, self.output_filename))
    out += "   Sources: {}\n".format(", ".join(self.sources))
    if self.shared_sources:
      out += "   SharedObjects: {}\n".format(self.shared_sources)
    if self.extra_libs:
      out += "   Libs: {}\n".format(", ".join(self.extra_libs))
    out += "   Origin: {}\n".format(self.origin_path)
    if self.module:
      out += "   Module: {}\n".format(self.module)

    return out.strip()


class _SConsBuilder(object):
  def __init__(self, action, **kwargs):
    self.action = action
    self.kwargs = kwargs
    self.builders = []
  def add_src_builder(self, builder):
    self.builders.append(builder)

class _fakeFile(object):
  """A fake file interface to return false data from an overridden open"""
  def __init__(self, filename):
    self.filename = filename
    self.data = ""

  def write(self, data):
    self.data += data

  def read(self):
    with no_intercept_os():
      caller = inspect.stack()[1][3]
    if "csymlib.c" in self.filename or caller == "replace_printf":
      return ""

def _wrappedOpen(file, mode=None):
  """A Fake open command to trap reading files in SConscripts"""
  return _fakeFile(file)


@contextlib.contextmanager
def no_intercept_os():
  "Contextmanager to temporarily suspend the OS interception checks"
  with _fake_system_env.current.suspend():
    yield

class _undo_fake_system_env(object):
  """Created from a fake env, context manager to suspend behaviour"""
  def __init__(self, original):
    self._orig = original

  def __enter__(self):
    self._orig.__exit__(None,None,None)

  def __exit__(self, *args):
    self._orig.__enter__()

class _fake_system_env(object):
  current = None
  def __init__(self, env):
    # self._os = {}
    # self._ospath = {}
    # self._sys = {}
    self.env = env
    self._orig = defaultdict(dict)

  _to_rewrite = {
    os: {"mkdir", "name"},
    os.path: {"isdir", "isfile", "exists"},
    sys: {"platform"}
  }

  def suspend(self):
    return _undo_fake_system_env(self)

  def __enter__(self):
    assert _fake_system_env.current is None
    # print("Entering fake env")
    # traceback.print_stack()
    _fake_system_env.current = self
    for module, names in self._to_rewrite.items():
      for name in names:
        self._orig[module][name] = getattr(module, name)
        setattr(module, name, getattr(self, "_fake_{}".format(name)))

    # for name in {"mkdir", "name"}:
    #   self._os[name] = getattr(os, name)
    #   setattr(os, name, getattr(self, "_fake_{}".format(name)))
    
    # for name in {"isdir", "isfile", "exists"}:
    #   self._ospath[name] = getattr(os.path, name)
    #   setattr(os.path, name, getattr(self, "_fake_{}".format(name)))

    # for name in {"isdir", "isfile", "exists"}:
    #   self._ospath[name] = getattr(os.path, name)
    #   setattr(os.path, name, getattr(self, "_fake_{}".format(name)))


  def __exit__ (self, type, value, tb):
    # print("Exiting fake env")
    # traceback.print_stack()
    for module, entries in self._orig.items():
      for name, value in entries.items():
        setattr(module, name, value)
    _fake_system_env.current = None

  _fake_name = "posix"
  _fake_platform = "linux2"
  

  def _fake_mkdir(self, path, mode):
    assert path.startswith("UNDERBUILD")

  def _fake_isdir(self, path):
    if path.startswith("UNDERBUILD"):
      return True
    if path.endswith("eigen"):
      return True
    if path == "DISTPATH/boost/boost/system":
      return True

    logger.debug("IS DIR: {}".format(path))
    # Everything exists for sconsscripts!
    # allowed_exists = {,}
    return True

  def _fake_isfile(self, file):
    # Looking for ccp4io printf rewriting...
    if file.startswith("DISTPATH/ccp4io/libccp4/ccp4"):
      return False

    logger.debug("IS FILE: {}".format(file))
    logger.debug("".join(traceback.format_stack()))

    with self.suspend():
      # If given a special location, try to find it
      if file.startswith("DISTPATH["):
        module = file[9:file.find("]")]
        # Find this module in our distpath
        for repo in [".", "cctbx_project"]:
          path = os.path.join(self.env.dist_path, repo, module)      
          if os.path.isdir(path):
            file = path + file[len(module)+10:]
      elif file.startswith("DISTPATH"):
        file = os.path.join(self.env.dist_path, file[9:])
      logger.debug("Out: {}".format(file))

      if os.path.isfile(file):
        logger.debug("  YES")
        return True
      else:
        logger.debug("  NO")
        return False
      return os.path.isfile(file)


  def _fake_exists(self, path):
    logger.debug("EXISTS: {}".format(path))
    logger.debug("".join(traceback.format_stack()))
    return self._orig[os.path]["exists"](path)  

class SconsEmulator(object):
  def __init__(self, dist):#, modules):
    self._exports = {}
    self._current_sconscript = None
    self._current_module = None

    self.dist_path = dist
    # self.module_map = modules

    self.targets = []

    do_import_patching(dist)


  def parse_module(self, module):

    self._current_module = module
    scons = os.path.join(self.dist_path, module.path, "SConscript")
    if not os.path.isfile(scons):
      logger.debug("No Sconscript for module {}".format(module.name))
      return
    logger.info("Parsing {}".format(module.name))

    self._fake_env = _fake_system_env(self)
    with self._fake_env:
      self.parse_sconscript(scons)

  def sconscript_command(self, name, exports=None):
    newpath = os.path.join(os.path.dirname(self._current_sconscript), name)
    logger.debug("Loading sub-sconscript {}".format(newpath))
    self.parse_sconscript(newpath, custom_exports=exports)
    logger.debug("Returning to sconscript {}".format(self._current_sconscript))

  def parse_sconscript(self, filename, custom_exports=None):
    # Build the object used to run the script
    module = InjectableModule(filename)

    # Build the Scons injection environment
    def _env_export(*args):
      logger.debug("Exporting {}".format(args))
      for name in args:
        self._exports[name] = module.getvar(name)
    def _env_import(*args):
      logger.debug("Importing {}".format(args))
      inj = {}
      for imp in args:
        if custom_exports and imp in custom_exports:
          inj[imp] = custom_exports[imp]
        else:
          inj[imp] = self._exports[imp]
      module.inject(inj)

    def _env_glob(path):
      globpath = os.path.join(os.path.dirname(filename), path)
      results = glob.glob(globpath)
      ldir = len(os.path.dirname(filename))
      return [x[ldir+1:] for x in results]

    def _new_env(*args, **kwargs):
      return SConsEnvironment(self, *args, **kwargs)

    inj = {
      "Environment": _new_env,
      "open": _wrappedOpen,
      "ARGUMENTS": {},
      "Builder": _SConsBuilder,
      "Export": _env_export,
      "Import": _env_import,
      "SConscript": self.sconscript_command,
      "Glob": _env_glob,
    }
    # Inject this
    module.inject(inj)
    # Handle the stack of Sconscript processing
    prev_scons = self._current_sconscript
    self._current_sconscript = filename
    # Now execute the script
    module.execute()
    self._current_sconscript = prev_scons
