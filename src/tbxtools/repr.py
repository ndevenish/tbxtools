"""Monkeypatching scitbx/cctbx for diagnostics output"""

from __future__ import annotations

import enum
import re
from io import StringIO
from math import floor, log10

from libtbx import phil


class Flags(enum.IntEnum):
    BACKGROUND_INCLUDES_BAD_PIXELS = 32768
    BAD_REFERENCE = 2097152
    BAD_SPOT = 64512
    CENTROID_OUTLIER = 131072
    DONT_INTEGRATE = 128
    FAILED_DURING_BACKGROUND_MODELLING = 262144
    FAILED_DURING_PROFILE_FITTING = 1048576
    FAILED_DURING_SUMMATION = 524288
    FOREGROUND_INCLUDES_BAD_PIXELS = 16384
    IN_POWDER_RING = 8192
    INCLUDES_BAD_PIXELS = 49152
    INDEXED = 4
    INTEGRATED = 768
    INTEGRATED_PRF = 512
    INTEGRATED_SUM = 256
    OBSERVED = 2
    OVERLAPPED_BG = 2048
    OVERLAPPED_FG = 4096
    OVERLOADED = 1024
    PREDICTED = 1
    REFERENCE_SPOT = 64
    STRONG = 32
    USED_IN_MODELLING = 65536
    USED_IN_REFINEMENT = 8

    @classmethod
    def resolve(cls, value):
        return FlagViewer({ev for ev in cls if value & ev.value})


class FlagViewer(set):
    def __repr__(self):
        flag_names = sorted([n.name.upper() for n in self])  # Flags.resolve(self.value)
        if flag_names:
            return " | ".join(flag_names)
        else:
            return "{None}"


# Remove dtype= section from numpy output
re_remove_dtype = re.compile(r"(?:,|\()\s*dtype=\w+(?=,|\))")
# Regular expression to help make repr's oneline
reReprToOneLine = re.compile("\n\\s+")

_summaryEdgeItems = 3  # repr N leading and trailing items of each dimension
_summaryThreshold = 1000  # total items > triggers array summarization


def _phil_repr(self, in_scope=False):
    """Hack in a phil.scope_extract repr function"""
    s = StringIO()
    if not in_scope:
        s.write('<phil.scope_extract """')
    s.write("{\n")
    # Step over every named element in this
    phil_names = sorted([x for x in self.__dict__ if not x.startswith("_")])
    # Get the maximum length of an attribute (non sub-scope) value
    max_len = max(
        len(x)
        for x in phil_names
        if not isinstance(getattr(self, x), phil.scope_extract)
    )
    for name in phil_names:
        s.write("  " + name.ljust(max_len) + " ")
        value = getattr(self, name)

        if isinstance(value, phil.scope_extract):
            # Get the representation, then add an indentation to every line
            subscope = value.__repr__(in_scope=True)
            subscope = "\n".join("  " + x for x in subscope.splitlines()).strip()
            s.write(subscope)
        else:
            # Just output the value
            s.write("= " + repr(value))
        s.write("\n")
    s.write("}")
    if not in_scope:
        s.write('""">')
    return s.getvalue()


def _miller_repr(self):
    """Special-case repr for miller-index objects"""
    s = type(self).__name__ + "("
    if len(self):
        s += "["

        indent = ",\n" + " " * len(s)
        # Work out how to align the data
        format_sample = self
        if len(self) > _summaryThreshold:
            format_sample = list(self[:_summaryEdgeItems]) + list(
                self[-_summaryEdgeItems:]
            )
        # Do we have negative symbols
        negs = [any(x[i] < 0 for x in format_sample) for i in range(3)]
        # Maximum width
        maxw = [
            max(
                int(1 + floor(log10(abs(x[i])) if x[i] != 0 else 0))
                for x in format_sample
            )
            for i in range(3)
        ]
        fmts = (
            "("
            + ", ".join(
                [
                    "{{:{}{}d}}".format(" " if neg else "", w + (1 if neg else 0))
                    for neg, w in zip(negs, maxw)
                ]
            )
            + ")"
        )

        # tup_fmt = ()

        if len(self) > _summaryThreshold:
            "({: 3d}, {: 3d}, {: 3d})"
            s += indent.join(fmts.format(*x) for x in self[:_summaryEdgeItems])
            s += indent + "..." + indent
            s += indent.join(fmts.format(*x) for x in self[-_summaryEdgeItems:])
        else:
            s += indent.join(fmts.format(*x) for x in self)

        s += "]"
    s += ")"
    return s


