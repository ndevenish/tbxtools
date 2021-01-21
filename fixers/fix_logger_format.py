#!/usr/bin/env python3
"""
Rewrite logger calls that don't defer their formatting.

e.g. logger.info("a value: %s" % value)
  -> logger.info("a value: %s", value)

     logger.info(" values: %s %d" % (value, 3))
  -> logger.info(" values: %s %d", value, 3)

Written so that flynt can be run over a codebase without turning
all of these into f-strings, at which point it's even more work
to turn them back to logger-style deferred formatting.

Requires python3 with the packages bowler, pytest available.

Doesn't float imports that:
    - Have a comment directly preceeding it
    - Are inside a try block
    - Are inside an if block
    - Are importing matplotlib (deal with this case later)
"""

import argparse
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


def process_percent_format(
    node: LN, capture: Capture, filename: Filename  # noqa: U100
) -> Optional[LN]:

    # root = node
    # while root.parent:
    #     root = root.parent

    # pprint.pprint(capture)
    # print_node(node)

    # breakpoint()
    try:
        if capture["vars"].children[0].value == "(":
            format_args = capture["vars"].children[1].children
        else:
            format_args = [capture["vars"]]
        # # breakpoint()
        # else:
        #     print_node(node)
        #     raise RuntimeError("Unknown child!")
    except IndexError:
        # pass
        # We aren't something with a sub-tuple, assume single-argument
        format_args = [capture["vars"]]

    # Prepare these node for transplant
    for child in format_args:
        child.parent = None
    capture["formatstr"].parent = None

    capture["call"].children[1] = Node(
        python_symbols.arglist, [capture["formatstr"], Comma()] + format_args
    )


def main(argv=None):
    """Runs the query. Called by bowler if run as a script"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--do", action="store_true", help="Actually write the changes")
    parser.add_argument(
        "--silent", action="store_true", help="Do the processing quietly"
    )
    parser.add_argument(
        "filenames", metavar="FILE", nargs="*", help="Specific filenames to process"
    )
    parser.add_argument(
        "--logger",
        metavar="NAME",
        nargs=1,
        default="logger",
        help="The conventional name for loggers",
    )
    args = parser.parse_args(argv)

    PERCENT_PATTERN = f"""
    power <
        '{args.logger}'
        trailer
        call=trailer <
            '(' term < formatstr=any '%' vars=any > ')'
        >
    >"""
    print(args)
    (
        Query(args.filenames)
        .select(PERCENT_PATTERN)
        .modify(process_percent_format)
        .execute(interactive=False, write=args.do, silent=args.silent)
    )


@pytest.fixture
def checker(tmp_path):
    """Pytest fixture to make testing input/expected output strings easy."""

    def _checker(input_text, expected_out):
        filename = tmp_path / "test.py"
        filename.write_text(input_text + "\n")
        main(["--do", str(filename)])

        # We don't care about formatting, only structure, so strip them
        def _compact(string):
            return "".join(x for x in string if x not in {" ", "\n"})

        actual_out = _compact(filename.read_text())
        if expected_out is None:
            expected_out = input_text
        assert (
            _compact(expected_out) == actual_out
        ), "Tranformed code does not match expected"

    return _checker


test_cases = [
    ('logger.info("abc" % something)', 'logger.info("abc", something)'),
    ('logger.info("def" % (something, 3))', 'logger.info("def", something, 3)'),
    ('logger.info("%s %s" % sometuple)', None),
]


@pytest.mark.parametrize("inline,outline", test_cases)
def test_basics(checker, inline, outline):
    checker(inline, outline)


# logger.info("def" % (something, 3)))


#     origin = """
# def x():
#     pass
#     import y
# """.lstrip()
#     expected = """
# import y
# def x():
#     pass
# """.lstrip()
#     checker(origin, expected)


if __name__ == "__main__":
    main()
