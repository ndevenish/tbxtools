# coding: utf-8

"""
Reads a tree of SConscripts and extracts module and target information
"""

import collections
import itertools
import logging
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Set

import networkx as nx
import yaml

from .sconsemu import SconsEmulator, Target
from .utils import return_as_list

logger = logging.getLogger(__name__)


class LibTBXModule(object):
    """Represents a libtbx module"""

    def __init__(self, name, path, module_root):
        self.name = name
        self.path = path
        self.module_root = module_root
        self.required = set()
        self.targets = []
        # The sources generated by the refresh step
        self.generated_sources = []
        # Extra include paths to use this module
        self.include_paths = set()

        # self.required_by = set()

        if self.has_config:
            # Read the configuration for a basic dependency tree
            with open(os.path.join(self.module_root, self.path, "libtbx_config")) as f:
                self._config = eval(f.read())
                self.required = set(
                    self._config.get("modules_required_for_build", set())
                )
                self.required |= set(
                    self._config.get("modules_required_for_use", set())
                )
                self.required |= set(self._config.get("optional_modules", set()))
                # Handle aliases/multis
                if "boost" in self.required:
                    self.required.add("boost_adaptbx")
                    self.required.remove("boost")
                if "annlib" in self.required:
                    self.required.add("annlib_adaptbx")
                    self.required.remove("annlib")

    def __repr__(self):
        return "Module(name={}, path={})".format(repr(self.name), repr(self.path))

    @property
    def has_sconscript(self):
        return os.path.isfile(os.path.join(self.module_root, self.path, "SConscript"))

    @property
    def has_config(self):
        return os.path.isfile(
            os.path.join(self.module_root, self.path, "libtbx_config")
        )

    @property
    def has_refresh(self):
        return os.path.isfile(
            os.path.join(self.module_root, self.path, "libtbx_refresh.py")
        )

    @property
    def looks_like_module(self):
        """Does this module have *anything* to indicate it might be a module?"""
        return (
            self.has_sconscript
            or self.has_config
            or self.has_refresh
            or self.targets
            or os.path.isdir(os.path.join(self.module_root, self.path, "command_line"))
        )


@return_as_list
def find_libtbx_modules(modulepath, repositories={"cctbx_project"}):
    """Find all modules in a path"""

    # Find all direct subdirs, plus all in cctbx_project
    subdirs = [x for x in list(next(os.walk(modulepath))[1]) if not x.startswith(".")]
    for repo in repositories:
        if repo in subdirs:
            subdirs.remove(repo)
        for dirname in next(os.walk(os.path.join(modulepath, repo)))[1]:
            if not dirname.startswith("."):
                subdirs.append(os.path.join(repo, dirname))

    # All subdirs == all modules, as far as libtbx logic goes. Filter them later.
    modules = {}
    for dirname in subdirs:
        name = os.path.basename(dirname)
        path = dirname
        # Hard-coded ignore names - last resort
        if name == "__pycache__":
            continue
        # If we have the same name twice (dxtbx, boost), try to resolve
        new_module = LibTBXModule(name=name, path=path, module_root=modulepath)
        if name in modules:
            proposed = [x for x in [modules[name], new_module] if x.looks_like_module]
            if len(proposed) != 1:
                logger.debug(
                    "Cannot disambiguate {} and {}; resorting to .py check".format(
                        modules[name].path, new_module.path
                    )
                )
                proposed = [
                    x
                    for x in [modules[name], new_module]
                    if any((modulepath / Path(x.path)).glob("*.py"))
                ]
            if len(proposed) != 1:
                raise RuntimeError(
                    "Cannot decide between module candidates of {} and {}".format(
                        modules[name].path, new_module.path
                    )
                )
            logger.info(
                "Resolving module ambiguity between {} and {} = {}".format(
                    modules[name].path, new_module.path, proposed[0].path
                )
            )
            new_module = proposed[0]
        modules[name] = new_module
    return modules.values()


