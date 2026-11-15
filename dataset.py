"""Generate + embed the logs once and cache them, so ingest timings measure only the protocols."""

import json

from . import config, loggen, ml


def dataset_path(n):
    return config.DATA_DIR / f"logs_{n}_{config.EMBED_MODEL.replace(':', '_')}.jsonl"


def load_dataset(n):
    path = dataset_path(n)
    if not path.exists():
        return None
    with path.open() as f:
        docs = [json.loads(line) for line in f]
    for d in docs:
        d.setdefault("pattern", loggen.message_pattern(d["message"]))
    return docs


def build_dataset(clients, model_id, n=10_000, batch=250, progress=None):
    logs = loggen.generate_logs(n)
    for i in range(0, n, batch):
        chunk = logs[i:i + batch]
        vectors = ml.embed(clients, model_id, [config.DOC_PREFIX + loggen.embedding_text(d) for d in chunk])
        for d, v in zip(chunk, vectors):
            d["embedding"] = [round(x, 6) for x in v]
        if progress:
            progress(min(i + batch, n), n)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dataset_path(n).with_suffix(".tmp")
    with tmp.open("w") as f:
        for d in logs:
            f.write(json.dumps(d) + "\n")
    tmp.replace(dataset_path(n))
    return logs