def _double_vec_repr(self):
    """Special-case repr for miller-index objects"""
    s = type(self).__name__ + "("
    if self:
        s += "["
        indent = "\n" + " " * len(s)

        if len(self) > _summaryThreshold:
            "({: 3d}, {: 3d}, {: 3d})"
            s += indent.join(repr(x) for x in self[:_summaryEdgeItems])
            s += indent + "..." + indent
            s += indent.join(repr(x) for x in self[-_summaryEdgeItems:])
        else:
            s += indent.join(repr(x) for x in self)
        s += "]"
    s += ")"
    return s


_max_column_width = 50
_max_column_height = 60


def _reftable_repr(self):
    _max_display_width = 100
    s = "<{}".format(type(self).__name__)
    if self:
        s += "\n"
        indent = "    "
        maxcol = max(len(x) for x in self.keys())

        rows = []
        for column in sorted(self.keys()):
            row = indent + column.ljust(maxcol) + " = "
            # Now do a single-line representation of the column....
            data = self[column]
            remaining_space = _max_display_width - len(row)
            data_repr = " ".join(x.strip() for x in repr(data).splitlines())
            if len(data_repr) > remaining_space:
                data_repr = data_repr[: remaining_space - 3] + "..."
            row += data_repr
            rows.append(row)
        s += "\n".join(rows)
    s += ">"
    if self:
        s += "\n[{} rows x {} columns]".format(len(self), len(list(self.keys())))
    return s


# re_remove_dtype
def _patch_flex(flex, dtype, shape=None, ndim=1):
    def _do_repr(x):
        return re_remove_dtype.sub(
            "", repr(x.as_numpy_array()).replace("array(", f"{type(x).__name__}(")
        )

    flex.__repr__ = _do_repr


def _cctbx_crystal_symmetry_repr(self):
    parts = []
    if self.space_group_info() is not None:
        parts.append("space_group_symbol='{}'".format(str(self.space_group_info())))
    if self.unit_cell() is not None:
        parts.append("unit_cell={}".format(self.unit_cell().parameters()))
    return "symmetry({})".format(", ".join(parts))


def _cctbx_miller_set_repr(self):
    """Repr function for miller set and array objects"""
    parts = ["crystal_symmetry=" + _cctbx_crystal_symmetry_repr(self)]
    if self.indices():
        parts.append("indices=" + reReprToOneLine.sub(" ", repr(self.indices())))
    if self.anomalous_flag() is not None:
        parts.append("anomalous_flag=" + str(self.anomalous_flag()))
    if hasattr(self, "data") and self.data() is not None:
        parts.append("data=" + reReprToOneLine.sub(" ", repr(self.data())))
    if hasattr(self, "sigmas") and self.sigmas() is not None:
        parts.append("sigmas=" + reReprToOneLine.sub(" ", repr(self.sigmas())))
    if hasattr(self, "info") and self.info() is not None:
        parts.append("sigmas=" + reReprToOneLine.sub(" ", repr(self.sigmas())))

    return type(self).__name__ + "(" + ", ".join(parts) + ")"


def do_monkeypatching():
    import cctbx.array_family.flex
    import cctbx.crystal
    import cctbx.miller
    import cctbx.sgtbx
    import cctbx.uctbx
    import dials.array_family.flex
    import dxtbx.model
    import scitbx.array_family.flex

    _patch_flex(scitbx.array_family.flex.size_t, int)
    _patch_flex(scitbx.array_family.flex.double, float)
    _patch_flex(scitbx.array_family.flex.int, int)
    _patch_flex(scitbx.array_family.flex.bool, bool)

    cctbx.array_family.flex.miller_index.__repr__ = _miller_repr
    scitbx.array_family.flex.vec3_double.__repr__ = _double_vec_repr
    dials.array_family.flex.reflection_table.__repr__ = _reftable_repr

    phil.scope_extract.__repr__ = _phil_repr
    phil.scope_extract.__str__ = lambda x: x.__repr__(in_scope=True)

    cctbx.crystal.symmetry.__repr__ = _cctbx_crystal_symmetry_repr
    cctbx.miller.set.__repr__ = _cctbx_miller_set_repr
    cctbx.sgtbx.space_group_info.__repr__ = lambda x: type(
        x
    ).__name__ + "('{}')".format(str(x))
    cctbx.sgtbx.space_group.__repr__ = (
        lambda x: "<"
        + type(x).__name__
        + " type="
        + repr(x.type().lookup_symbol())
        + ">"
    )

    cctbx.uctbx.unit_cell.__repr__ = lambda x: "{}({})".format(
        type(x).__name__, x.parameters()
    )
    cctbx.uctbx.ext.unit_cell.__repr__ = lambda x: "{}({})".format(
        type(x).__name__, x.parameters()
    )

    dxtbx.model.ExperimentList.__repr__ = (
        lambda self: "[" + ", ".join(repr(x) for x in self) + "]"
    )


# Always do this on import
do_monkeypatching()
