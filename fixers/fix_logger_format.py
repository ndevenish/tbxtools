#!/usr/bin/env python3
"""
Rewrite logger calls that don't defer their formatting.

e.g. logger.info("a value: %s" % value)
  -> logger.info("a value: %s", value)

     logger.info(" values: %s %d" % (value, 3))
  -> logger.info(" values: %s %d", value, 3)
  
     logger.info("{} {:.3f} {:>10}".format(a, b, c))
  -> logger.info("%s %.3f %10s", a, b, c)

Written so that flynt can be run over a codebase without turning
all of these into f-strings, at which point it's even more work
to turn them back to logger-style deferred formatting.

Requires python3 with the packages bowler, pytest available.
"""

import argparse
import re
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


def print_node(
    node: LN, max_depth: int = 1000, indent: str = "", last: bool = True, capture={}
):
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
    name = ""
    if node in capture.values():
        name = (
            "\033[32m" + next(k for k, v in capture.items() if v == node) + "\033[0m= "
        )
    if type(node) is Node:
        print(
            prefix
            + name
            + "Node[{}] prefix={} suffix={}".format(
                type_repr(node.type), repr(node.prefix), repr(node.get_suffix())
            )
        )
    elif type(node) is Leaf:
        print(
            indent
            + first_i
            + name
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
                capture=capture,
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


RE_MAPPINGKEY = re.compile(r"[^%]%\(")
RE_SPECIFIERS = re.compile(r"(?<!%)%[^%]")
RE_FORMAT_SPECIFIER = re.compile(r"(?<!{)({[^{}]*})")
RE_UNDERSTOOD_FORMAT = re.compile(r"(?<!{)({:\d*.\d+f}|{:>\d+}|{})")


def process_percent_format(
    node: LN, capture: Capture, filename: Filename  # noqa: U100
) -> Optional[LN]:

    # if capture["formatstr"] has more than one format point, don't
    # expand it for now
    # if capture["formatstr"]:
    # print_node(capture["formatstr"])
    specifiers = None
    if capture["formatstr"].type == token.STRING:
        if RE_MAPPINGKEY.search(capture["formatstr"].value):
            print(
                f"Not formatting {filename}:{node.get_lineno()} as appears to have mapping key"
            )
            return None
        specifiers = RE_SPECIFIERS.findall(capture["formatstr"].value)

    # breakpoint()
    try:
        # Access precise chain of lookups for the inline-tuple case
        if capture["vars"].children[0].value == "(":
            format_args = capture["vars"].children[1].children
        else:
            # It's not this specific case, treat it as one item
            raise IndexError
    except IndexError:
        # Not a tuple, if we have more than one specifier then we can't
        # reliably rewrite this.
        if specifiers and len(specifiers) > 1:
            print(
                f"Not formatting {filename}:{node.get_lineno()} because unsafe rewriting indirect tuple"
            )
            return None
        # We aren't something with a sub-tuple, assume single-argument
        format_args = [capture["vars"]]

    # Don't rewrite if
    # Prepare these node for transplant
    for child in format_args:
        child.parent = None
    capture["formatstr"].parent = None

    capture["call"].children[1] = Node(
        python_symbols.arglist, [capture["formatstr"], Comma()] + format_args
    )
    return None


# RE_FORMAT_SPECIFIER = re.compile(r"(?<!{)({[^{}]*})")
# RE_UNDERSTOOD_FORMAT = re.compile(r"(?<!{)({:\d*.\d+f}|{:>\d+}|{})")


def process_format_format(
    node: LN, capture: Capture, filename: Filename  # noqa: U100
) -> Optional[LN]:

    formatstring = capture["formatstr"]
    if formatstring.type != token.STRING:
        print("Not formatting {filename}:{node.get_lineno()} because indirect format")
        return None
    num_specifiers = len(RE_FORMAT_SPECIFIER.findall(formatstring.value))
    understood = RE_UNDERSTOOD_FORMAT.findall(formatstring.value)

    if len(understood) != num_specifiers:
        print(
            f"Not formatting {filename}:{node.get_lineno()} because complex format specifiers:\n    {formatstring.value.strip()}"
        )
        return None

    # Basic {}
    formatstring.value = re.sub(r"{}", "%s", formatstring.value)
    # {:.3f} and friends
    formatstring.value = re.sub(r"(?<!{){:(\d*\.\d+f)}", r"%\1", formatstring.value)
    # {:>12}
    formatstring.value = re.sub(r"(?<!{){:>(\d+)}", r"%\1s", formatstring.value)
    # if RE_NONPLAIN_FORMAT.search(formatstring.value):
    #     return None

    # print_node(node, capture=capture)
    # breakpoint()
    if isinstance(capture["arglist"], Leaf):
        arguments = [capture["arglist"]]
    else:
        arguments = list(capture["arglist"].children)

    # Clear the parents of moved nodes
    for child in arguments:
        child.remove()
    formatstring.remove()

    # Rewrite the format-string specifier
    formatstring.value = formatstring.value.replace("{}", "%s")
    # breakpoint()
    # Build the arglist
    capture["formatblock"].replace(
        Node(python_symbols.arglist, [formatstring, Comma()] + arguments)
    )

    # RE_NONPLAIN_FORMAT
    # print_node(node, capture=capture)
    # breakpoint()
    return None


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
    FORMAT_PATTERN = f"""
    power <
        '{args.logger}'
        trailer
        call=trailer <
            '(' formatblock=any <
                formatstr=STRING trailer < "." "format" >
                any < '(' arglist=any ')'>
            > ')'
        >
    >
    """
    (
        Query(args.filenames)
        .select(PERCENT_PATTERN)
        .modify(process_percent_format)
        .select(FORMAT_PATTERN)
        .modify(process_format_format)
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
    ('logger.info("%(some)" % {"some": 4})', None),
    ('logger.info("{}".format(3))', 'logger.info("%s", 3)'),
    ('logger.info("{:.12f}".format(3))', 'logger.info("%.12f", 3)'),
    ('logger.info("{:>3}".format(3))', 'logger.info("%3s", 3)'),
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
