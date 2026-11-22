"""Expose the pipeline implementation at its original import path."""
import sys

from source.core import pipeline as implementation

sys.modules[__name__] = implementation
