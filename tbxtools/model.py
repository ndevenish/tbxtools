# coding: utf-8

"""
Contains the model objects for a tbx-like distribution
"""
#!/usr/bin/env python
# coding: utf-8

"""
Create a libtbx environment WITHOUT having to import libtbx
"""

import os
import ast
import logging

logger = logging.getLogger(__name__)

class Module(object):
  """
  Represents a module in a tbx-like distribution.

  A module is literally a folder, but can contain optional configuration
  files. Thus, the only way to classify a module is by request - it it's 
  requested as a module, and exists as a folder, it is one.
  """

  def __init__(self, path, dist):
    """
    :param path: The path to the module, relative to distribution root
    :param dist:      The distribution this module belongs to
    """
    self.name = os.path.basename(path)
    self.path = path
    self.dist = dist

    # A dictionary of configuration files written to libtbx_config
    self.config = {
      'modules_required_for_use': [],
      'modules_required_for_build': [],
      'optional_modules': [],
      'exclude_from_binary_bundle': [],
      # Extra paths (other than ".") to look for command_line subfolders
      'extra_command_line_locations': [],
    }

    # Load information about this module from the libtbx_config... if there is one
    config_filename = os.path.join(dist.path, self.path, "libtbx_config")
    if os.path.isfile(config_filename):
      with open(config_filename) as f:
        # Unfortunately, these files aren't json but some custom-written
        # python dictionary syntax (e.g. json + trailing commas)
        for key, value in ast.literal_eval(f.read()).items():
          if not key in self.config:
            logger.warning("Unknown libtbx_config key {} in module {}".format(key, self.name))
          self.config[key] = value

  # @property
  # def dependencies(self):
  #   "Returns a list of modules this module depends on/uses"

# {'dist_paths': ["/Users/nickd/dials/dist/modules/cctbx_project/iota",
                  #                None],
                  # 'env': '__selfreference__',
                  # 'exclude_from_binary_bundle': [],
                  # 'extra_command_line_locations': [],
                  # 'mate_suffix': 'adaptbx',
                  # 'name': 'iota',
                  # 'names': ['iota', 'iota_adaptbx'],
                  # 'optional': [],
                  # 'python_paths': [],
                  # 'required_for_build': [],
                  # 'required_for_use': ['xfel']}

class DependencyError(RuntimeError):
  pass

class Distribution(object):
  # Paths to search for modules within. This handles the cctbx_project subdirectory
  repositories = {".", "cctbx_project"}

  def __init__(self, module_path, build_path=None):
    self.path = module_path
    self.build_path = build_path
    self._modules = {}
    self._requested_modules = set()

  def _find_module_dir(self, name):
    "Search the distribution for a path matching a module name"
    for repo in self.repositories:
      if os.path.isdir(os.path.join(self.path, repo, name)):
        return os.path.normpath(os.path.join(repo, name))

  def _load_dependencies_for(self, module):
    "Ensure the dependencies for a given module are loaded"
    # Assume that build/use requirements are both absolute and any missing
    # will cause failure of configuration.
    abs_deps = set(module.config["modules_required_for_build"]) | \
               set(module.config["modules_required_for_use"])
    for dep_name in abs_deps:
      if self.load_module(dep_name) is None:
        raise DependencyError("Cannot find module {} (required by {})".format(dep_name, module.name))
    # Just try loading optional dependencies without worrying about the result
    for dep_name in module.config["optional_modules"]:
      try:
        self.find_module(dep_name)
      except DependencyError:
        pass

  def load_module(self, name):
    """Search the distribution module paths for a module, and load it.

    :param str name:  The name of the module to retrieve
    :returns:         A Module object if found, or None
    """
    # If we've already loaded this module, just return it
    if name in self._modules:
      return self._modules[name]
    # Look in all the repositories...
    module_path = self._find_module_dir(name)
    if not module_path:
      return None
    module = Module(path=module_path, dist=self)
    # Now try loading any dependency requirements...
    # We need to add to the modules data to avoid problems with dependency
    # loops, but reverse in case of a dependency load failure down the line.
    try:
      self._modules[name] = module
      self._load_dependencies_for(module)
    except DependencyError:
      # We errored whilst loading this module. Remove it
      del self._modules[name]
      raise

    # All done!
    return module

  def request_modules(self, names):
    """Load a list of modules, but also mark them as specifically requested."""
    self._requested_modules |= set(names)
    for modulename in names:
      if self.load_module(modulename) is None:
        raise DependencyError("Could not find requested module {}".format(modulename))

  @property
  def modules(self):
    return self._modules.items()

  def __getitem__(self, name):
    return self._modules[name]

  def __contains__(self, name):
    return name in self._modules

