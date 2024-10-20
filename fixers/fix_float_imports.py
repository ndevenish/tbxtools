#!/usr/bin/env python3
"""
Float imports from enclosed scopes to global.

Requires python3 with the packages bowler, pytest available.
If isort (5) is installed, can use for classification.

Doesn't float imports that:
    - Have a comment directly preceeding it
    - Are inside a try block
    - Are inside an if block
    - Are importing matplotlib (deal with this case later)
"""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import fissix.pgen2
import fissix.pygram
import fissix.pytree
import pytest
from bowler import Query
from bowler.types import LN, Capture, Filename
from fissix.fixer_util import Comma
from fissix.pgen2 import token
from fissix.pygram import python_symbols
from fissix.pytree import Leaf, Node, type_repr

try:
    import isort
except ImportError:
    isort = None

PATTERN = """import_name | import_from"""

IGNORE_IF = False
ONLY_FLOAT = set()

# Build a driver to help generate nodes from known code
# Used in testing
driver = fissix.pgen2.driver.Driver(
    fissix.pygram.python_grammar, convert=fissix.pytree.convert
)


def print_node(node: LN, max_depth: int = 1000, indent: str = "", last: bool = True):
    """Debugging function to print node tree.
    Arguments:
        node: The node to print
        max_depth: The maximum recursion depth to walk children
    """
    if last:
        first_i = "└─"
        second_i = "  "
    else:
        first_i = "├─"
        second_i = "│ "
    prefix = indent + first_i
    if type(node) is Node:
        print(
            prefix
            + "Node[{}] prefix={} suffix={}".format(
                type_repr(node.type), repr(node.prefix), repr(node.get_suffix())
            )
        )
    elif type(node) is Leaf:
        print(
            indent
            + first_i
            + "Leaf({}, {}, col={}{})".format(
                token.tok_name[node.type],
                repr(node.value),
                node.column,
                ", prefix={}".format(repr(node.prefix)) if node.prefix else "",
            )
        )
    else:
        raise RuntimeError("Unknown node type")
    indent = indent + second_i

    children = list(node.children)
    if max_depth == 0 and children:
        print(indent + f"└─...{len(children)} children")
    else:
        for i, child in enumerate(node.children):
            print_node(
                child,
                indent=indent,
                last=(i + 1) == len(children),
                max_depth=max_depth - 1,
            )


def get_child(node: Node, childtype: int) -> Optional[LN]:
    """Extract a single child from a node by type."""
    filt = [x for x in node.children if x.type == childtype]
    assert len(filt) <= 1
    return filt[0] if filt else None


def get_children(
    node: Node,
    childtype: int,
    *,
    recursive: bool = False,
    recurse_if_found=False,
    recurse_depth=None,
) -> List[LN]:
    """Extract all children from a node that match a type.
    Arguments:
        node: The node to search the children of. Won't be matched.
        childtype: The symbol/token code to search for
        recursive:
            If False, only the immediate children of the node will be searched
        recurse_if_found:
            If False, will stop recursing once a node has been found. If True,
            it is possible to have node types that are children of other nodes
            that were found earlier in the search.
        recurse_depth:
            How deep to go. None for any depth.
    Returns:
        A list of nodes matching the search type.
    """
    if not recursive or (recurse_depth is not None and recurse_depth <= 0):
        return [x for x in node.children if x.type == childtype]
    if recurse_depth is not None:
        recurse_depth -= 1
    matches = []
    for child in node.children:
        if child.type == childtype:
            matches.append(child)
            # If we want to stop recursing into found nodes
            if recurse_if_found:
                continue
        matches.extend(
            get_children(child, childtype, recursive=True, recurse_depth=recurse_depth)
        )
    return matches


def join_comma(entries):
    """Return a copy of a list with comma tokens between items"""
    out = [entries[0]]
    if len(entries) > 1:
        for entry in entries[1:]:
            out.append(Comma())
            out.append(entry)
    return out


# Accessing grammar types:  python_symbols.import_from
#           parting tokens: token.COMMA


def find_import_insert_point(node):
    past_docstring = False
    for i, n in enumerate(node.children):
        if not n.type == python_symbols.simple_stmt:
            break
        child_node = n.children[0]
        # If it's a non-future import then we're past the docstring
        if (
            not past_docstring
            and child_node.type == python_symbols.import_from
            and child_node.children[1] != "__future__"
        ):
            past_docstring = True

        # If we're not past the docstring and this is a string, then
        # this IS the docstring and so we don't want this
        if not past_docstring and child_node.type == token.STRING:
            past_docstring = True
            continue

        if n.children[0].type not in {
            python_symbols.import_name,
            python_symbols.import_from,
        }:
            break

    return i


