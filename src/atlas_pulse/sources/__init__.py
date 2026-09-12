"""Adapters for authoritative public data sources."""

from atlas_pulse.sources.base import FetchedDocument, NormalizedBatch, SourceAdapter
from atlas_pulse.sources.firms import FIRMSClient, FIRMSFeed
from atlas_pulse.sources.http import PermanentSourceError, RetryableSourceError
from atlas_pulse.sources.nws import NWSAlertCollection, NWSClient
from atlas_pulse.sources.usgs import USGSClient, USGSFeed

__all__ = [
    "FIRMSClient",
    "FIRMSFeed",
    "FetchedDocument",
    "NWSAlertCollection",
    "NWSClient",
    "NormalizedBatch",
    "PermanentSourceError",
    "RetryableSourceError",
    "SourceAdapter",
    "USGSClient",
    "USGSFeed",
]
