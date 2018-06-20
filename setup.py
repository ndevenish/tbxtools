#!/usr/bin/env python

from setuptools import setup

setup(
    name='tbxtools',
    packages=["tbxtools", "tbxtools.tbx2cmake"],
    version='0.1.0',
    description='Tools for introspecting and working with a tbx distribution',
    entry_points = {
        'console_scripts': [
          'tbx-expand-deps=tbxtools.info_tools:run_expand_dependencies',
          # 'tbx2cmake=tbx2cmake.write_cmake:main'
          'tbx2depfile=tbxtools.tbx2cmake.read_scons:main',
          'tbx2cmake=tbxtools.tbx2cmake.write_cmake:main'
        ],
    },
    install_requires=['enum34;python_version<"3"',
      "docopt", "networkx~=2.1", "pyyaml", "mock",],
    package_data={
      'tbxtools.tbx2cmake': ['build_info.yaml'],
    },
)
