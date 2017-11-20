# coding: utf-8

"""
Converts a TBX-distribution into a set of CMake scripts.

No root CMakeLists.txt will be created. Instead, an autogen-CMakeLists.txt
file will be created in the root directory that can be included by the root
CMakeLists.txt. Writing of this root may be added later.

Usage: tbx2cmake [--build-info=<infofile>] [-v | -vv] <module_dir> <output_dir>

Options:
-h, --help                Display this message
--build-info=<infofile>   The build information file, to supply extra info
                          about e.g. dependencies, generated file output. See
                          tbxtools/tbx2cmake/build_info.yaml for the defaults.
"""

import sys
import os
import logging
import pkgutil

from docopt import docopt
import yaml

from .utils import fully_split_path
from .read_scons import read_distribution
from .sconsemu import Target

logger = logging.getLogger()

# Renames from Scons-library targets to CMake names
DEPENDENCY_RENAMES = {
  "boost_python": "Boost::python",
  "tiff": "TIFF::TIFF",
  "GL": "OpenGL::GL",
  "GLU": "OpenGL::GLU",
  "boost_thread": "Boost::thread",
  "hdf5_c": "HDF5::C",
  "hdf5": "HDF5::HDF5",
  "hdf5_hl": "HDF5::HL",
  "boost": "Boost::boost",
  "boost_numpy": "Boost::numpy",
  "eigen": "Eigen::Eigen",
  "openmp": "OpenMP::OpenMP_CXX",
  "pcre": "PCRE::PCRE",
  "numpy": "Python::Numpy",
  "png": "PNG::PNG"
}

# Global optional dependencies - unless a module/target has an explicit
# external dependency listed for these, the target will be always added
# and then an extra test added for linking in these. Filled from the
# build_info.yaml:optional_dependencies.all field.
OPTIONAL_DEPENDS = set()

_warned_types = set()

class CMakeLists(object):
  "Represents a single CMakeLists file. Keeps track of subdirectories."

  def __init__(self, path="", parent=None):
    self.path = path
    self.subdirectories = {}
    self.parent = parent

    self.is_module_root = False
    self.targets = []
    self._module = None

  def get_path(self, path):
    "Returns a CMakeLists object for a specific subpath"
    assert not os.path.isabs(path)
    parts = fully_split_path(path)
    assert not ".." in parts, "No relative referencing implemented"
    if parts[0] in {"", "."}:
      return self
    else:
      if not parts[0] in self.subdirectories:
        subdir = CMakeLists(parts[0], parent=self)
        self.subdirectories[parts[0]] = subdir
      else:
        subdir = self.subdirectories[parts[0]]
      if len(parts) > 1:
        return subdir.get_path(os.path.join(*parts[1:]))
      else:
        return subdir

  def draw_tree(self, indent="", last=True, root=True):
    "Quick and easy function to dump a tree representation"""
    line = indent
    if not root:
      if last:
        line += " └"
        indent += "  "
      else:
        line += " ├"
        indent += " │"
    if not self.parent:
      line += " ROOT"
    else:
      line += " " + self.path
    print(line.ljust(25-len(self.path)))# + " ({} targets)".format(len(self.targets)))
    for i, child in enumerate(sorted(self.subdirectories.values(), key=lambda x: x.path)):
      child.draw_tree(indent, i == len(self.subdirectories) - 1, root=False)

  def all(self):
    yield self
    for child in self.subdirectories.values():
      for result in child.all():
        yield result

  @property
  def full_path(self):
    if self.parent:
      return os.path.join(self.parent.full_path, self.path)
    else:
      return self.path

  @property
  def module(self):
    if self._module:
      return self._module
    elif self.parent:
      return self.parent.module
    else:
      return None

  def __repr__(self):
    return "<CMakeLists {}>".format(self.full_path)

  def generate_cmakelist(self):
    blocks = []

    if self.is_module_root:
      blocks.append(CMLModuleRootBlock(self))

    if self.targets:
      for target in self.targets:
        if target.name == self.module.name:
          # Handled separately
          continue
        if target.type in {Target.Type.SHARED, Target.Type.STATIC, Target.Type.MODULE}:
          blocks.append(CMLLibraryOutput(target))
        else:
          # Warn about target types not yet handled
          if not target.type in _warned_types:
            _warned_types.add(target.type)
            logger.warn("Not handling {} yet".format(target.type))

    if self.subdirectories:
      blocks.append(CMLSubDirBlock(self))

    return "\n\n".join(str(x) for x in blocks) + "\n"

