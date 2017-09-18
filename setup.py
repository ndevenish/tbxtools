#!/usr/bin/env python

from setuptools import setup

setup(
    name='tbxtools',
    packages=["tbxtools"],
    version='0.1.0',
    description='Tools for introspecting and working with a tbx distribution',
    entry_points = {
        'console_scripts': [
          'tbx-expand-deps=tbxtools.info_tools:run_expand_dependencies',
          # 'tbx2cmake=tbx2cmake.write_cmake:main'
        ],
    },
    # install_requires=["enum34", "docopt", "networkx", "pyyaml", "mock"],
)
