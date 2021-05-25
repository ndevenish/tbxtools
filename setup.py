# -*- coding: utf-8 -*-
from setuptools import setup

package_dir = {"": "src"}

packages = ["tbxtools", "tbxtools.tbx2cmake"]

package_data = {"": ["*"]}

install_requires = [
    "PyYAML>=5.4.1,<6.0.0",
    "bowler>=0.8.0,<0.9.0",
    "docopt>=0.6.2,<0.7.0",
    "fissix>=19.2b1,<20.0",
    "networkx>=2.5.1,<3.0.0",
]

entry_points = {
    "console_scripts": [
        "tbx-expand-deps = " "tbxtools.info_tools:run_expand_dependencies",
        "tbx2cmake = tbxtools.tbx2cmake.write_cmake:main",
        "tbx2depfile = tbxtools.tbx2cmake.read_scons:main",
    ]
}

setup_kwargs = {
    "name": "tbxtools",
    "version": "0.1.0",
    "description": "",
    "long_description": None,
    "author": "Nicholas Devenish",
    "author_email": "ndevenish@gmail.com",
    "maintainer": None,
    "maintainer_email": None,
    "url": None,
    "package_dir": package_dir,
    "packages": packages,
    "package_data": package_data,
    "install_requires": install_requires,
    "entry_points": entry_points,
    "python_requires": ">=3.6,<4.0",
}


setup(**setup_kwargs)
