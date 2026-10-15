"""Human population, activity and access products."""


def get_population(**kwargs):
    """Estimate static residents in H3 cells and geodesic neighborhoods.

    See :func:`human.demography_and_presence.population.static.get_population` for inputs and result documentation.
    Imports population dependencies only when called; no AIS/viewshed initialization.
    """
    from .demography_and_presence.population.static import get_population as implementation
    return implementation(**kwargs)
