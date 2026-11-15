"""Generates realistic microservice logs with a few planted incidents, so search and the AI assistant have something to find."""

import random
import re
import uuid
from datetime import datetime, timedelta, timezone

# Variable parts of a log line; replacing them turns a message into its pattern ("template").
_VARIABLE_PARTS = [
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b[a-z]{2,3}_[0-9a-f]{6,}\b"), "<id>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
]


def message_pattern(message):
    for regex, token in _VARIABLE_PARTS:
        message = regex.sub(token, message)
    return message


SERVICES = ["api-gateway", "auth-service", "payment-service", "order-service",
            "inventory-service", "notification-service", "search-service", "user-service"]
REGIONS = ["us-east-1", "eu-west-1", "ap-south-1"]
PATHS = ["/api/v1/products", "/api/v1/cart", "/api/v1/orders", "/api/v1/users/me", "/api/v1/search",
         "/api/v1/checkout", "/api/v1/payments", "/health"]
QUERIES = ["red running shoes", "wireless earbuds", "laptop stand", "coffee beans", "yoga mat",
           "phone case", "desk lamp", "water bottle"]
EMAIL_TEMPLATES = ["order_confirmation", "password_reset", "shipping_update", "welcome", "invoice"]


def _uid(r):
    return f"u{r.randint(10000, 99999)}"


def _routine(r, service):
    """(level, message, status_code, latency_ms) for normal day-to-day traffic."""
    lat = int(r.lognormvariate(3.3, 0.6))
    if service == "api-gateway":
        method, path = r.choice(["GET", "GET", "GET", "POST", "PUT"]), r.choice(PATHS)
        status = r.choices([200, 201, 204, 304, 400, 404], [70, 8, 4, 8, 5, 5])[0]
        return "INFO", f"{method} {path} {status} in {lat}ms", status, lat
    if service == "auth-service":
        return r.choice([
            ("INFO", f"User {_uid(r)} logged in successfully via SSO", 200, lat),
            ("INFO", f"Access token refreshed for user {_uid(r)}", 200, lat),
            ("INFO", f"User {_uid(r)} logged out", 204, lat),
            ("DEBUG", f"JWKS cache hit for issuer https://sso.example.com", 200, 1),
        ])
    if service == "payment-service":
        amount = r.uniform(5, 900)
        return r.choice([
            ("INFO", f"Payment pi_{uuid.UUID(int=r.getrandbits(128)).hex[:14]} authorized for "
                     f"${amount:.2f} (card ending {r.randint(1000, 9999)})", 200, lat * 4),
            ("INFO", f"Refund re_{r.getrandbits(40):x} processed for ${amount / 3:.2f}", 200, lat * 3),
            ("INFO", "Stripe webhook payment_intent.succeeded verified", 200, lat),
        ])
    if service == "order-service":
        return r.choice([
            ("INFO", f"Order ord-{r.randint(100000, 999999)} created with {r.randint(1, 6)} items, "
                     f"total ${r.uniform(10, 600):.2f}", 201, lat),
            ("INFO", f"Order ord-{r.randint(100000, 999999)} shipped via {r.choice(['UPS', 'FedEx', 'DHL'])}",
             200, lat),
            ("DEBUG", f"Order cache miss for ord-{r.randint(100000, 999999)}, loaded from postgres", 200, lat),
        ])
    if service == "inventory-service":
        return r.choice([
            ("INFO", f"Reserved {r.randint(1, 5)} units of SKU-{r.randint(1000, 9999)} for checkout", 200, lat),
            ("INFO", f"Stock level synced for warehouse WH-{r.randint(1, 9)}: {r.randint(500, 5000)} SKUs updated",
             200, lat * 20),
            ("DEBUG", f"Inventory cache hit for SKU-{r.randint(1000, 9999)}", 200, 1),
        ])
    if service == "notification-service":
        return r.choice([
            ("INFO", f"Email sent to {_uid(r)}@example.com template={r.choice(EMAIL_TEMPLATES)}", 200, lat * 2),
            ("INFO", f"Push notification delivered to device of user {_uid(r)}", 200, lat),
            ("INFO", f"SMS queued for user {_uid(r)}", 202, lat),
        ])
    if service == "search-service":
        q = r.choice(QUERIES)
        return r.choice([
            ("INFO", f"Query '{q}' returned {r.randint(0, 400)} results in {lat}ms", 200, lat),
            ("INFO", f"Autocomplete for '{q[:4]}' served from cache", 200, 2),
        ])
    return r.choice([
        ("INFO", f"Profile updated for user {_uid(r)}", 200, lat),
        ("INFO", f"Address added for user {_uid(r)}", 201, lat),
        ("INFO", f"Preferences saved for user {_uid(r)}", 200, lat),
    ])


