"""Adapters for authoritative public data sources."""

from atlas_pulse.sources.usgs import FetchedDocument, USGSClient, USGSFeed

__all__ = ["FetchedDocument", "USGSClient", "USGSFeed"]