def find_prev_leaf(node: LN) -> LN:
    """Find the previous leaf of a node in a node tree.

    This works even if the node is the first child of it's parent - the
    tree will be walked up until there is a previous sibling and then
    walk back down the tree.
    """
    # Move to previous node
    if node.prev_sibling is None:
        # Walk to a parent to get the sibling
        while node.prev_sibling is None:
            # If the parent is None and sibling is None, we're at the top. Give up.
            if node.parent is None:
                return None
            node = node.parent
    node = node.prev_sibling
    # Now navigate down the tree to the last leaf
    while node.children:
        node = node.children[-1]
    return node


def get_complete_prefix(node: LN) -> str:
    """Get a prefix for a node, even including ones attached to previous whitespace.

    This is because with dedenting, the graph can look like:
        │ │  └─Leaf(DEDENT, prefix='Something')
        │ └─Leaf(DEDENT, prefix='')
        ├─Node[simple_stmt, prefix='']
        │ ├─Node[import_from, prefix='']
    and therefore the prefix isn't attached to the node itself
    """
    prefix = node.prefix
    node = find_prev_leaf(node)
    while node and node.type in {token.DEDENT, token.INDENT}:
        prefix = str(node) + prefix
        node = find_prev_leaf(node)
    return prefix


@lru_cache(maxsize=1)
def get_isort_config_for(filename: Filename) -> isort.settings.Config:
    """Fetch a new or cached isort configuration for a file's path"""
    return isort.settings.Config(settings_path=str(Path(filename).parent))


def process_import(node: LN, capture: Capture, filename: Filename) -> Optional[LN]:
    # Skip any imports at file scope
    if node.parent.parent.type == python_symbols.file_input:
        return

    IMPORT_WHITELIST = {
        "importlib",
        "math",
        "optparse",
        "os",
        "os.path",
        "six.moves.cPickle as pickle",
        "six.moves",
        "sys",
        "urllib2",
        "uuid",
    }
    module_name = str(node.children[1]).strip()

    always_float = module_name in IMPORT_WHITELIST
    # if STDLIB_ONLY:
    #     if :
    #         return
    # See if this is rejected
    if ONLY_FLOAT:
        isort_class = None
        if any(x.isupper() for x in ONLY_FLOAT):
            isort_class = isort.place_module(
                module_name, config=get_isort_config_for(filename)
            )
        if (
            module_name not in ONLY_FLOAT
            and not any(module_name.startswith(x + ".") for x in ONLY_FLOAT)
            and isort_class not in ONLY_FLOAT
        ):
            return

    # if not always_float:
    # Bypass nodes with comments for now
    if node.get_suffix().strip() or get_complete_prefix(node).strip():
        print(
            f"Not floating {filename}:{node.get_lineno()} ({module_name}) as has comments"
        )
        return

    if "matplotlib" in str(node):
        print(f"Not floating {filename}:{node.get_lineno()} as matplotlib")
        return

    # Find the root node. While doing so, check that we aren't inside a try
    root = node
    while root.parent:
        # Handle always-float and try blocks - don't leave invalid
        if root.type == python_symbols.try_stmt:
            if always_float:
                # Check that we aren't the only entry in this suite
                assert node.parent.parent.type == python_symbols.suite
                if len(node.parent.parent.children) == 4:
                    print(
                        f"Not floating always-float {filename}:{node.get_lineno()} ({module_name}) as only statement inside try"
                    )
                    return
            else:
                print(
                    f"Not floating {filename}:{node.get_lineno()} ({module_name}) as inside try"
                )
                return
        if not always_float:
            # Give a special message to the user for __main__ if non-floating
            if (
                root.type == python_symbols.if_stmt
                and not IGNORE_IF
                and "__main__" in str(root.children[1])
            ):
                print(
                    f"Not floating {filename}:{node.get_lineno()} ({module_name}) as inside __main__ test if"
                )
                return
            if root.type == python_symbols.if_stmt and not IGNORE_IF:
                print(
                    f"Not floating {filename}:{node.get_lineno()} ({module_name}) as inside if"
                )
                return

        root = root.parent

    # Find the insertion point for this root node
    insert_point = find_import_insert_point(root)

    # Get the actual statement node
    statement = node.parent
    prev_sibling = statement.prev_sibling
    next_sibling = statement.next_sibling
    assert statement.type == python_symbols.simple_stmt

    # Are we are the start of a scope?
    parent_index = statement.parent.children.index(statement)
    # From suite definition; parent_index of first statement is either 0 or 2:
    #   suite: simple_stmt | NEWLINE INDENT stmt+ DEDENT
    # But for our purposes can be 3 if we have a docstring.
    assert parent_index != 0, "Inline statement functions not supported ATM"
    prev_sibiling_is_string = (
        prev_sibling.type == python_symbols.simple_stmt
        and prev_sibling.children[0].type == token.STRING
    )
    if parent_index == 2 or (parent_index == 3 and prev_sibiling_is_string):
        # We're the first statement, or the first non-docstring statement.
        # If we have a trailing newline, remove it. Indentation handled later.
        if next_sibling and next_sibling.prefix.startswith("\n"):
            next_sibling.prefix = next_sibling.prefix[1:]

    # Get the previous node. This might be the sibling, or some tree-child thereof
    prev_node = list(prev_sibling.leaves())[-1]

    # print_node(prev_node)
    if prev_node.type in {token.INDENT, token.DEDENT}:
        # If we just indented(dedented) then tree looks like:
        #   [INDENT]      "    "
        #   [simple_stmt] ""            <- statement
        #       ...
        #       [NEWLINE] ""      "\n"
        #   [LN]          "    "
        # e.g. this next sibling node holds it's own indent but the
        # statement node's indentation is handled by the indent. So we
        # need to remove the indentation from the next sibling.
        next_sibling.prefix = next_sibling.prefix.lstrip(" ")

    if prev_node.type == token.INDENT and prev_node.prefix.isspace():
        # We've got leading newlines we want to strip -
        # If a function starts with a blank line(s), the "\n" and any
        # stray indentation are attached to the [INDENT] as a prefix.
        # Our reasoning for removing this blank line: It was showing the
        # user that the import was special.
        # This might be flimsy reasoning.
        prev_node.prefix = ""

    # We could be transplanting a node with a prefix. Move it to the sibling
    next_sibling.prefix = node.prefix.rstrip(" ") + next_sibling.prefix

    # Do the actual moving
    statement.remove()
    root.insert_child(insert_point, statement)
    node.prefix = ""