class TargetCollection(Set[Target]):
    """Collection wrapper to make operations on target sets easier"""

    def __init__(self, distribution):
        self.distribution = distribution

    def __contains__(self, target):
        if isinstance(target, str):
            return any(x.name == target for x in self)
        # The target must have a module to be in the distribution
        if target.module is None:
            return False
        # The target module must exist identically in this distribution
        if target.module is not self.distribution._modules.get(target.module.name):
            return False
        assert target in target.module.targets, "Target-modules out of sync"
        return True

    def __iter__(self):
        return itertools.chain(
            *(x.targets for x in self.distribution._modules.values())
        )

    def __len__(self):
        return sum(len(x.targets) for x in self.distribution._modules.values())

    @classmethod
    def _from_iterable(cls, it):
        return set(it)

    def remove(self, target):
        assert target in self
        target.module.targets.remove(target)
        target.module = None

    def remove_all(self, targets):
        for target in targets:
            self.remove(target)

    def __getitem__(self, targetname):
        found = [x for x in self if x.name == targetname]
        if not found:
            raise KeyError("No target named {}".format(targetname))
        if len(found) > 1:
            raise KeyError("More than one target names {}".format(targetname))
        return found[0]


class TBXDistribution(object):
    """Holds collected information about a TBX distribution and it's targets"""

    def __init__(self):
        self.module_path = None
        self._modules = {}
        self._targetcollection = TargetCollection(self)
        # Files generated by unusual means during build
        self.other_generated = []

    @property
    def targets(self) -> TargetCollection:
        """Returns a iterator over all targets in the distribution"""
        return self._targetcollection

    @property
    def modules(self):
        return self._modules

    @property
    def all_generated(self):
        return {
            x
            for x in set(self.other_generated)
            | set(
                itertools.chain(*(x.generated_sources for x in self._modules.values()))
            )
        }


def _build_dependency_graph(modules):
    """Builds a networkX dependency graph out of the module self-reported requirements.

    :param modules: A list of modules.
    """

    G = nx.DiGraph()
    G.add_nodes_from(x.name for x in modules)

    # Build the dependency graph from the libtbx information
    for module in modules:
        for req in module.required:
            G.add_edge(module.name, req)

        # Force a dependency on libtbx so it goes before everything else
        if not module.name == "libtbx":
            G.add_edge(module.name, "libtbx")

        # Check that we know about all the dependencies, and warn if we don't
        reqs = {x for x in modules if x.name in module.required}
        if len(reqs) < len(module.required):
            print(
                "{} has missing dependency: {}".format(
                    module.name, module.required - {x.name for x in reqs}
                )
            )

    # Custom edges to fix problems - not sure how order is determined without this
    G.add_edge("scitbx", "omptbx")

    # Handle adaptbx modules
    # An adaptbx module will usually(?) have a corresponding module that it's
    # generating the code/imports for. Unfortunately, when other modules specify
    # a dependency on "X" they often mean/require "X_adaptbx". libtbx seems to treat
    # these modules as co-dependents.
    #
    # For every dependency on an X where X_adaptbx exists, also add a dependecy to
    # X_adaptbx
    for src, dst in list(G.edges):
        adaptbx = dst + "_adaptbx"
        if adaptbx in G.nodes and not (src, adaptbx) in G.edges:
            logger.debug("Adding extra adaptbx edge %s, %s", src, adaptbx)
            G.add_edge(src, adaptbx)

    # We might potentially have dependency cycles. Try to turn this into an acyclic
    # graph by removing edges in order of priority based on how hard the dependency
    # requirement is.
    while not nx.is_directed_acyclic_graph(G):
        cycle = nx.cycles.find_cycle(G)
        logger.debug(
            "Cycle found in dependency graph: {}".format(
                " → ".join(x[0] for x in cycle)
            )
        )

        # Find the lowest priority edge (or an edge of the lowest priority)
        # in this cycle, and break it
        cycle_breaking_order = [
            "modules_required_for_build",
            "modules_required_for_use",
            "optional_modules",
        ]
        lowest_priority, lowest_priority_edge = None, None
        for start, end in cycle:
            module = [x for x in modules if x.name == start][0]

            edge_priority = None
            for config_list, entries in module._config.items():
                if end in entries and config_list in cycle_breaking_order:
                    edge_priority = cycle_breaking_order.index(config_list)
            assert (
                edge_priority is not None
            ), "Could not find loop edge {} in configuration for {}".format(end, start)
            logger.debug(
                ". Edge ({}, {}) priority = {}".format(start, end, edge_priority)
            )

            # If it's of the lowest priority then just break this. Otherwise, track it
            if lowest_priority is None or edge_priority > lowest_priority:
                lowest_priority, lowest_priority_edge = (edge_priority, (start, end))

        assert (
            edge_priority is not None
        ), "Could not find ANY loop edge priorities in cycle. Fatal Error."

        logger.info(
            "Resolving cycle by removing dependency {}={} from {}".format(
                cycle_breaking_order[lowest_priority],
                lowest_priority_edge[1],
                lowest_priority_edge[0],
            )
        )
        G.remove_edge(*lowest_priority_edge)

    return G


