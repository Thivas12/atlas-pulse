"""Adapters for authoritative public data sources."""

from atlas_pulse.sources.base import FetchedDocument, NormalizedBatch, SourceAdapter
from atlas_pulse.sources.firms import FIRMSClient, FIRMSFeed
from atlas_pulse.sources.gdelt import GDELTClient, GDELTExportPointer, GDELTFeed
from atlas_pulse.sources.http import PermanentSourceError, RetryableSourceError, SourceFetchError
from atlas_pulse.sources.nws import NWSAlertCollection, NWSClient
from atlas_pulse.sources.usgs import USGSClient, USGSFeed

__all__ = [
    "FIRMSClient",
    "FIRMSFeed",
    "FetchedDocument",
    "GDELTClient",
    "GDELTExportPointer",
    "GDELTFeed",
    "NWSAlertCollection",
    "NWSClient",
    "NormalizedBatch",
    "PermanentSourceError",
    "RetryableSourceError",
    "SourceAdapter",
    "SourceFetchError",
    "USGSClient",
    "USGSFeed",
]
