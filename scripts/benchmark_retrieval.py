"""Local synthetic BM25/SQL benchmark; no model calls, production data, or SLA claims."""

import argparse
import gc
import hashlib
import json
import math
import platform
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from uuid import uuid4

import psutil
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, get_settings
from app.errors import AppError
from app.models.relationship import RelationshipItem, RelationshipProfile
from app.repositories.database import get_engine
from app.services.relationship_rag import retrieve
from app.services.retrieval_cache import corpus_cache
from quality.environment import isolated_space
from quality.run import code_fingerprint

QUERIES = ["蓝色杯子", "咖啡与散步", "周末安排"]


class MemorySampler:
    def __enter__(self):
        self.process = psutil.Process()
        self.start = self.process.memory_info().rss
        self.peak = self.start
        self.stop = threading.Event()

        def sample():
            while not self.stop.wait(0.025):
                self.peak = max(self.peak, self.process.memory_info().rss)

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.peak = max(self.peak, self.process.memory_info().rss)
        self.stop.set()
        self.thread.join(timeout=2)


def stats(values):
    values = sorted(values)
    return {
        "samples": len(values),
        "p50_ms": round(median(values), 3),
        "p95_ms": round(values[max(0, math.ceil(len(values) * 0.95) - 1)], 3),
        "max_ms": round(max(values), 3),
    }


def seed(owner, documents, target):
    with Session(get_engine()) as db:
        db.execute(delete(RelationshipItem).where(RelationshipItem.owner == owner))
        rows = []
        raw_bytes = 0
        for i in range(documents):
            line = f"第{i}份合成聊天：今天聊了咖啡、散步、工作和周末安排，明天再一起讨论。\n"
            repeat = max(1, target // documents // len(line.encode()))
            content = line * repeat
            if i == 0:
                content += "\n唯一线索：她喜欢周三使用蓝色杯子。"
            raw_bytes += len(content.encode())
            rows.append(
                RelationshipItem(
                    id=str(uuid4()),
                    owner=owner,
                    kind="chat_log",
                    title=f"合成记录{i}",
                    quote="",
                    status="active",
                    created_at=datetime.now(UTC),
                    revision=1,
                    details={"text": content},
                )
            )
        db.add_all(rows)
        db.commit()
    corpus_cache.clear(reset=True)
    gc.collect()
    return raw_bytes


def query(owner, value):
    started = time.perf_counter()
    with Session(get_engine()) as db:
        result = retrieve(db, owner, value, top_k=3, rerank=False)
    return (time.perf_counter() - started) * 1000, result


def measure(owner, repetitions, cached, reference):
    corpus_cache.clear(reset=True)
    gc.collect()
    values = []
    chunks = 0
    with MemorySampler() as memory:
        for i in range(repetitions):
            if not cached:
                corpus_cache.clear()
            value = QUERIES[i % len(QUERIES)]
            milliseconds, result = query(owner, value)
            values.append(milliseconds)
            chunks = result.get("lexical_chunks", 0)
            found = [(row["source_id"], row["quote"]) for row in result["sources"]]
            if value in reference:
                assert reference[value] == found, (
                    "Ranking changed across identical benchmark data"
                )
            else:
                reference[value] = found
        counters = corpus_cache.stats()
    return {
        **stats(values),
        "cache": counters,
        "lexical_chunks": chunks,
        "rss_before_mib": round(memory.start / 1024**2, 2),
        "sampled_peak_rss_mib": round(memory.peak / 1024**2, 2),
        "sampled_rss_growth_mib": round((memory.peak - memory.start) / 1024**2, 2),
        "latencies_ms": [round(value, 3) for value in values],
    }


def measure_concurrency(owners):
    def safe_query(i):
        try:
            return {
                "completed": True,
                "ms": query(owners[i % len(owners)], QUERIES[i % 3])[0],
            }
        except AppError as exc:
            return {"completed": False, "error": exc.code}

    with MemorySampler() as memory, ThreadPoolExecutor(max_workers=4) as pool:
        started = time.perf_counter()
        results = list(pool.map(safe_query, range(8)))
    completed = [item["ms"] for item in results if item["completed"]]
    return {
        **(stats(completed) if completed else {}),
        "requests": 8,
        "completed": len(completed),
        "owners": len(owners),
        "rejections": [item["error"] for item in results if not item["completed"]],
        "wall_seconds": round(time.perf_counter() - started, 3),
        "sampled_peak_rss_mib": round(memory.peak / 1024**2, 2),
        "cache": corpus_cache.stats(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=9)
    parser.add_argument("--max-mib", type=int, choices=[2, 8, 16], default=8)
    args = parser.parse_args()
    if not 6 <= args.repetitions <= 30:
        parser.error("repetitions must be 6–30")
    report = {
        "data_origin": "synthetic",
        "scope": "BM25 + SQL only; embeddings/rerank/model/HTTP excluded",
        "python": platform.python_version(),
        "os": platform.system() + " " + platform.release(),
        "cpu": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "memory_gib": round(psutil.virtual_memory().total / 1024**3, 2),
        "source_code_sha256": code_fingerprint(),
        "benchmark_sha256": hashlib.sha256(
            Path(__file__).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT
        )
        .decode()
        .strip(),
        "sampling_interval_ms": 25,
        "workloads": [],
        "resources_cleaned": False,
        "note": "Local observed workload, not a production capacity or latency guarantee. Includes cache preparation in measured runs. RSS includes native/shared memory; sampling may miss short peaks.",
    }
    with isolated_space() as space:
        get_settings().rag_dense_enabled = False
        report["cache_limits"] = {
            "bytes": get_settings().rag_cache_bytes,
            "entries": get_settings().rag_cache_entries,
            "ttl_seconds": get_settings().rag_cache_ttl_seconds,
            "cold_build_concurrency_per_process": 2,
        }
        with Session(get_engine()) as db:
            for value in ["a" * 64, "b" * 64]:
                db.add(
                    RelationshipProfile(owner=value, partner_name="合成", style="合成")
                )
            db.commit()
            report["postgres_version"] = db.scalar(text("SHOW server_version"))
        workloads = [(20, 256 * 1024), (100, 2 * 1024**2)]
        if args.max_mib >= 8:
            workloads.append((300, 8 * 1024**2))
        if args.max_mib >= 16:
            workloads.append((450, 16 * 1024**2))
        for documents, target in workloads:
            size = seed("a" * 64, documents, target)
            reference = {}
            row = {
                "documents": documents,
                "raw_utf8_bytes": size,
                "uncached": measure("a" * 64, args.repetitions, False, reference),
                "cached": measure("a" * 64, args.repetitions, True, reference),
            }
            row["identical_rankings"] = True
            row["concurrent_4"] = measure_concurrency(["a" * 64])
            seed("b" * 64, documents, target)
            row["cross_owner_concurrent_4"] = measure_concurrency(["a" * 64, "b" * 64])
            report["workloads"].append(row)
            print(f"completed {documents} documents, {size} bytes", flush=True)
    report["resources_cleaned"] = True
    folder = PROJECT_ROOT / ".runtime" / "benchmarks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("retrieval-" + space.run_id + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print("REPORT " + str(path), flush=True)


if __name__ == "__main__":
    main()
