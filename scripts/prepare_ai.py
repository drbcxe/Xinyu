"""Initialize AI infrastructure; optional local model download uses only configured D: cache."""

import argparse
import time

from app.services.graph_checkpoints import prepare_checkpoints
from app.services.relationship_rag import backfill_indexes, embedder, ensure_collection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-model", action="store_true")
    args = parser.parse_args()
    prepare_checkpoints()
    for attempt in range(15):
        try:
            ensure_collection()
            break
        except Exception:
            if attempt == 14:
                raise
            time.sleep(2)
    if args.download_model:
        vector = next(
            iter(embedder(allow_download=True).embed(["心语的本地中文语义检索"]))
        )
        print(f"Local embedding ready: {len(vector)} dimensions")
    backfill_indexes()
    print("LangGraph checkpoints and Qdrant collection ready")


if __name__ == "__main__":
    main()
