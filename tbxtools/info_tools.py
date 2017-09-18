# coding: utf-8

import sys
import logging
import textwrap
import argparse
import collections

from .model import Distribution
from .docopt import docopt

def run_expand_dependencies():
  """
  Expand a list of modules to include all dependencies.

  Modules can be passed in as individual items, but a CMake-style semicolon-
  separated list will also be accepted. 
  
  Usage:  tbx-expand-deps [options] <distribution> [<module> [<module> ...]]
          tbx-expand-deps -h | --help
  
  Options:
    -h --help      Display this message
    --cmake        Return the output list in a semicolon-separated, cmake-style list
    -v, --verbose  Debugging output
  """

  options = docopt(textwrap.dedent(run_expand_dependencies.__doc__))
  print(options)
  logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if options["--verbose"] else logging.INFO)
  dist = Distribution(options["<distribution>"])
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
  # Now print out the results
  joiner = ";" if options["--cmake"] else " "
  print(joiner.join(sorted(x.name for x in dist.modules)))

