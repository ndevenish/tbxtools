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
