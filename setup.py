#!/usr/bin/env python3
"""setup.py for cli-anything-davinci-resolve. Install with: pip install -e ."""
from setuptools import find_namespace_packages, setup

with open("cli_anything/davinci_resolve/README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="cli-anything-davinci-resolve",
    version="1.0.0",
    description="CLI harness for DaVinci Resolve Studio via its scripting API",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.10,<3.14",
    install_requires=["click>=8.0.0", "prompt-toolkit>=3.0.0"],
    extras_require={"dev": ["pytest>=7.0.0"]},
    entry_points={
        "console_scripts": [
            "cli-anything-davinci-resolve=cli_anything.davinci_resolve.davinci_resolve_cli:main",
        ],
    },
    package_data={"cli_anything.davinci_resolve": ["skills/*.md", "utils/logo.ans"]},
    include_package_data=True,
    zip_safe=False,
)