##############################################################################
# __main__ handling and setup functionality


def _deduplicate_target_names(targets):
    "Takes a list of targets and fixes names to avoid duplicates"
    namecount = collections.Counter([x.name for x in targets])
    for duplicate in [x for x in namecount.keys() if namecount[x] > 1]:
        duped = [x for x in targets if x.name == duplicate]
        modules = set(x.module for x in duped)
        assert len(modules) == len(duped), (
            "Module name not enough to disambiguate duplicate targets "
            "named {} (in {})"
        ).format(duplicate, modules)
        for target in duped:
            oldname = target.name
            target.name = "{}_{}".format(target.name, target.module.name)
            logger.info("Renaming target {} to {}".format(oldname, target.name))
    assert all(
        [x == 1 for _, x in collections.Counter([x.name for x in targets]).items()]
    ), "Deduplication failed"


def read_module_path_sconscripts(module_path):
    """Parse all modules/SConscripts in a tbx module root.

    Returns a TBXDistribution object.
    """

    modules = {x.name: x for x in find_libtbx_modules(module_path)}
    # Make a lookup to find modules by name
    # modulemap = {x.name: x for x in modules}

    # Find an order of processing that satisfies dependencies
    G = _build_dependency_graph(modules.values())
    node_order = list(reversed(list(nx.lexicographical_topological_sort(G))))

    logger.debug("Dependency processing order: {}".format(node_order))

    # Prepare the SCons emulator
    scons = SconsEmulator(dist=module_path)  # , modules=modules)

    # Process all modules in the determined dependency order
    scons_modules = [
        modules[x] for x in node_order if x in modules and modules[x].has_sconscript
    ]
    for module in scons_modules:
        scons.parse_module(module)

    # Say what we found
    logger.info("Found modules:")
    maxl = max(len(x.name) for x in modules.values())
    for module in sorted(modules.values(), key=lambda x: x.name):
        if module.looks_like_module:
            logger.info("  {}  {}".format(module.name.ljust(maxl), module.path))

    logger.info("Processing of SConscripts done.")
    logger.info("{} Targets recognised".format(len(scons.targets)))

    tbx = TBXDistribution()
    tbx.module_path = module_path
    tbx._modules = modules

    return tbx


def _is_cuda_target(target):
    if target.type == Target.Type.CUDALIB:
        return True
    if "cufft" in target.extra_libs:
        return True
    return False


