import base64
import json
from urllib.parse import urlparse

import grpc
import requests
import urllib3
from opensearch.protobufs.services import document_service_pb2_grpc, search_service_pb2_grpc

from . import config

# The local demo cluster uses self-signed certificates.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_MAX_MSG = 256 * 1024 * 1024
_POOL_SIZE = 128


class OpenSearchClients:
    """Fast, thread-safe clients for both protocols so the comparison is fair:
    REST: a urllib3 keep-alive connection pool (HTTP/1.1, one connection per in-flight request).
    gRPC: one channel (HTTP/2, many in-flight requests multiplexed on one connection).
    A `requests` session is kept for setup/admin calls that aren't measured."""

    def __init__(self):
        if not config.PASSWORD:
            raise RuntimeError("Set OS_PASSWORD before starting the demo")
        token = base64.b64encode(f"{config.USER}:{config.PASSWORD}".encode()).decode()
        self.auth_header = f"Basic {token}"

        self.rest = requests.Session()
        self.rest.verify = False
        self.rest.headers.update({"Authorization": self.auth_header, "Content-Type": "application/json"})
        self.rest_url = config.REST_URL.rstrip("/")

        url = urlparse(self.rest_url)
        pool_cls = urllib3.HTTPSConnectionPool if url.scheme == "https" else urllib3.HTTPConnectionPool
        tls = {"cert_reqs": "CERT_NONE", "assert_hostname": False} if url.scheme == "https" else {}
        self.rest_pool = pool_cls(url.hostname, url.port, maxsize=_POOL_SIZE, block=True, **tls)

        self.channel = grpc.insecure_channel(config.GRPC_ADDR, options=[
            ("grpc.max_send_message_length", _MAX_MSG),
            ("grpc.max_receive_message_length", _MAX_MSG),
        ])
        self.docs = document_service_pb2_grpc.DocumentServiceStub(self.channel)
        self.search = search_service_pb2_grpc.SearchServiceStub(self.channel)
        self.grpc_auth = [("authorization", self.auth_header)]

    def post(self, path, body, content_type="application/json", timeout=120):
        """Measured REST call: returns (raw response bytes, parsed JSON)."""
        r = self.rest_pool.request("POST", path, body=body, timeout=timeout, headers={
            "Authorization": self.auth_header, "Content-Type": content_type})
        return r.data, json.loads(r.data)

    def call(self, method, path, body=None, timeout=60):
        """REST call returning parsed JSON (errors included, so callers can show them)."""
        r = self.rest.request(method, self.rest_url + path, json=body, timeout=timeout)
        return r.json() if r.content else {}

    def grpc_ready(self, timeout=3):
        try:
            grpc.channel_ready_future(self.channel).result(timeout=timeout)
            return True
        except grpc.FutureTimeoutError:
            return False
