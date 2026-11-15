# Can Search Speak gRPC? OpenSearch REST vs gRPC demo

A live demo comparing OpenSearch's **REST (JSON over HTTP/1.1)** and **gRPC (Protocol Buffers over HTTP/2)**
APIs on OpenSearch 3.7. It covers bulk ingest, k-NN vector search, a concurrent load test, and an AI assistant that
searches 10,000 application logs over gRPC and streams answers from a local LLM.

| Path | What it is |
|---|---|
| `app.py`, `log_intel/` | The Streamlit demo app (UI + backend) |
| `prepare_data.py` | Generates and embeds the 10,000 demo logs |
| `Dockerfile`, `deploy/gcp/` | Everything needed to run the whole demo on one Google Cloud VM |
| `bulk_basics.py`, `knn_basics.py`, `bench_rest_vs_grpc.py` | Small standalone scripts (see "Developer notes") |

---

## Deploy on Google Cloud (start here)

Follow these steps in order. You only need a web browser and a Google Cloud project with billing enabled.
Everything runs on **one VM**: OpenSearch 3.7 (REST + gRPC), Ollama (embeddings + LLM), the demo app, and
Caddy, which gives the app an **HTTPS address with a username and password**. OpenSearch itself is **not**
reachable from the internet.

**Cost:** an `e2-standard-4` VM is billed per second only while it's running. Stop it when you're not using it
(see "Stop, start, delete"). Set a budget alert first: Console → **Billing → Budgets & alerts → Create budget**.

### Step 1: Create the VM (Google Cloud Console)

1. Open https://console.cloud.google.com and pick your project in the top bar.
2. Go to **☰ → Compute Engine → VM instances**. If asked, click **Enable** for the Compute Engine API.
3. Click **Create instance** and set:

   | Setting | Value |
   |---|---|
   | Name | `opensearch-grpc-demo` |
   | Region / Zone | `asia-south1 (Mumbai)` / `asia-south1-a` (any region works) |
   | Machine configuration | **E2 → e2-standard-4** (4 vCPU, 16 GB memory) |
   | OS and storage → Change | **Ubuntu 24.04 LTS (x86/64)**, **Balanced persistent disk**, **50 GB** |
   | Networking → Firewall | ✅ **Allow HTTP traffic** and ✅ **Allow HTTPS traffic** |

4. Click **Create** and wait for the green check. Smaller machines (4 GB memory) are **not** enough.

### Step 2: Install Docker on the VM

