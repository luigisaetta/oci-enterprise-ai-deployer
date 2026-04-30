"""
Author: L. Saetta
Last modified: 2026-04-30
License: MIT

Description:
    Shared pytest fixtures for the OCI Enterprise AI deployer tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def project_on_sys_path() -> None:
    """Ensure the repository root is importable during tests."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
