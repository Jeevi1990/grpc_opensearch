"""Concurrent load test: many in-flight k-NN searches, like a busy application server."""

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from opensearch.protobufs.schemas import common_pb2 as pb

from . import config, search


def run_load(clients, protocol, vectors, concurrency, requests=1000, k=10, warmup=None):
    """Both protocols share ONE client across all threads:
    REST = urllib3 pool (HTTP/1.1: a separate TLS connection per in-flight request),
    gRPC = one channel (HTTP/2: all requests multiplexed over a single connection)."""
    fn = search.SEARCHERS[protocol]

    def one(i):
        t = time.perf_counter()
        fn(clients, vectors[i % len(vectors)], k)
        return time.perf_counter() - t

    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(one, range(warmup if warmup is not None else concurrency * 3)))
        cpu0, t0 = time.process_time(), time.perf_counter()
        latencies = list(pool.map(one, range(requests)))
        wall, cpu = time.perf_counter() - t0, time.process_time() - cpu0
    ms = sorted(x * 1000 for x in latencies)
    q = statistics.quantiles(ms, n=100)
    return {"protocol": protocol, "concurrency": concurrency, "requests": requests,
            "qps": requests / wall, "p50": q[49], "p95": q[94], "p99": q[98],
            "cpu_ms_per_req": cpu / requests * 1000}


def run_sweep(clients, vectors, levels=(1, 8, 32, 64), requests=1000, k=10, progress=None):
    """Runs each concurrency level for both protocols, alternating who goes first."""
    results = []
    for i, c in enumerate(levels):
        order = ["REST", "gRPC"] if i % 2 == 0 else ["gRPC", "REST"]
        for p in order:
            results.append(run_load(clients, p, vectors, c, requests, k))
            if progress:
                progress(len(results), len(levels) * 2, results[-1])
    return results


def encode_cost(vector, k=10, n=2000):
    """Median microseconds to build + serialize the same k-NN request (no network, no TLS)."""
    def rest():
        return json.dumps({"size": k, "_source": {"excludes": ["embedding"]},
                           "query": {"knn": {"embedding": {"vector": vector, "k": k}}}}).encode()

    def grpc():
        return pb.SearchRequest(index=[config.SEARCH_INDEX], search_request_body=pb.SearchRequestBody(
            size=k, x_source=pb.SourceConfig(filter=pb.SourceFilter(excludes=["embedding"])),
            query=pb.QueryContainer(knn=pb.KnnQuery(field="embedding", vector=vector, k=k)))).SerializeToString()

    def median_us(f):
        times = []
        for _ in range(n):
            t = time.perf_counter()
            f()
            times.append(time.perf_counter() - t)
        return statistics.median(times) * 1e6

    return {"REST": median_us(rest), "gRPC": median_us(grpc)}