def read_distribution(module_path):
    """Reads a TBX distribution, filter and prepare for output conversion."""

    tbx = read_module_path_sconscripts(module_path)

    # Remove the boost targets
    boost_target_names = {
        "boost_thread",
        "boost_system",
        "boost_python",
        "boost_chrono",
        "boost_numpy",
        "boost_filesystem",
        "libboost_filesystem",
    }
    boost_targets = {x for x in tbx.targets if x.name in boost_target_names}
    for target in boost_targets:
        logger.info(
            "Removing target {} (in {})".format(target.name, target.module.name)
        )
        tbx.targets.remove(target)

    # Remove any modules we don't want
    # - Clipper has some script referencing we don't understand completely
    # - fftw3tbx uses an external library and we don't use this in dials, so skip
    for module in {"clipper", "clipper_adaptbx", "fftw3tbx"}:
        if module in tbx.modules:
            logger.info(
                "Removing module {} ({} targets)".format(
                    module, len(tbx.modules[module].targets)
                )
            )
            del tbx.modules[module]

    # Remove CUDA projects, for now
    for target in [x for x in tbx.targets if _is_cuda_target(x)]:
        logger.info("Removing CUDA target {}".format(target.name))
        tbx.targets.remove(target)
        # Find any targets that link to this and remove those
        # Note, that this *may* expose symbol dependency problems without
        # unwinding Sconscript logic blocks, so enable_cuda=False may need
        # to set at some point to properly handle this
        link_name = target.output_filename
        if link_name.startswith("lib"):
            link_name = link_name[3:]
        for dependent in (x for x in tbx.targets if link_name in x.extra_libs):
            logger.debug("- removing {} from {}".format(link_name, dependent.name))
            dependent.extra_libs.remove(link_name)

    # Fix any duplicated target names
    _deduplicate_target_names(tbx.targets)

    # Classify any python-module-type targets as modules
    target: Target
    for target in tbx.targets:
        if (
            "boost_python" in target.extra_libs
            and not target.prefix
            and not target.type == Target.Type.OBJECT
        ):
            target.type = Target.Type.MODULE

    # Check assumptions about all the targets
    assert all(x.module for x in tbx.targets), "Not all targets belong to a module"
    assert all(x.prefix == "lib" for x in tbx.targets if x.type == Target.Type.SHARED)
    assert all(x.prefix == "lib" for x in tbx.targets if x.type == Target.Type.STATIC)
    assert all(x.prefix == "" for x in tbx.targets if x.type == Target.Type.MODULE)
    # assert all(
    #     not x.shared_sources for x in tbx.targets
    # ), "Shared sources exists - all should be filtered"
    # assert all("GLU" in x.extra_libs for x in tbx.targets if "GL" in x.extra_libs), (
    # "Not all GLU has GL")
    # assert all("GL" in x.extra_libs for x in tbx.targets if "GLU" in x.extra_libs), (
    # "Not all GL has GLU")

    # For all targets named directly after a module, ensure it's in the module root
    violating_targets = [
        x
        for x in tbx.targets
        if x.name in tbx.modules
        and PurePosixPath(x.origin_path)
        != PurePosixPath(Path(tbx.modules[x.name].path))
    ]
    for target in violating_targets:
        logger.info(
            "Moving module-named target {} to module {} from {}".format(
                target.name, target.name, target.module.name
            )
        )
        target.module.targets.remove(target)
        tbx.modules[target.name].targets.append(target)
        target.module = tbx.modules[target.name]
        target.origin_path = target.module.path

    # Finally, remove any modules that don't appear to be modules
    # - might not even be real modules
    for module in [x.name for x in tbx.modules.values() if not x.looks_like_module]:
        del tbx.modules[module]
        logger.debug("Removing module {} because no targets".format(module))

    # Print some information out
    all_libs = set(itertools.chain(*[x.extra_libs for x in tbx.targets]))
    external_libs = all_libs - {x.name for x in tbx.targets}
    logger.info("All linked libraries: {}".format(", ".join(all_libs)))
    logger.info("All external (w/o universal): {}".format(", ".join(external_libs)))
    logger.info("{} Targets remaining".format(len(tbx.targets)))

    # Check that we know and expect all the external libraries
    expected_external_libs = {
        "tiff",
        "boost_python",
        "GL",
        "GLU",
        "hdf5",
        "boost_numpy",
        "png",
        "hdf5_hl",
        "gtest",
        "gtest_main",
        "boost_filesystem",
        "dl",
    }
    if unexpected_external := external_libs - expected_external_libs:
        # Let's work out where these came from for a better error message
        for lib in unexpected_external:
            from_targets = [x.name for x in tbx.targets if lib in x.extra_libs]
            logger.error(
                "Got unexpected extra lib: %s from: %s", lib, ", ".join(from_targets)
            )
        raise RuntimeError(
            f"Unexpected extra external libs: {', '.join(unexpected_external)}"
        )

    return tbx


def main(args=None):
    logging.basicConfig(level=logging.INFO)

    if args is None:
        args = sys.argv[1:]
    if "-h" in args or "--help" in args or len(args) != 1 or not os.path.isdir(args[0]):
        print("Usage: tbx2depfile <module_path>")
        return 0

    module_path = args[0]
    tbx = read_distribution(module_path)

    # Can't: Not all python libraries end up in lib
    # assert all(
    #     x.output_path == "#/lib" for x in targets if x.type == Target.Type.MODULE
    # )

    # Build an export dictionary
    # module_data = defaultdict(list)
    # target_data = []
    scons_data = {"targets": [], "modules": []}

    for module in (x for x in tbx.modules.values()):
        scons_data["modules"].append({"path": module.path, "name": module.name})

    for target in tbx.targets:
        tdict = {
            "name": target.name,
            "type": target.type.value.lower(),
            "origin": target.origin_path,
            "sources": list(target.sources),
            "module": target.module.name,
        }
        if target.filename != target.name:
            tdict["filename"] = target.filename
        if target.output_path != "#/lib":
            tdict["output_path"] = target.output_path
        if target.extra_libs:
            tdict["dependencies"] = list(target.extra_libs)

        scons_data["targets"].append(tdict)

    # # module_data = dict(module_data)
    # import code
    # code.interact(local=locals())

    with open("scons_targets.yml", "w") as f:
        f.write(yaml.dump(scons_data))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
