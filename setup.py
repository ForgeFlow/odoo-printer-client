from pathlib import Path
from setuptools import setup, find_packages

setup(
    name="odoo-print-client",
    version="1.0.0",
    description="A local WebSocket print client for Odoo, zero footprint.",
    long_description=Path("README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    author="David Jiménez",
    author_email="david.jimenez@forgeflow.com",
    url="https://github.com/forgeflow/odoo-print-client",
    license="LGPL-3.0-only",
    python_requires=">=3.7",
    packages=find_packages(exclude=["venv*"]),
    install_requires=[
        "requests",
        "websocket-client",
        "python-dotenv",
    ],
    entry_points={
        "console_scripts": [
            "odoo-printer=odoo_print_client.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Printing",
    ],
)