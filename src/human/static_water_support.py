"""Compatibility import; implementation lives in human.pipelines.population.water_support."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.pipelines.population.water_support")
