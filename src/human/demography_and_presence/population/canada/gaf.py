"""Compatibility import for Human population GAF parsing."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.pipelines.population.gaf")
