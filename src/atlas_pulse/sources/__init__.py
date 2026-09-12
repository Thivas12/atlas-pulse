"""Adapters for authoritative public data sources."""

from atlas_pulse.sources.base import FetchedDocument, NormalizedBatch, SourceAdapter
from atlas_pulse.sources.http import RetryableSourceError
from atlas_pulse.sources.nws import NWSAlertCollection, NWSClient
from atlas_pulse.sources.usgs import USGSClient, USGSFeed

__all__ = [
    "FetchedDocument",
    "NWSAlertCollection",
    "NWSClient",
    "NormalizedBatch",
    "RetryableSourceError",
    "SourceAdapter",
    "USGSClient",
    "USGSFeed",
]
