#!/usr/bin/env python3
"""
Combine any __future__ imports by floating the names to the first one.

e.g. combines
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
import sys
from typing import List, Optional

from bowler import Query
from bowler.types import LN, Capture, Filename
from fissix.fixer_util import Comma, Name
from fissix.pgen2 import token
from fissix.pygram import python_symbols
from fissix.pytree import Node

UPDATE_MODE = False


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
    # # Do nothing if one
    if len(imports) <= 1 and not UPDATE_MODE:
        return

    # Extract the list of __future__ imports from all of these
    future_names = set()
    for imp in imports:
        if imp.children[3].type == token.NAME:
            future_names.add(imp.children[3].value)
        elif imp.children[3].type == python_symbols.import_as_names:
            future_names.update(
                {x.value for x in get_children(imp.children[3], token.NAME)}
            )
    if UPDATE_MODE:
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

    do_write = "--do" in sys.argv
    do_silent = "--silent" in sys.argv
    # Hack in a separate mode for updating everything for py3
    global UPDATE_MODE
    UPDATE_MODE = "--update" in sys.argv

    filenames = [x for x in sys.argv[1:] if not x.startswith("-")]
    if filenames:
        print(f"Got {len(filenames)} files to process")
    else:
        sys.exit("No files specified")

    (
        Query(filenames)
        .select_root()
        .modify(process_root)
        .execute(interactive=False, write=do_write, silent=do_silent)
    )


if __name__ == "__main__":
    main()
