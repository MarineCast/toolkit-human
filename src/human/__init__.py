"""Human population, civil calendar and native infrastructure products."""
from .main import HumanData, get_human_data
from .pipelines.population.static import get_population

__all__ = ["get_human_data", "HumanData", "get_population"]
