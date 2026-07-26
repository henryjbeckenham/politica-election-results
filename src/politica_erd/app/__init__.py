"""Local operator application for governed election-result ingestion."""


def create_app(*args, **kwargs):
    """Load the web layer lazily so service/reader imports have no app side effects."""

    from .api import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["create_app"]
