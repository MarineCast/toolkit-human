"""Compatibility import; implementation lives in human.pipelines.calendar.lookup."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.pipelines.calendar.lookup")
