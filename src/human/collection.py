"""Compatibility import; implementation lives in human.pipelines.native.collection."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.pipelines.native.collection")
