import os
from pathlib import Path

GRPC_ADDR = os.environ.get("OS_GRPC", "localhost:9400")
REST_URL = os.environ.get("OS_REST", "https://localhost:9200")
USER = os.environ.get("OS_USER", "admin")
PASSWORD = os.environ.get("OS_PASSWORD")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# How the OpenSearch container reaches Ollama on the host (Docker Desktop).
OLLAMA_FROM_OPENSEARCH = os.environ.get("OLLAMA_FROM_OPENSEARCH", "host.docker.internal:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "768"))
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.2:1b")

# nomic-embed-text expects these task prefixes for best retrieval quality.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

ML_CONNECTOR_NAME = "log-intel: ollama nomic-embed-text"
ML_MODEL_NAME = "log-intel: nomic-embed-text"

INDEX_REST = "logs-via-rest"
INDEX_GRPC = "logs-via-grpc"
# Both protocols search the same index so results can be compared one-to-one.
SEARCH_INDEX = INDEX_GRPC

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
