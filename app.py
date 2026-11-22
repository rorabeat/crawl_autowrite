"""Compatibility entry point for the reorganized desktop application."""
import sys

from source.ui import app as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())

sys.modules[__name__] = implementation
