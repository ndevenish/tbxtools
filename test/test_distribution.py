# coding: utf-8

import os
from tbxtools import Distribution

test_dist = os.path.join(os.path.dirname(__file__), "fake_distribution")

def testLoadDistribution():
  dist = Distribution(test_dist)