class CMakeListBlock(object):
  def __init__(self, cmakelist):
    self.cml = cmakelist

class CMLSubDirBlock(CMakeListBlock):
  def __str__(self):
    lines = []
    for subdir in sorted(self.cml.subdirectories):
      lines.append("add_subdirectory({})".format(subdir))
    return "\n".join(lines)

def _expand_include_path(path):
  assert not path.startswith("!")
  if path.startswith("#base"):
    path = path.replace("#base", "${CMAKE_SOURCE_DIR}")
  elif path.startswith("#build"):
    path = path.replace("#build", "${CMAKE_BINARY_DIR}")
  else:
    path = "${CMAKE_CURRENT_SOURCE_DIR}/" + path
  return path

class CMLModuleRootBlock(CMakeListBlock):
  def __str__(self):
    module = self.cml.module
    lines = []
    lines.append("project({})".format(self.cml.module.name))
    lines.append("")

    # Decide what kind of library we are
    module_target = [x for x in self.cml.targets if x.name == self.cml.module.name]
    assert len(module_target) <= 1
    if module_target:
      # We are a real, compiled library
      lines.append(str(CMLLibraryOutput(module_target[0])))
    else:
      # We're just an interface library
      lines.append("add_library( {} INTERFACE )".format(module.name))
      include_paths = {"${CMAKE_CURRENT_SOURCE_DIR}/.."}

      # Handle any replacements in this path
      for path in module.include_paths:
        assert not path.startswith("!"), "No private includes for interface libraries"
        path = _expand_include_path(path)
        include_paths.add(path)
      linepre = "target_include_directories( {} INTERFACE ".format(module.name)

      lines.append(_append_list_to(linepre, include_paths, append=(" )", "\n)")))

    # Write out the libtbx refresh generator, along with the sources it creates
    if self.cml.module.generated_sources:
      lines.append("")
      lines.append("add_libtbx_refresh_command( ${CMAKE_CURRENT_SOURCE_DIR}/libtbx_refresh.py")

      slines = []
      for source in sorted(self.cml.module.generated_sources):
        indent = "            "
        if not slines:
          indent = "     OUTPUT "
        slines.append(indent + "${CMAKE_BINARY_DIR}/" + source)
      slines.append(")")
      lines.extend(slines)

    return "\n".join(lines)

def _append_list_to(line, list, join=" ", indent=4, append=("",""), firstindent=None):
  """
  Appends a list to a line, either inline or as separate lines depending on length.

  :param join:   The string to join between or at the end of entries
  :param indent: How far to indent if using separate lines
  :param append: What to append. A tuple of (inline, split) postfixes
  """
  if firstindent is None:
    firstindent = " " * indent

  if len(line + join.join(list)) + 2 <= 78:
    return line + join.join(list) + append[0]
  else:
    joiner = join.strip() + "\n" + " "*indent
    return line + "\n" + firstindent + joiner.join(list) + append[1]

