"""Index setup and timed bulk ingest over REST and gRPC."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

from opensearch.protobufs.schemas import common_pb2 as pb

from . import config


def create_index(clients, index, dim=config.EMBED_DIM):
    clients.call("DELETE", f"/{index}")
    r = clients.call("PUT", f"/{index}", {
        "settings": {
            "index.knn": True, "number_of_shards": 1, "number_of_replicas": 0,
            # Logs repeat the same message many times, and HNSW graphs lose recall on large groups of identical
            # vectors (measured recall@10: 0.85 HNSW vs 1.00 exact). Below this threshold no graph is built, so
            # the knn query runs an exact scan, which is still only a few ms at this scale.
            "index.knn.advanced.approximate_threshold": 1_000_000,
        },
        "mappings": {"properties": {
            "@timestamp": {"type": "date"},
            "log_id": {"type": "keyword"},
            "service": {"type": "keyword"},
            "level": {"type": "keyword"},
            "host": {"type": "keyword"},
            "region": {"type": "keyword"},
            "trace_id": {"type": "keyword"},
            "message": {"type": "text"},
            "pattern": {"type": "keyword"},
            "status_code": {"type": "integer"},
            "latency_ms": {"type": "integer"},
            "embedding": {"type": "knn_vector", "dimension": dim,
                          "method": {"name": "hnsw", "engine": "faiss", "space_type": "cosinesimil"}},
        }},
    })
    if not r.get("acknowledged"):
        raise RuntimeError(f"Could not create {index}: {r}")


def rest_bulk(clients, index, batch):
    """Timing includes building the NDJSON body and parsing the JSON response."""
    t = time.perf_counter()
    lines = []
    for d in batch:
        lines.append(json.dumps({"index": {"_id": d["log_id"]}}))
        lines.append(json.dumps(d))
    body = ("\n".join(lines) + "\n").encode()
    raw, resp = clients.post(f"/{index}/_bulk", body, content_type="application/x-ndjson")
    seconds = time.perf_counter() - t
    errors = sum(1 for it in resp.get("items", []) if next(iter(it.values())).get("error"))
    return {"seconds": seconds, "req_bytes": len(body), "resp_bytes": len(raw),
            "server_took_ms": resp.get("took", 0), "errors": errors}


def grpc_bulk(clients, index, batch):
    """Timing includes building the protobuf request and decoding the protobuf response."""
    t = time.perf_counter()
    req = pb.BulkRequest(index=index, bulk_request_body=[
        pb.BulkRequestBody(
            operation_container=pb.OperationContainer(index=pb.IndexOperation(x_id=d["log_id"])),
            object=json.dumps(d).encode(),
        )
        for d in batch
    ])
    resp = clients.docs.Bulk(req, timeout=120, metadata=clients.grpc_auth)
    seconds = time.perf_counter() - t
    errors = sum(1 for it in resp.items if getattr(it, it.WhichOneof("item")).HasField("error"))
    return {"seconds": seconds, "req_bytes": req.ByteSize(), "resp_bytes": resp.ByteSize(),
            "server_took_ms": resp.took, "errors": errors}


def run_ingest(clients, docs, batch_size=500, protocols=("REST", "gRPC")):
    """Yields one result per (batch, protocol). Protocols alternate who goes first so neither is favored."""
    targets = {"REST": (rest_bulk, config.INDEX_REST), "gRPC": (grpc_bulk, config.INDEX_GRPC)}
    for p in protocols:
        create_index(clients, targets[p][1])
    batches = [docs[i:i + batch_size] for i in range(0, len(docs), batch_size)]
    done = {p: 0 for p in protocols}
    for i, batch in enumerate(batches):
        order = list(protocols) if i % 2 == 0 else list(reversed(protocols))
        for p in order:
            fn, index = targets[p]
            result = fn(clients, index, batch)
            done[p] += len(batch)
            yield {"protocol": p, "batch": i + 1, "batches": len(batches), "docs": len(batch),
                   "docs_done": done[p], **result}
    for p in protocols:
        clients.call("POST", f"/{targets[p][1]}/_refresh")


def doc_count(clients, index):
    r = clients.call("GET", f"/{index}/_count")
    return r.get("count", 0)


SHIPPER_INDEX = {"REST": "logs-shipper-rest", "gRPC": "logs-shipper-grpc"}


def shipper_throughput(clients, protocol, docs, batch, parallel):
    """Docs/s when `parallel` senders push small batches at once (like log shippers). Uses its own index."""
    fn = rest_bulk if protocol == "REST" else grpc_bulk
    index = SHIPPER_INDEX[protocol]
    create_index(clients, index)
    batches = [docs[i:i + batch] for i in range(0, len(docs), batch)]
    t = time.perf_counter()
    with ThreadPoolExecutor(parallel) as pool:
        results = list(pool.map(lambda b: fn(clients, index, b), batches))
    seconds = time.perf_counter() - t
    if sum(r["errors"] for r in results):
        raise RuntimeError(f"{protocol} shipper ingest had errors")
    return len(docs) / seconds


def compare_shippers(clients, docs, batch, parallel, repeats=2):
    """Runs both protocols `repeats` times, alternating who goes first, then removes the scratch indexes."""
    runs = {"REST": [], "gRPC": []}
    for i in range(repeats):
        for p in (["REST", "gRPC"] if i % 2 == 0 else ["gRPC", "REST"]):
            runs[p].append(shipper_throughput(clients, p, docs, batch, parallel))
    for index in SHIPPER_INDEX.values():
        clients.call("DELETE", f"/{index}")
    return runs
