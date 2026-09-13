"""Download and validate the production embedding snapshot during image build."""

from pathlib import Path

from fastembed import TextEmbedding
from huggingface_hub import snapshot_download

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REPOSITORY = "qdrant/bge-small-en-v1.5-onnx-q"
MODEL_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
MODEL_PATH = Path("/opt/atlas-models/bge-small-en-v1.5")


def main() -> None:
    snapshot_download(
        repo_id=MODEL_REPOSITORY,
        revision=MODEL_REVISION,
        local_dir=MODEL_PATH,
    )
    model = TextEmbedding(
        model_name=MODEL_NAME,
        cache_dir=str(MODEL_PATH.parent),
        threads=2,
        specific_model_path=str(MODEL_PATH),
        local_files_only=True,
    )
    if model.embedding_size != 384:
        raise RuntimeError("pinned embedding model must expose 384 dimensions")


if __name__ == "__main__":
    main()