def main():
    """Runs the query. Called by bowler if run as a script"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--do", action="store_true", help="Actually write the changes")
    parser.add_argument(
        "--silent", action="store_true", help="Do the processing quietly"
    )
    parser.add_argument(
        "--ignoreif", action="store_true", help="Float from inside if statements"
    )
    parser.add_argument(
        "--stdlib",
        action="store_true",
        help="Only float standard library imports. Same as --only=STDLIB",
    )
    parser.add_argument(
        "--only",
        action="store",
        help="Comma-separated list of modules to float. All others will be ignored. Can pass in all caps e.g. STDLIB and the isort classification will be used.",
    )
    parser.add_argument(
        "filenames", metavar="FILE", nargs="*", help="Specific filenames to process"
    )
    args = parser.parse_args()
    global IGNORE_IF, ONLY_FLOAT
    IGNORE_IF = args.ignoreif
    # Don't allow stdlib floating if isort is not available
    if args.stdlib and isort is None:
        sys.exit("Can not classify standard library modules; isort not installed")

    # Handle specific subsets of floating
    if args.only:
        only_list_prep = [x.strip() for x in args.only.split(",")]
        if any(x.isupper() for x in only_list_prep) and isort is None:
            sys.exit("Can not use isort classification as isort is not available")
        ONLY_FLOAT = set(only_list_prep)
    if args.stdlib:
        ONLY_FLOAT.add("STDLIB")

    (
        Query(args.filenames)
        .select(PATTERN)
        # .select_root()
        .modify(process_import)
        .execute(interactive=False, write=args.do, silent=args.silent)
    )


@pytest.fixture
def checker():
    """Pytest fixture to make testing input/expected output strings easy."""

    def _checker(input_text, expected_out):
        nodeIn = driver.parse_string(input_text)
        print_node(nodeIn)
        import_node = get_children(nodeIn, python_symbols.import_name, recursive=True)[
            0
        ]
        process_import(import_node, {}, "__TEST__")
        # print_tree(nodeIn)
        print_node(nodeIn)
        assert expected_out == str(nodeIn), "Tranformed code does not match expected"

    return _checker


def test_basic_move(checker):
    origin = """
def x():
    pass
    import y
""".lstrip()
    expected = """
import y
def x():
    pass
""".lstrip()
    checker(origin, expected)


def test_function_start_trim(checker):
    origin_simple_removal = """
def x():
    import y
    pass
"""
    origin_with_extra_space = """
def x():
    import y

    pass
"""
    expected = """import y

def x():
    pass
"""
    checker(origin_simple_removal, expected)
    # print("Origin2")
    checker(origin_with_extra_space, expected)
    # print("Origin3")
    # Check with a docstring - we still want to remove the extra line
    origin_with_docstring = """
def x():
    \"""something\"""
    import y

    pass
"""
    expected_with_docstring = """import y

def x():
    \"""something\"""
    pass
"""
    checker(origin_with_docstring, expected_with_docstring)


def test_import_after_indent(checker):
    origin = """
def x():
    while True:
        something
    import x
    if x:
        pass
"""
    out = """import x

def x():
    while True:
        something
    if x:
        pass
"""
    checker(origin, out)


def test_whitelisted_import_comments(checker):
    origin = """
def x():
    # Some comment
    import os
    pass
"""
    out = """import os

def x():
    # Some comment
    pass
"""
    checker(origin, out)


def test_leading_blank_line_in_function(checker):
    origin = """
def x():

    import os
    pass
"""
    out = """import os

def x():
    pass
"""
    checker(origin, out)


def test_bad_always_float(checker):
    origin = """
try:
    import math
except:
    pass
"""
    out = origin
    checker(origin, out)


if __name__ == "__main__":
    main()
