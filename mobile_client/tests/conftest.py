"""
Make the tests importable from anywhere.

Two paths matter: the repository root, because the code under test imports
``mobile_client.backend.*`` and the seeder modules, and this directory,
because the test modules share ``fakes.py``.
"""

import os
import sys

TESTS_DIR = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.abspath(os.path.join(TESTS_DIR, "..", ".."))

for path in (REPO_ROOT, TESTS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
