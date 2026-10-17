"""Compatibility import; implementation lives in human.pipelines.delivery.run."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.pipelines.delivery.run")