class CMLLibraryOutput(CMakeListBlock):
  def __init__(self, target):
    self.target = target

  @property
  def typename(self):
    if self.target.type == self.target.Type.MODULE:
      return "MODULE"
    elif self.target.type == self.target.Type.SHARED:
      return "SHARED"
    elif self.target.type == self.target.Type.STATIC:
      return "STATIC"

  @property
  def is_python_module(self):
    return self.target.type == Target.Type.MODULE and "boost_python" in self.target.extra_libs

  def __str__(self):
    # if self.target.name == "cctbx_array_family_flex_ext":
    #   import pdb
    #   pdb.set_trace()

    if self.is_python_module:
      add_command = "add_python_library( {} "
    else:
      add_command = "add_library( {} {} "

    add_lib = add_command.format(self.target.name, self.typename)

    # Work out if we can put all the sources on one line
    lines = []

    lines.append(_append_list_to(add_lib, self.target.sources, append=(" )", " )")))

    # lines.extend()
    # if len(add_lib + " ".join(self.target.sources)) + 2 <= 78:
    #   lines.append(add_lib + " ".join(self.target.sources) + " )")
    # else:
    #   lines.append(add_lib)
    #   lines.extend(["    " + x for x in self.target.sources])
    #   lines.append(")")

    assert self.target.name == self.target.filename

    # Add generated sources
    if self.target.generated_sources:
      addgen = "add_generated_sources( {} ".format(self.target.name)
      lines.append(_append_list_to(addgen, self.target.generated_sources, append=(" )", " )")))

    # If we have custom include directories, add them now
    if self.target.include_paths:
      include_public = []
      include_private = []
      for path in self.target.include_paths:
        pathtype = include_public
        if path.startswith("!"):
          path = path[1:]
          pathtype = include_private

        path = _expand_include_path(path)
        pathtype.append(path)

      inclines = ["target_include_directories( {} ".format(self.target.name)]
      if include_public:
        inclines.append("    PUBLIC  " + "\n            ".join(include_public))
      if include_private:
        inclines.append("    PRIVATE " + "\n            ".join(include_private))
        # inclines.append(_append_list_to("    PRIVATE ", include_private))
      lines.append("\n".join(inclines) + " )")

    extra_libs = self.target.extra_libs - OPTIONAL_DEPENDS - self.target.optional_extra_libs
    if self.is_python_module:
      extra_libs = extra_libs - {"boost_python"}
    else:
      extra_libs |= {"boost"}
    if extra_libs:
      lines.append("target_link_libraries( {} {} )".format(self.target.name, " ".join(_target_rename(x) for x in extra_libs)))

    # Handle any optional dependencies
    optionals = (OPTIONAL_DEPENDS & set(self.target.extra_libs)) | self.target.optional_extra_libs
    if optionals:
      for option in optionals:
        lines.extend([
          "",
          "# Optional dependency on {}".format(option),
          "if(TARGET {})".format(_target_rename(option)),
          "  target_link_libraries({} {})".format(self.target.name, _target_rename(option)),
          "endif()"])

    # OLD optional handling; we may still want to enable/disable targets in the future
    # based on availability of some other library/target
      # # Ensure we have properly split lines before indenting
      # lines = "\n".join(lines).splitlines()
      # # cond_lines = []
      # conditions = " AND ".join(("TARGET {}".format(_target_rename(x)) for x in optionals))
      # cond_lines = ["if({})".format(conditions)]
      # cond_lines.extend("  " + x for x in lines)
      # cond_lines.append("endif()")
      # lines = cond_lines

    return "\n".join(lines)