def _background_noise(r):
    """Occasional, unrelated warnings/errors that happen on any normal day."""
    return r.choice([
        ("order-service", "WARN", f"Slow query detected: SELECT * FROM orders WHERE user_id=$1 took {r.randint(800, 2500)}ms", 200, r.randint(800, 2500)),
        ("api-gateway", "WARN", f"Upstream retry 1/3 for {r.choice(SERVICES[1:])} after 502", 502, r.randint(100, 900)),
        ("search-service", "WARN", "Query parsing fell back to simple_query_string for malformed input", 200, 15),
        ("user-service", "ERROR", "Validation failed: phone number has invalid format", 400, 12),
    ])


# Each incident: (name, hours before 'now' it starts, duration in minutes, weight, generator)
def _payment_timeouts(r):
    return r.choice([
        ("payment-service", "ERROR", "Timeout calling Stripe API POST /v1/payment_intents after 30000ms", 504, 30000),
        ("payment-service", "ERROR", "Payment authorization failed: upstream provider timeout (stripe)", 504, 30000),
        ("payment-service", "WARN", "Retrying Stripe request (attempt 2/3) after read timeout", 0, 15000),
        ("payment-service", "ERROR", "Circuit breaker OPEN for stripe-client: 52% failures in last 60s", 503, 2),
        ("api-gateway", "ERROR", "POST /api/v1/checkout 504 in 30012ms (upstream payment-service)", 504, 30012),
    ])


def _db_pool(r):
    return r.choice([
        ("order-service", "ERROR", "HikariPool-1 - Connection is not available, request timed out after 30000ms", 500, 30000),
        ("order-service", "ERROR", "Failed to create order: could not acquire JDBC connection from pool (active=50, max=50)", 500, 30000),
        ("order-service", "WARN", "HikariPool-1 - Thread starvation or clock leap detected (housekeeper delta=45s)", 0, 0),
        ("order-service", "ERROR", "PSQLException: FATAL: sorry, too many clients already", 500, 12),
        ("api-gateway", "ERROR", "POST /api/v1/orders 500 in 30044ms (upstream order-service)", 500, 30044),
    ])


def _oom(r):
    return r.choice([
        ("inventory-service", "ERROR", "java.lang.OutOfMemoryError: Java heap space at InventorySnapshot.load", 500, 0),
        ("inventory-service", "ERROR", "Container inventory-service killed: OOMKilled (exit code 137), restarting", 0, 0),
        ("inventory-service", "WARN", "GC overhead limit approaching: 96% time spent in GC over last 5 minutes", 0, 0),
        ("inventory-service", "WARN", "Pod inventory-service restarted 4 times in 10 minutes (CrashLoopBackOff)", 0, 0),
    ])


