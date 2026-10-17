"""Compatibility import; implementation lives in human.pipelines.native.features."""
if __name__ == "__main__":
    from human.pipelines.native.features import main
    raise SystemExit(main())
else:
    import sys
    from importlib import import_module
    sys.modules[__name__] = import_module("human.pipelines.native.features")
