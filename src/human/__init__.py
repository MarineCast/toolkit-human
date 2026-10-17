"""The supported three-product Human pipeline, with legacy helper compatibility."""

__all__ = ["get_human_data"]


def get_population(**kwargs):
    """Estimate static residents in H3 cells and geodesic neighborhoods.

    See :func:`human.demography_and_presence.population.static.get_population` for inputs and result documentation.
    Imports population dependencies only when called; no AIS/viewshed initialization.
    """
    from .pipelines.population.static import get_population as implementation
    return implementation(**kwargs)


def get_human_data(*args, **kwargs):
    """Collect and produce the three final Human Parquets; clean run intermediates.

    See :func:`human.pipeline.get_human_data` for configuration and result paths.
    """
    from .pipelines.delivery.run import get_human_data as implementation
    return implementation(*args, **kwargs)