def _read_autogen_information(filename, tbx):
  "Read a build information override file and apply to a distribution"

  if filename:
    with open(filename) as f:
      data = yaml.load(f)
  else:
    data = yaml.load(pkgutil.get_data("tbxtools.tbx2cmake", "build_info.yaml"))

  # Load the list of module-refresh-generated files
  for modname, value in data.get("libtbx_refresh", {}).items():
    module = tbx.modules[modname]
    module.generated_sources.extend(value)

  # Add the generated sources information
  tbx.other_generated = data.get("other_generated", [])

  # Find all targets that use repository-lookup sources
  for target in tbx.targets:
    lookup_sources = [x for x in target.sources if x.startswith("#")]
    unknown = set()
    for source in lookup_sources:
      # If the source is generated, then mark it so and it'll be read from the build dir
      if source[1:] in tbx.all_generated:
        target.sources.remove(source)
        target.generated_sources.add(source[1:])
      else:
        # This might be a general-lookup source. Find the actual directory.
        repositories = ["", "cctbx_project"]
        for repo in repositories:
          full_path = os.path.join(tbx.module_path, repo, source[1:])
          if os.path.isfile(full_path):
            # print("Found {} in {}".format(source, repo))
            # Change the sources list to use a relative reference to the target path
            target.sources.remove(source)
            relpath = os.path.relpath(os.path.join(repo, source[1:]), target.origin_path)
            target.sources.append(relpath)
            # print("  rewriting to {}".format(relpath))
            break
        # Did we find?
        if source in target.sources:
          unknown.add(source)

    if unknown:
      print("Unknown {} from {}: {}".format(target.name, target.origin_path, unknown))

  # Now, some of the sources are relative to "source or build" and so we need to
  # mark them as explicitly generated.
  for target in tbx.targets:
    for source in list(target.sources):
      if not os.path.isfile(os.path.join(tbx.module_path, target.origin_path, source)):
        # print("Could not find {}:{}".format(target.name, source))
        # Look in the generated sources list
        relpath = os.path.relpath(target.origin_path, target.module.path)
        genpath = os.path.normpath(os.path.join(target.module.name, relpath, source))
        assert genpath in tbx.all_generated, "Could not find missing source {}:{}".format(target.name, source)
          # print("   Found generated at {}".format(genpath))
        target.sources.remove(source)
        target.generated_sources.add(genpath)

  # Double-check that we have no unknown lookup sources
  assert not unknown, "Unknown scons-repository sources: {}".format(unknown)

  # Warn about any targets with no normal sources
  for target in tbx.targets:
    if not target.sources:
      logger.warning("Target {}:{} has no non-generated sources".format(target.origin_path, target.name))


  def _expand_target_lib_list(target, liblist, values):
    """Generic function to add targets to a list(s) of targets.

    If there is duplication of information then a logger debug warning will be
    emitted.

    :param target:  The target to add values to
    :param liblist: The name(s) of lib lists to expand. `str` or `[str]`
    :param values:  The value(s) to add to the list(s). `str` or `[str]`
    """
    if isinstance(values, basestring):
      values = [values]
    if isinstance(liblist, basestring):
      liblist = [liblist]
    logger.debug("Adding {} to {}'s [{}]".format(values, target.name, ", ".join(liblist)))

    for listname in liblist:
      already_added = getattr(target, listname) & set(values)
      if already_added:
        logger.debug("  ... although {} are already on target.{}".format(", ".join(already_added), listname))
      setattr(target, listname, getattr(target, listname) | set(values))

  # Handle any forced dependencies (e.g. things we can't tell/can't tell easily from SCons)
  for name, deps in data.get("dependencies", {}).items():
    _expand_target_lib_list(tbx.targets[name], "extra_libs", deps)

  # Handle any optional dependencies
  for name, deps in data.get("optional_dependencies", {}).items():
    # If name is "all", then just expand the global list
    if name == "all":
      global OPTIONAL_DEPENDS
      OPTIONAL_DEPENDS |= set(deps) if not isinstance(deps, basestring) else set([deps])
      logger.debug("Expanding global optional dependency list with: {}".format(deps))
    else:
      _expand_target_lib_list(tbx.targets[name], ["extra_libs", "optional_extra_libs"], deps)


  # Handle adding of include paths to specific targets/modules
  for name, incs in data.get("target_includes", {}).items():
    if isinstance(incs, basestring):
      incs = [incs]

    inc_target = None
    if name in tbx.targets:
      inc_target = tbx.targets[name]
    elif name in tbx.modules:
      inc_target = tbx.modules[name]
    else:
      logger.warning("No target/module named {} found; ignoring extra include paths".format(name))
    if inc_target:
      inc_target.include_paths |= set(incs)

def _target_rename(name):
  "Renames a target to the CMake target name, if required"
  return DEPENDENCY_RENAMES.get(name, name)

def main():

  options = docopt(__doc__)
  logging.basicConfig(level=logging.INFO if not options["-v"] else logging.DEBUG)
  module_dir = options["<module_dir>"]
  output_dir = options["<output_dir>"]
  autogen_file = options["--build-info"]



  # Validate the input values
  if not os.path.isdir(module_dir):
    print("Error: Module path {} must be a directory".format(module_dir))
    sys.exit(1)
  if os.path.isfile(output_dir):
    print("Error: Output path {} is a file. Please specify a directory or name of one to create.".format(options["<module_dir>"]))
    sys.exit(1)

  logger.info("Reading TBX distribution")
  tbx = read_distribution(module_dir)
  _read_autogen_information(autogen_file, tbx)

  logger.info("Read {} targets in {} modules".format(len(tbx.targets), len(tbx.modules)))

  # Start building the CMakeLists structure
  root = CMakeLists()

  for module in tbx.modules.values():
    modroot = root.get_path(module.path)
    modroot.is_module_root = True
    modroot._module = module

  for target in tbx.targets:
    cmakelist = root.get_path(target.origin_path)
    cmakelist.targets.append(target)

  # root.draw_tree()

  # Make sure the output path exists
  if not os.path.isdir(output_dir):
    os.makedirs(output_dir)

  for cml in root.all():
    path = os.path.join(output_dir, cml.full_path)
    if not os.path.isdir(path):
      os.makedirs(path)
    filename = "CMakeLists.txt"
    if cml is root:
      filename = "autogen_CMakeLists.txt"
    with open(os.path.join(path, filename), "w") as f:
      lines = cml.generate_cmakelist().splitlines()
      # Strip any trailing whitespace
      data = "\n".join(x.rstrip() for x in lines)
      f.write(data)


if __name__ == "__main__":
  sys.exit(main())