In the VM list, click **SSH** next to the VM. A terminal opens in your browser. Paste:

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf && sudo sysctl --system
```

**Close the SSH window and click SSH again** so the Docker permission takes effect.

### Step 3: Get the code

```bash
git clone https://github.com/Jeevi1990/grpc_opensearch.git
cd grpc_opensearch/deploy/gcp
```

### Step 4: Create the settings file (`.env`)

Paste this block. It asks you to choose a password for the demo website, then writes `.env` with:
a random OpenSearch admin password, the website address, and the hashed website password.

```bash
IP=$(curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)
read -rsp "Choose a password for the demo website: " DEMO_PW; echo
HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$DEMO_PW")
cat > .env <<EOF
OPENSEARCH_ADMIN_PASSWORD=Os-$(openssl rand -hex 10)-Zz9
SITE_ADDRESS=${IP//./-}.sslip.io
DEMO_USER=demo
DEMO_PASSWORD_HASH='$HASH'
EOF
chmod 600 .env
grep SITE_ADDRESS .env
```

The last line prints your website address, for example `SITE_ADDRESS=34-93-12-7.sslip.io`.
`sslip.io` is a free DNS name that points to your VM's IP, so Caddy can get a real HTTPS certificate.

Keep `.env` on the VM only. Never commit it or paste it into chat. To see the OpenSearch admin password later:
`grep OPENSEARCH_ADMIN_PASSWORD .env`.

### Step 5: Start everything

```bash
docker compose up -d --build
```

The first start takes a few minutes: it builds the app and downloads OpenSearch, Ollama, and the two Ollama models
(about 1.6 GB). Check progress:

```bash
docker compose ps -a                         # opensearch should become "healthy"; ollama-pull ends with "Exited (0)"
docker compose logs ollama-pull | tail -3    # model download progress
docker logs opensearch 2>&1 | grep -i "grpc" | tail -2   # should show "Started gRPC server ... 9400"
```

### Step 6: Open the demo

1. Open `https://<SITE_ADDRESS>` from Step 4 in a browser (for example `https://34-93-12-7.sslip.io`).
   The first visit can take up to a minute while the HTTPS certificate is issued.
2. Log in with user **`demo`** and the website password you chose in Step 4.
3. The sidebar should show ✅ for REST, gRPC, Embeddings, and LLM.

### Step 7: Load the demo data (once)

1. In the sidebar, click **Generate + embed logs**. This creates 10,000 logs and their vectors. On a CPU-only VM
   it can take several minutes. Alternatively, in the SSH window: `docker compose exec app python prepare_data.py`.
2. Go to tab **② Bulk ingest**, scroll to **B · Where it's a tie**, keep **10,000** logs, and click **Start the race**.
   This loads the logs into the two search indexes. The sidebar should then show **10,000 docs** for both.
3. Click **Warm up LLM** in the sidebar.

The demo is ready. The data stays on the VM disk, so you only do this once.

### Running the demo

Suggested order for the talk:
1. **★ Why gRPC**: click **Measure the scoreboard**. Six live REST vs gRPC measurements (about 30 s).
2. **① Two languages**: the same k-NN request as JSON and as protobuf.
3. **④ Under load**: concurrent searches, where gRPC's HTTP/2 multiplexing pays off.
4. **② Bulk ingest**: log shippers (gRPC wins), then the large-batch load (a tie, and why).
5. **⑤ Ask the logs**: pick an example question; logs are retrieved over gRPC and the answer streams in.
6. **⑥ Trade-offs**: summary of everything measured in this session.

Run the scoreboard once before the audience arrives to warm everything up, and quote ratios (for example "~2×")
rather than absolute numbers, which vary with machine load.

### Stop, start, delete (saving money)

- **Stop** after each session: Console → VM instances → ⋮ → **Stop**. You then pay only for the disk.
- **Start** again: ⋮ → **Start / Resume**. All containers start automatically. The VM may get a **new IP address**,
  which changes the website address. In SSH, update it with:

  ```bash
  cd ~/grpc_opensearch/deploy/gcp
  IP=$(curl -s -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)
  sed -i "s/^SITE_ADDRESS=.*/SITE_ADDRESS=${IP//./-}.sslip.io/" .env && docker compose up -d && grep SITE_ADDRESS .env
  ```

  To keep the same address, reserve a static IP: **VPC network → IP addresses → Reserve external static IP**
  and attach it to the VM. A reserved IP has a small cost.
- **Delete** when you're done with the demo: ⋮ → **Delete**. This also deletes the disk and all data.

### Updating to the latest code

```bash
cd ~/grpc_opensearch && git pull && cd deploy/gcp && docker compose up -d --build
```

### Troubleshooting

| Problem | What to check |
|---|---|
| Website doesn't load | Step 1 firewall boxes (HTTP + HTTPS) are ticked; `docker compose logs caddy` |
| HTTPS certificate error | Wait a minute and reload. `docker compose logs caddy` shows certificate errors. If sslip.io is rate-limited, point your own domain at the VM IP and put it in `SITE_ADDRESS` |
| `opensearch` not healthy | `docker logs opensearch`. Check `sysctl vm.max_map_count` is `262144` (Step 2) and the VM has 16 GB memory |
| Sidebar: Embeddings ❌ | `docker compose ps -a`: `ollama-pull` must have `Exited (0)`; then `docker compose restart app` |
| Sidebar: LLM ❌ | Same as above; the `llama3.2:1b` download may still be running |
| Search tabs say "run the ingest race first" | Do Step 7.2 |

---

## Run locally (laptop)

Requirements: Docker, Python 3.12+, [Ollama](https://ollama.com), and an OpenSearch 3.7 cluster with gRPC enabled
(see "Enable gRPC on the server" below).

```bash
ollama pull nomic-embed-text && ollama pull llama3.2:1b && ollama serve
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export OS_PASSWORD='<admin password>'
.venv/bin/python prepare_data.py          # creates the ML model, embeds the logs (cached in data/)
.venv/bin/streamlit run app.py --server.address localhost
```

If OpenSearch runs in Docker and Ollama on the host, OpenSearch reaches Ollama at `host.docker.internal:11434`.
That endpoint must be allowed in ML Commons (`plugins.ml_commons.trusted_connector_private_endpoints_regex` and
`plugins.ml_commons.connector.private_ip_enabled: true`). Setting `OS_CONFIGURE_ML=true` makes the app add these
settings for you.

---

# Developer notes

## 1. Enable gRPC on the server

In the OpenSearch service's `environment` in `docker-compose.yml` (environment variables, **no brackets**):

```yaml
- aux.transport.types=transport-grpc
- aux.transport.transport-grpc.port=9400-9500
- grpc.host=0.0.0.0
- grpc.publish_port=9400
```

and publish the port: `- "9400:9400"`. Then `docker compose up -d opensearch-node1`.
The log should show `Started gRPC server on port ... 9400-9500`.

## 2. Run the sample

```bash
python3 -m venv .venv
.venv/bin/pip install "opensearch-protobufs==1.4.0" grpcio   # 1.4.0 = the protos bundled in OpenSearch 3.7
export OS_PASSWORD='<admin password>'
.venv/bin/python bulk_basics.py
```

| Step | Operation | What you should see |
|---|---|---|
| 1 | `index` | `created`, version 1 |
| 2 | `create` | `created`, version 1 |
| 3 | `create` same id | `ABORTED` + `version_conflict_engine_exception` |
| 4 | `update` partial doc | `updated`, year 2010 → 2011 |
| 5 | `update` + `doc_as_upsert` | `created` for a new id |
| 6 | `update` + Painless script | year 2011 → 2012 |
| 7 | `delete` | `deleted`, REST shows not found |
| 8 | mixed batch | index + update + delete in one request |

## 3. k-NN (vector) search over gRPC

```bash
.venv/bin/python knn_basics.py
```

`knn_basics.py` creates a `movies-knn` index over REST (gRPC has no create-index API), loads 3-D vectors with
gRPC `Bulk`, and then searches with gRPC `SearchService.Search` using `QueryContainer(knn=KnnQuery(...))`.
It runs a plain search and a search with a `genre` filter. Requires the `opensearch-knn` plugin.

## 4. Benchmark: REST vs gRPC for vectors

```bash
.venv/bin/python bench_rest_vs_grpc.py --docs 5000 --queries 1000   # defaults: 2000 docs, 768-dim, 200 queries
```

Runs the same ingest and k-NN searches through both protocols (keep-alive HTTPS for REST, alternating order,
warm-up, same index for search) and checks that both return the same top-k IDs. Typical results on a local
single-node cluster (5000 × 768-dim docs, 1000 queries, k=10):

| | REST | gRPC | |
|---|---|---|---|
| k-NN request size | 8,082 B | 3,131 B | gRPC **2.6x smaller** (packed floats vs JSON text) |
| k-NN response size | 1,310 B | 810 B | gRPC **1.6x smaller** |
| Bulk response size / 200 docs | 34.0 KB | 8.9 KB | gRPC **3.8x smaller** |
| k-NN latency p50 / p95 | ~1.4 / ~2.1 ms | ~1.3 / ~1.9 ms | gRPC ~7–13% faster |
| Bulk ingest throughput | ~3,200 docs/s | ~3,200 docs/s | about the same |
| Bulk request size | 1,575 KB | 1,573 KB | about the same (documents are JSON bytes in both) |

Caveats: REST uses TLS here while the gRPC port is plaintext; it's one local node, so network savings matter
much less than they would across a real network. `BulkRequestBody.extra_field_values` (binary vectors) is
accepted by 3.7 but the vectors are not indexed, so it isn't used.

## 5. Talk demo: "Can Search Speak gRPC?" (Streamlit app)

An end-to-end log-intelligence demo: 10,000 generated microservice logs, embedded with `nomic-embed-text`
through an OpenSearch ML Commons connector to Ollama, loaded and searched through both REST and gRPC, plus an AI
assistant that retrieves logs over gRPC and streams an answer from `llama3.2:1b`.

```bash
ollama serve                                  # needs nomic-embed-text and llama3.2:1b pulled
.venv/bin/pip install -r requirements.txt
export OS_PASSWORD='<admin password>'
.venv/bin/python prepare_data.py              # creates the ML model, embeds the logs (~1 min, cached in data/)
.venv/bin/streamlit run app.py --server.address localhost
```

Tabs follow the talk: ★ a live "Why gRPC" scoreboard (one click measures the six gRPC wins below), ① the same
k-NN request as JSON and as protobuf (incl. client encode cost), ② bulk ingest (log shippers first, then the
large-batch tie), ③ semantic search side by side (latency distribution, sizes, identical-results check),
④ concurrent load test, ⑤ "Ask the logs" with streaming, ⑥ a trade-off scorecard built from the session's
measurements.

Where gRPC wins, and where it doesn't (local single node, fast clients on both sides: urllib3 pool vs one gRPC channel):

| | REST | gRPC | |
|---|---|---|---|
| k-NN search @ 64 concurrent | ~2,400 q/s, p99 ~65 ms | ~4,900 q/s, p99 ~23 ms | gRPC ~2x throughput, ~2.8x lower p99 |
| Client CPU per search | ~0.5 ms | ~0.2 ms | gRPC ~2.4x less |
| Encode one 768-dim k-NN request | ~130 µs | ~12 µs | gRPC ~11x less |
| Single sequential search | ~2 ms | ~1.5–2 ms | small; noise can flip individual runs |
| Bulk ingest, large batches (with or without vectors, 1–8 streams) | | | tie (±10%): docs are JSON bytes in both |
| Bulk ingest, log shippers (10 plain docs/request, 16 senders) | e.g. 38,410 docs/s | e.g. 58,805 docs/s | gRPC ~1.3–1.5x (absolute numbers vary with machine load) |

REST runs over TLS here while the gRPC port is plaintext; mention it when presenting the numbers.

Notes from building it:
- The k-NN indexes use exact search (`index.knn.advanced.approximate_threshold`) because repeated log messages
  hurt HNSW recall (measured recall@10: 0.85 HNSW vs 1.00 exact, ~0.5 ms slower).
- Searches can group results by log pattern (`collapse` + `inner_hits`), which works the same over gRPC.
- OpenSearch 3.7 has no gRPC ML service, so the assistant streams from Ollama directly instead of through an
  OpenSearch agent.

## Things learned

- **Docs are bytes.** `object` / `doc` are raw bytes; the docs show Base64 only because they print requests as JSON.
- **`_id` → `x_id`.** Leading-underscore REST fields get an `x_` prefix in protobuf (requests *and* responses).
- **Auth goes in metadata.** With the security plugin on, send `authorization: Basic …` (or `Bearer <jwt>`) as gRPC metadata,
  otherwise every call fails with `UNAUTHENTICATED`. Calls made right after a restart can also fail until the security config has loaded.
- **Per-item status is a gRPC code** in 3.7 (`0` OK, `5` NOT_FOUND, `10` ABORTED), not an HTTP code.
- **Partial failures don't fail the RPC.** The call returns OK with `errors=true`; check each item.
- **Match proto versions.** Use the `opensearch-protobufs` version bundled in the server
  (`modules/transport-grpc/protobufs-*.jar`).
