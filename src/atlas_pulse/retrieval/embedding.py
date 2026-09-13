"""Free local ONNX embeddings with strict vector validation."""

import asyncio
from collections.abc import Iterable
from importlib import import_module
from math import isfinite, sqrt
from typing import Protocol, cast

from atlas_pulse.retrieval.base import Embedding


class _FastEmbedModel(Protocol):
    embedding_size: int

    def passage_embed(self, texts: Iterable[str]) -> Iterable[Iterable[float]]: ...

    def query_embed(self, texts: Iterable[str]) -> Iterable[Iterable[float]]: ...


class FastEmbedProvider:
    """Lazy CPU embedding provider backed by FastEmbed and ONNX Runtime."""

    def __init__(
        self,
        *,
        model_name: str,
        dimensions: int,
        cache_dir: str,
        threads: int,
        model_path: str | None = None,
        local_files_only: bool = False,
    ) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._cache_dir = cache_dir
        self._threads = threads
        self._model_path = model_path
        self._local_files_only = local_files_only
        self._model: _FastEmbedModel | None = None
        self._load_lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _create_model(self) -> _FastEmbedModel:
        model_class = import_module("fastembed").TextEmbedding
        model = cast(
            _FastEmbedModel,
            model_class(
                model_name=self._model_name,
                cache_dir=self._cache_dir,
                threads=self._threads,
                specific_model_path=self._model_path,
                local_files_only=self._local_files_only,
            ),
        )
        if model.embedding_size != self._dimensions:
            raise ValueError(
                f"embedding model exposes {model.embedding_size} dimensions; "
                f"expected {self._dimensions}"
            )
        return model

    async def _loaded_model(self) -> _FastEmbedModel:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._create_model)
        return self._model

    def _validated(self, values: Iterable[float]) -> Embedding:
        vector = tuple(float(value) for value in values)
        if len(vector) != self._dimensions:
            raise ValueError(f"embedding has {len(vector)} dimensions; expected {self._dimensions}")
        if not all(isfinite(value) for value in vector):
            raise ValueError("embedding must contain only finite values")
        norm = sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("embedding must not be the zero vector")
        return tuple(value / norm for value in vector)

    async def embed_documents(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        model = await self._loaded_model()

        def embed() -> tuple[Embedding, ...]:
            return tuple(self._validated(vector) for vector in model.passage_embed(texts))

        vectors = await asyncio.to_thread(embed)
        if len(vectors) != len(texts):
            raise RuntimeError("embedding model returned a different number of vectors")
        return vectors

    async def embed_query(self, text: str) -> Embedding:
        model = await self._loaded_model()

        def embed() -> Embedding:
            vectors = tuple(model.query_embed((text,)))
            if len(vectors) != 1:
                raise RuntimeError("embedding model did not return exactly one query vector")
            return self._validated(vectors[0])

        return await asyncio.to_thread(embed)
