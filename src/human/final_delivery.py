"""Compatibility import; implementation lives in human.pipelines.delivery.finalize."""
if __name__ == "__main__":
    from human.pipelines.delivery.finalize import main
    raise SystemExit(main())
else:
    import sys
    from importlib import import_module
    sys.modules[__name__] = import_module("human.pipelines.delivery.finalize")
