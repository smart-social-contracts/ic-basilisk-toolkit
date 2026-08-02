from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="ic-basilisk-toolkit",
    version="0.5.2",
    author="Smart Social Contracts",
    author_email="contact@smartsocialcontracts.org",
    description="Basilisk Toolkit — Services, shell, and SFTP for IC Python canisters",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/smart-social-contracts/ic-basilisk-toolkit",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Internet",
    ],
    python_requires=">=3.10",
    install_requires=[
        "ic-basilisk>=0.11.0",
        "ic-python-db>=0.9.0",
        "ic-python-logging>=0.3.1",
    ],
    extras_require={
        "shell": ["asyncssh"],
        "test": ["pytest>=7.0.0", "pytest-cov>=4.0.0"],
    },
    entry_points={
        "console_scripts": [
            "basilisk-toolkit=ic_basilisk_toolkit.cli:main",
        ],
    },
)
