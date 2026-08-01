"""ITB Kedro project package.

Layout (by concern):

* ``features`` / ``labels`` / ``signals`` / ``classifiers`` / ``backtesting`` — domain modules
* ``orchestration`` — generator dispatch and train/predict feature-set flow
* ``nodes`` — thin Kedro node adapters
* ``catalog`` — datasets and path helpers
* ``worker`` — FastAPI job API + runtime defaults
* ``common`` — shared dataframe / scoring helpers

Kedro entrypoints stay at package root: ``settings``, ``pipeline_registry``, ``hooks``.
The worker is also reachable via ``kedro_pipeline.main:app`` for uvicorn.
"""
__version__ = "0.1.0"