def _brute_force(r):
    ip = f"185.220.101.{r.randint(2, 60)}"
    user = r.choice(["admin", "root", "support", "test"])
    return r.choice([
        ("auth-service", "WARN", f"Failed login for user '{user}' from {ip}: invalid password", 401, 40),
        ("auth-service", "WARN", f"Failed login for user '{user}' from {ip}: invalid password", 401, 40),
        ("auth-service", "ERROR", f"Account '{user}' locked after 5 failed login attempts from {ip}", 423, 5),
        ("api-gateway", "WARN", f"Rate limit exceeded for IP {ip} on POST /api/v1/login (429)", 429, 1),
        ("auth-service", "ERROR", f"Possible credential stuffing attack: 120 failed logins from {ip} in 2 minutes", 401, 0),
    ])


def _disk(r):
    pct = r.randint(88, 97)
    return r.choice([
        ("search-service", "WARN", f"Disk usage at {pct}% on /var/lib/search-data (threshold 85%)", 0, 0),
        ("search-service", "ERROR", "Index write blocked: flood-stage disk watermark exceeded on node search-2", 503, 0),
        ("search-service", "WARN", "Segment merge deferred: not enough free disk space", 0, 0),
    ])


def _kafka_lag(r):
    return r.choice([
        ("notification-service", "WARN", f"Consumer lag for topic order-events partition {r.randint(0, 5)} is "
                                         f"{r.randint(8000, 40000)} messages", 0, 0),
        ("notification-service", "WARN", "Kafka consumer group notif-workers rebalancing (member left)", 0, 0),
        ("notification-service", "ERROR", "Shipping update emails delayed: queue backlog above 30000", 0, 0),
    ])


def _cert(r):
    return ("notification-service", "WARN", "TLS certificate for smtp.mail.internal expires in 6 days "
                                            "(CN=smtp.mail.internal)", 0, 0)


INCIDENTS = [
    # name, starts N hours ago, duration minutes, approx. number of logs, generator
    ("payment gateway timeouts", 6.0, 40, 450, _payment_timeouts),
    ("order DB connection pool exhausted", 3.0, 25, 350, _db_pool),
    ("inventory OOM / crash loop", 9.0, 20, 200, _oom),
    ("auth brute-force attack", 1.5, 15, 350, _brute_force),
    ("search disk almost full", 12.0, 120, 150, _disk),
    ("kafka consumer lag", 4.5, 50, 150, _kafka_lag),
    ("TLS certificate expiring", 24.0, 24 * 60, 24, _cert),
]


def generate_logs(n=10_000, seed=7, now=None):
    r = random.Random(seed)
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(hours=24)
    logs = []

    def add(ts, service, level, message, status, latency):
        logs.append({
            "@timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "service": service,
            "level": level,
            "host": f"{service}-{r.choice(['7f9c', 'a12d', 'c4e8'])}-{r.randint(1, 3)}",
            "region": r.choices(REGIONS, [6, 3, 1])[0],
            "message": message,
            "pattern": message_pattern(message),
            "trace_id": uuid.UUID(int=r.getrandbits(128)).hex,
            "status_code": status,
            "latency_ms": latency,
        })

    for _, hours_ago, minutes, count, gen in INCIDENTS:
        t0 = now - timedelta(hours=hours_ago)
        for _ in range(count):
            service, level, msg, status, lat = gen(r)
            add(t0 + timedelta(seconds=r.uniform(0, minutes * 60)), service, level, msg, status, lat)

    n_noise = int(n * 0.03)
    for _ in range(n_noise):
        service, level, msg, status, lat = _background_noise(r)
        add(start + timedelta(seconds=r.uniform(0, 86400)), service, level, msg, status, lat)

    while len(logs) < n:
        service = r.choices(SERVICES, [30, 12, 10, 12, 10, 8, 10, 8])[0]
        level, msg, status, lat = _routine(r, service)
        add(start + timedelta(seconds=r.uniform(0, 86400)), service, level, msg, status, lat)

    logs = logs[:n]
    logs.sort(key=lambda d: d["@timestamp"])
    for i, d in enumerate(logs):
        d["log_id"] = f"log-{i:06d}"
    return logs


def embedding_text(log):
    return f"{log['level']} {log['service']}: {log['message']}"
