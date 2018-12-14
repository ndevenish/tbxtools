# coding: utf-8

import sys
import logging
import textwrap
import argparse
import collections

from .model import Distribution, Module
from .docopt import docopt

# List of modules to allow to be missing
# ALLOW_MISSING = {"boost"}

def run_expand_dependencies():
  """
  Expand a list of modules to include all dependencies.

  Modules can be passed in as individual items, but a CMake-style semicolon-
  separated list will also be accepted.

  Usage:  tbx-expand-deps [options] [--optional=MOD]... <distribution> [<module> [<module> ...]]
          tbx-expand-deps -h | --help

  Options:
    -h --help       Display this message
    --cmake         Return the output list in a semicolon-separated, cmake-style list
    --graphviz      Return a Graphviz dot-format directed graph
    -v, --verbose   Debugging output
    --optional MOD  Always treat a module as optional, even if normally required.
                    This can be used to handle otherwise missing modules.
  """

  options = docopt(textwrap.dedent(run_expand_dependencies.__doc__))
  logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if options["--verbose"] else logging.INFO)
  dist = Distribution(options["<distribution>"], ignore_missing=options["--optional"])

  # Read any modules (including ;-separated) into a set
  requested = set()
  for arg in options["<module>"]:
    if ";" in arg:
      requested |= set(arg.split(";"))
    else:
      requested.add(arg)

  # If actually asking for any, try to load them
  if requested:
    dist.request_modules(requested)

  # Work out any special formatting for primary nodes e.g. the only
  # modules that needed to be requested (all the others being derived)
  primary_format = "{}"
  if sys.stdout.isatty():
    primary_format = "\033[1m{}\033[0m"

  if options["--graphviz"]:
    print "digraph distribution {"
    for module in dist.modules:
      if module.name == "libtbx":
        continue
      for dependency in module.dependencies:
        if dependency.name == "libtbx":
          continue
        print("  {} -> {};".format(module.name, dependency.name))
      if not module.dependencies and not module.dependents:
        print("  {};".format(module.name))
    print("}")
 #     digraph graphname {
 #     a -> b -> c;
 #     b -> d;
 # }
  else:
    # Build a list of module names
    module_names = []
    for module in sorted(dist.modules, key=lambda x: x.name):
      fmts = primary_format if not module.dependents else "{}"
      # Special case is libtbx: we never need to request this
      # if module.name == "libtbx":
      #   fmts = "{}"
      module_names.append(fmts.format(module.name))

    # Now print out the results
    joiner = ";" if options["--cmake"] else " "

    # module_names = [x.name for x in dist.modules]
    print(joiner.join(module_names))
    # print(dist["xfel"].dependents)

