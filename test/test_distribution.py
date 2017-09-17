# coding: utf-8

import os

import pytest

from tbxtools import Distribution, DependencyError

test_dist = os.path.join(os.path.dirname(__file__), "fake_distribution")

def testLoadDistribution():
  dist = Distribution(test_dist)

def testFailedPackage():
  dist = Distribution(test_dist)
  with pytest.raises(DependencyError):
    dist.request_modules(["notapackage"])

def testRepositories():
  dist = Distribution(test_dist)
  dist.request_modules(["repo_module", "root_module"])
  assert dist["repo_module"].path == "cctbx_project/repo_module"
  assert dist["root_module"].path == "root_module"