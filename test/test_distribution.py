# coding: utf-8
from __future__ import annotations

import logging
import os

import pytest

from tbxtools import DependencyError, Distribution

logging.basicConfig(level=logging.INFO)
test_dist = os.path.join(os.path.dirname(__file__), "fake_distribution")


def testLoadDistribution():
    Distribution(test_dist)


def testFailedPackage():
    dist = Distribution(test_dist)
    with pytest.raises(DependencyError):
        dist.request_modules(["notapackage"])


def testRepositories():
    dist = Distribution(test_dist)
    dist.request_modules(["repo_module", "root_module"])
    assert dist["repo_module"].path == "cctbx_project/repo_module"
    assert dist["root_module"].path == "root_module"


def testBasicDependency():
    dist = Distribution(test_dist)
    dist.request_modules(["root_module"])
    assert "repo_module" in dist


def testBadDepModule():
    dist = Distribution(test_dist)
    with pytest.raises(DependencyError):
        dist.request_modules(["bad_dep_module"])


def testOptionalDependency():
    dist = Distribution(test_dist)
    dist.request_modules(["i_have_optional_dependencies"])
    assert "repo_module" in dist
    assert "i_have_optional_dependencies" in dist
