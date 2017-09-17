#!/usr/bin/env python

from setuptools import setup

setup(
    name='tbxtools',
    packages=["tbxtools"],
    version='0.1.0',
    description='Tools for introspecting and working with a tbx distribution',
    # entry_points = {
    #     'console_scripts': [
    #       'tbx2depfile=tbx2cmake.read_scons:main',
    #       'tbx2cmake=tbx2cmake.write_cmake:main'
    #     ],
    # },
    # install_requires=["enum34", "docopt", "networkx", "pyyaml", "mock"],
)
