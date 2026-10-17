"""Compatibility import; implementation lives in human.pipelines.population.collection."""
if __name__ == "__main__":
    from human.pipelines.population.collection import main
    raise SystemExit(main())
else:
    import sys
    from importlib import import_module
    sys.modules[__name__] = import_module("human.pipelines.population.collection")
