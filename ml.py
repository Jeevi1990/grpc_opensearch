"""Embedding model inside OpenSearch ML Commons (a remote connector to Ollama nomic-embed-text)."""

import os
import time

from . import config


def configure_cluster_for_ollama(clients):
    """Allows ML Commons connectors to call the Ollama endpoint on a private network (e.g. a Docker network).
    Only runs when OS_CONFIGURE_ML=true, and appends to existing allow-lists instead of replacing them."""
    if os.environ.get("OS_CONFIGURE_ML", "").lower() != "true":
        return
    endpoint = "^http://" + config.OLLAMA_FROM_OPENSEARCH.replace(".", r"\.") + "/.*$"
    current = clients.call("GET", "/_cluster/settings?flat_settings=true").get("persistent", {})
    settings = {
        "plugins.ml_commons.only_run_on_ml_node": False,
        "plugins.ml_commons.connector.private_ip_enabled": True,
    }
    for key in ("plugins.ml_commons.trusted_connector_endpoints_regex",
                "plugins.ml_commons.trusted_connector_private_endpoints_regex"):
        existing = current.get(key, [])
        if endpoint not in existing:
            settings[key] = existing + [endpoint]
    r = clients.call("PUT", "/_cluster/settings", {"persistent": settings})
    if not r.get("acknowledged"):
        raise RuntimeError(f"Could not configure ML Commons for Ollama: {r}")


def _find(clients, kind, name):
    r = clients.call("POST", f"/_plugins/_ml/{kind}/_search", {
        "size": 10, "query": {"term": {"name.keyword": name}}})
    hits = [h for h in r.get("hits", {}).get("hits", []) if "chunk_number" not in h["_source"]]
    return hits[0] if hits else None


def ensure_embedding_model(clients):
    """Returns the model_id, creating the connector and model on first use. Safe to call repeatedly."""
    configure_cluster_for_ollama(clients)
    model = _find(clients, "models", config.ML_MODEL_NAME)
    if model is None:
        connector = _find(clients, "connectors", config.ML_CONNECTOR_NAME)
        if connector is None:
            r = clients.call("POST", "/_plugins/_ml/connectors/_create", {
                "name": config.ML_CONNECTOR_NAME,
                "description": "Embeddings for the log-intelligence demo",
                "version": 1,
                "protocol": "http",
                "parameters": {"endpoint": config.OLLAMA_FROM_OPENSEARCH, "model": config.EMBED_MODEL},
                # Ollama ignores the key, but ML Commons requires a credential on every connector.
                "credential": {"api_key": "unused-by-ollama"},
                "actions": [{
                    "action_type": "predict",
                    "method": "POST",
                    # Ollama's OpenAI-compatible endpoint lets us reuse the built-in OpenAI pre/post processors.
                    "url": "http://${parameters.endpoint}/v1/embeddings",
                    "headers": {"Content-Type": "application/json"},
                    "request_body": '{ "model": "${parameters.model}", "input": ${parameters.input} }',
                    "pre_process_function": "connector.pre_process.openai.embedding",
                    "post_process_function": "connector.post_process.openai.embedding",
                }],
            })
            connector_id = r["connector_id"]
        else:
            connector_id = connector["_id"]
        r = clients.call("POST", "/_plugins/_ml/models/_register?deploy=true", {
            "name": config.ML_MODEL_NAME, "function_name": "remote", "connector_id": connector_id})
        model_id = r["model_id"]
    else:
        model_id = model["_id"]

    for _ in range(30):
        state = clients.call("GET", f"/_plugins/_ml/models/{model_id}").get("model_state")
        if state == "DEPLOYED":
            return model_id
        if state in (None, "REGISTERED", "UNDEPLOYED", "DEPLOY_FAILED", "PARTIALLY_DEPLOYED"):
            clients.call("POST", f"/_plugins/_ml/models/{model_id}/_deploy")
        time.sleep(1)
    raise RuntimeError(f"Embedding model {model_id} did not reach DEPLOYED")


def embed(clients, model_id, texts):
    r = clients.call("POST", f"/_plugins/_ml/_predict/text_embedding/{model_id}", {"text_docs": texts}, timeout=300)
    if "inference_results" not in r:
        raise RuntimeError(f"Embedding failed: {r}")
    vectors = [o["data"] for res in r["inference_results"] for o in res["output"]]
    if len(vectors) != len(texts):
        raise RuntimeError(f"Expected {len(texts)} embeddings, got {len(vectors)}")
    return vectors


def embed_query(clients, model_id, text):
    return embed(clients, model_id, [config.QUERY_PREFIX + text])[0]
