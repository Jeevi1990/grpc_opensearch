"""Streaming log assistant (RAG): gRPC k-NN retrieval + streamed answer from a local Ollama LLM."""

import json
import time

import requests

from . import config, ml, search

SYSTEM_PROMPT = """You are an SRE assistant analysing application logs.
Answer ONLY from the log patterns provided. They are ordered by relevance to the question; ignore patterns
that are unrelated to the question. If the logs don't contain the answer, say so.
Be concise and use this structure:
**Summary** (1-2 sentences)
**Affected services and time window**
**Likely root cause**
**Suggested next steps** (2-3 bullets)
Quote exact error messages and timestamps when useful."""


def format_patterns(groups):
    lines = []
    for i, g in enumerate(groups, 1):
        when = g["first"] if g["first"] == g["last"] else f"{g['first']} to {g['last']}"
        lines.append(f"{i}. {g['level']} {g['service']} - {g['count']} matching lines, {when}\n"
                     f"   e.g. \"{g['message']}\"")
    return "\n".join(lines)


def retrieve(clients, model_id, question, patterns=12, filters=None):
    """Embeds the question, then runs a grouped gRPC k-NN search: one hit per log pattern."""
    t = time.perf_counter()
    vector = ml.embed_query(clients, model_id, question)
    embed_s = time.perf_counter() - t
    result = search.grpc_knn(clients, vector, k=patterns, filters=filters, group=True)
    return {"embed_seconds": embed_s, **result}


def stream_answer(question, groups, history=None, timing=None):
    """Yields answer text chunks from Ollama. `timing` (a dict) receives time-to-first-token and total time."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-4:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": (
        f"Question: {question}\n\nLog patterns (most relevant first):\n{format_patterns(groups)}")})

    t = time.perf_counter()
    with requests.post(f"{config.OLLAMA_URL}/api/chat", stream=True, timeout=300, json={
        "model": config.LLM_MODEL, "messages": messages, "stream": True, "keep_alive": "30m",
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            text = chunk.get("message", {}).get("content", "")
            if text:
                if timing is not None and "ttft" not in timing:
                    timing["ttft"] = time.perf_counter() - t
                yield text
            if chunk.get("done"):
                break
    if timing is not None:
        timing["total"] = time.perf_counter() - t


def warm_up():
    """Loads the LLM into memory so the first demo question doesn't pay the model load time."""
    requests.post(f"{config.OLLAMA_URL}/api/generate", timeout=120, json={
        "model": config.LLM_MODEL, "prompt": "", "keep_alive": "30m", "options": {"num_ctx": 4096}})


def ollama_ok():
    try:
        tags = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3).json()
        names = {m["name"] for m in tags.get("models", [])}
        return config.LLM_MODEL in names, names
    except requests.RequestException:
        return False, set()
