"""Compatibility import; implementation lives in human.utils.study."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("human.utils.study")
