"""Timed k-NN search over REST and gRPC with identical semantics."""

import json
import time

from opensearch.protobufs.schemas import common_pb2 as pb

from . import config

# With grouping on, k-NN first takes this many nearest logs, then collapses them into one hit per pattern.
# Large because repeated routine lines can otherwise crowd out rarer, relevant patterns.
GROUP_CANDIDATES = 2000


def _rest_filter(filters):
    clauses = [{"bool": {"should": [{"term": {field: v}} for v in values]}}
               for field, values in (filters or {}).items() if values]
    return {"bool": {"filter": clauses}} if clauses else None


def _grpc_filter(filters):
    clauses = [
        pb.QueryContainer(bool=pb.BoolQuery(should=[
            pb.QueryContainer(term=pb.TermQuery(field=field, value=pb.FieldValue(string=v))) for v in values
        ]))
        for field, values in (filters or {}).items() if values
    ]
    return pb.QueryContainer(bool=pb.BoolQuery(filter=clauses)) if clauses else None


def rest_body(vector, k=10, filters=None, group=False):
    """The JSON body for POST /<index>/_search."""
    knn = {"vector": vector, "k": GROUP_CANDIDATES if group else k}
    f = _rest_filter(filters)
    if f:
        knn["filter"] = f
    body = {"size": k, "_source": {"excludes": ["embedding"]}, "query": {"knn": {"embedding": knn}}}
    if group:
        body["collapse"] = {"field": "pattern", "inner_hits": [
            {"name": name, "size": 1, "sort": [{"@timestamp": {"order": order}}], "_source": ["@timestamp"]}
            for name, order in (("first", "asc"), ("last", "desc"))]}
    return body


def rest_knn(clients, vector, k=10, filters=None, group=False, index=config.SEARCH_INDEX):
    """Timing includes JSON-encoding the request and parsing the JSON response."""
    t = time.perf_counter()
    data = json.dumps(rest_body(vector, k, filters, group)).encode()
    raw, resp = clients.post(f"/{index}/_search", data, timeout=60)
    if "error" in resp:
        raise RuntimeError(resp["error"].get("reason", resp["error"]))
    hits = []
    for h in resp["hits"]["hits"]:
        hit = {"_id": h["_id"], "score": h["_score"], **h["_source"]}
        if group:
            first, last = h["inner_hits"]["first"]["hits"], h["inner_hits"]["last"]["hits"]
            hit.update(count=first["total"]["value"], first=first["hits"][0]["_source"]["@timestamp"],
                       last=last["hits"][0]["_source"]["@timestamp"])
        hits.append(hit)
    seconds = time.perf_counter() - t
    return {"seconds": seconds, "req_bytes": len(data), "resp_bytes": len(raw),
            "server_took_ms": resp.get("took", 0), "hits": hits}


def _grpc_inner_hits(name, order):
    return pb.InnerHits(
        name=name, size=1,
        sort=[pb.SortCombinations(field_with_order=pb.FieldSortMap(
            field_sort_map={"@timestamp": pb.FieldSort(order=order)}))],
        x_source=pb.SourceConfig(filter=pb.SourceFilter(includes=["@timestamp"])),
    )


def _grpc_total(hits_metadata):
    total = hits_metadata.total
    return total.total_hits.value if total.HasField("total_hits") else total.int64


def grpc_request(vector, k=10, filters=None, group=False, index=config.SEARCH_INDEX):
    """The protobuf SearchRequest for SearchService/Search."""
    knn = pb.KnnQuery(field="embedding", vector=vector, k=GROUP_CANDIDATES if group else k)
    f = _grpc_filter(filters)
    if f is not None:
        knn.filter.CopyFrom(f)
    body = pb.SearchRequestBody(
        size=k,
        x_source=pb.SourceConfig(filter=pb.SourceFilter(excludes=["embedding"])),
        query=pb.QueryContainer(knn=knn),
    )
    if group:
        body.collapse.CopyFrom(pb.FieldCollapse(field="pattern", inner_hits=[
            _grpc_inner_hits("first", pb.SORT_ORDER_ASC), _grpc_inner_hits("last", pb.SORT_ORDER_DESC)]))
    return pb.SearchRequest(index=[index], search_request_body=body)


def grpc_knn(clients, vector, k=10, filters=None, group=False, index=config.SEARCH_INDEX):
    """Timing includes building the protobuf request and decoding the protobuf response."""
    t = time.perf_counter()
    req = grpc_request(vector, k, filters, group, index)
    resp = clients.search.Search(req, timeout=60, metadata=clients.grpc_auth)
    hits = []
    for h in resp.hits.hits:
        hit = {"_id": h.x_id, "score": h.x_score.double, **json.loads(h.x_source)}
        if group:
            first, last = h.inner_hits["first"].hits, h.inner_hits["last"].hits
            hit.update(count=_grpc_total(first), first=json.loads(first.hits[0].x_source)["@timestamp"],
                       last=json.loads(last.hits[0].x_source)["@timestamp"])
        hits.append(hit)
    seconds = time.perf_counter() - t
    return {"seconds": seconds, "req_bytes": req.ByteSize(), "resp_bytes": resp.ByteSize(),
            "server_took_ms": resp.took, "hits": hits}


SEARCHERS = {"REST": rest_knn, "gRPC": grpc_knn}


def compare(clients, vector, k=10, filters=None, group=False, repeats=1):
    """Runs both protocols `repeats` times, alternating who goes first. Returns per-protocol runs."""
    runs = {"REST": [], "gRPC": []}
    for i in range(repeats):
        order = ["REST", "gRPC"] if i % 2 == 0 else ["gRPC", "REST"]
        for p in order:
            runs[p].append(SEARCHERS[p](clients, vector, k, filters, group))
    return runs
