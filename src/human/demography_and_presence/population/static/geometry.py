"""Compatibility import; implementation lives in human.pipelines.population.static.geometry."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.pipelines.population.static.geometry")
