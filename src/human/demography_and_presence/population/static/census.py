"""Compatibility import; implementation lives in human.pipelines.population.static.census."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.pipelines.population.static.census")
