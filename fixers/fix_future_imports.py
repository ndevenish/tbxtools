#!/usr/bin/env python3
"""
Fixing up of __future__ imports.

- Combine any __future__ imports by floating the names to the first one.
- Remove obselete __future__ imports and add the standard set

Only the floating will be done if --floatonly has been passed. For the
floating this is e.g. combining
    from __future__ import division
    from __future__ import print_function
    ...
    ...
    from __future__ import absolute_import

into:
    from __future__ import absolute_import, division, print_function

This is useful for tidiness but also required because not running the
full futurize stage2 doesn't go back and fixup the split import
declarations.
"""

from __future__ import annotations

import argparse
from typing import List, Optional

import fissix.pgen2
from bowler import Query
from bowler.types import LN, Capture, Filename
from fissix.fixer_util import Comma, Name
from fissix.pgen2 import token
from fissix.pygram import python_symbols
from fissix.pytree import Node

driver = fissix.pgen2.driver.Driver(
    fissix.pygram.python_grammar, convert=fissix.pytree.convert
)

FLOAT_MODE = False


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


def join_comma(entries: List[LN]) -> List[LN]:
    """Join a list with a comma token"""
    out = [entries[0]]
    if len(entries) > 1:
        for entry in entries[1:]:
            out.append(Comma())
            out.append(entry)
    return out


def find_future_import_insert_point(node: Node):
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

        return i
        # if n.children[0].type not in {
        #     python_symbols.import_name,
        #     python_symbols.import_from,
        # }:
        #     break

    return i


def process_root(node: LN, capture: Capture, filename: Filename) -> Optional[LN]:
    print(filename)
    # Find any imports close to the root level
    imports = [
        x
        for x in get_children(
            node, python_symbols.import_from, recursive=True, recurse_depth=1
        )
        if x.children[1].type == token.NAME and x.children[1].value == "__future__"
    ]
    if len(imports) <= 1 and FLOAT_MODE:
        # Do nothing if one or no imports and only in float-mode
        return
    elif len(imports) == 0:
        # We're in update mode - add missing __future__ imports
        import_insert_point = find_future_import_insert_point(node)
        insert = driver.parse_string(
            "from __future__ import absolute_import, division, print_function\n"
        )
        node.children.insert(import_insert_point, insert)
        return

    # Extract the list of __future__ imports from all of the import statements
    future_names = set()
    for imp in imports:
        if imp.children[3].type == token.NAME:
            future_names.add(imp.children[3].value)
        elif imp.children[3].type == python_symbols.import_as_names:
            future_names.update(
                {x.value for x in get_children(imp.children[3], token.NAME)}
            )
    # If we're not purely floating, update the list of names
    if not FLOAT_MODE:
        # If present, make all the basics present
        if future_names:
            future_names |= {"absolute_import", "division", "print_function"}

        # Remove anything already mandatory
        future_names -= {"generators", "nested_scopes", "with_statement"}

    # Update the first import instance with all the names
    if len(future_names) == 1:
        imports[0].children[3] = Name(list(future_names)[0])
    else:
        # Make the first one a multi-import
        commad = join_comma([Name(x, prefix=" ") for x in sorted(future_names)])
        names = Node(python_symbols.import_as_names, commad)
        imports[0].children[3] = names

    # Remove the other imports
    for imp in imports[1:]:
        assert imp.parent.type == python_symbols.simple_stmt
        imp.parent.parent.children.remove(imp.parent)


def main():
    """Runs the query. Called by bowler if run as a script"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--do", action="store_true", help="Actually write the changes")
    parser.add_argument(
        "--silent", action="store_true", help="Do the processing quietly"
    )
    parser.add_argument(
        "--onlyfloat", action="store_true", help="Only float, don't also fixup"
    )
    parser.add_argument(
        "filenames", metavar="FILE", nargs="*", help="Specific filenames to process"
    )
    args = parser.parse_args()

    # Hack in a separate mode for updating everything for py3
    global FLOAT_MODE
    FLOAT_MODE = args.onlyfloat

    (
        Query(args.filenames)
        .select_root()
        .modify(process_root)
        .execute(interactive=False, write=args.do, silent=args.silent)
    )


if __name__ == "__main__":
    main()
