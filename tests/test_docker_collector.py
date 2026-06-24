"""Tests for the Docker container monitoring collector."""

import json
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from llamawatch.collectors import docker_collector
from llamawatch.collectors.docker_collector import (
    UnixHTTPConnection,
    _docker_get,
    _docker_post,
    _fetch_remote_containers,
    collect,
    docker_available,
)


@pytest.fixture(autouse=True)
def reset_remote_cache():
    """Flush the remote-container cache between tests to avoid leakage."""
    docker_collector._remote_cache = {"ts": 0.0, "data": None}
    yield
    docker_collector._remote_cache = {"ts": 0.0, "data": None}


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_module_constants():
    assert docker_collector.WIDGET_ID == "docker"
    assert docker_collector.WIDGET_NAME == "Docker"
    assert docker_collector.WIDGET_MULTI_INSTANCE is False
    assert docker_collector.WIDGET_CONFIG_REQUIRED is False
    assert isinstance(docker_collector.WIDGET_DEFAULT_SIZE, dict)
    assert docker_collector.WIDGET_DEFAULT_SIZE["w"] == 4
    assert docker_collector.WIDGET_REQUIRES == []
    assert docker_collector.WIDGET_CONFIG_SCHEMA == []


# ---------------------------------------------------------------------------
# docker_available
# ---------------------------------------------------------------------------


class TestDockerAvailable:
    def test_returns_false_when_socket_absent(self, tmp_path):
        """Returns False when the Docker socket does not exist."""
        with patch.object(docker_collector, "_DOCKER_SOCKET", str(tmp_path / "no.sock")):
            assert docker_available() is False

    def test_returns_true_when_socket_exists_and_readable(self, tmp_path):
        """Returns True when socket path exists and is readable."""
        sock = tmp_path / "docker.sock"
        sock.touch()
        with patch.object(docker_collector, "_DOCKER_SOCKET", str(sock)):
            assert docker_available() is True

    def test_returns_false_when_not_readable(self, tmp_path):
        """Returns False when socket exists but has no read permission."""
        sock = tmp_path / "docker.sock"
        sock.touch()
        sock.chmod(0o000)
        try:
            with patch.object(docker_collector, "_DOCKER_SOCKET", str(sock)):
                # If running as root, os.access always returns True — skip
                if os.getuid() == 0:
                    pytest.skip("root bypasses permission checks")
                assert docker_available() is False
        finally:
            sock.chmod(0o644)


# ---------------------------------------------------------------------------
# UnixHTTPConnection
# ---------------------------------------------------------------------------


class TestUnixHTTPConnection:
    def test_stores_socket_path(self):
        conn = UnixHTTPConnection("/tmp/test.sock")
        assert conn.socket_path == "/tmp/test.sock"

    def test_connect_calls_unix_socket(self):
        """connect() creates AF_UNIX socket and connects to socket_path."""
        mock_sock = MagicMock()
        with patch("socket.socket", return_value=mock_sock) as mock_socket_cls:
            conn = UnixHTTPConnection("/var/run/docker.sock", timeout=3)
            conn.connect()

        import socket as socket_mod
        mock_socket_cls.assert_called_once_with(
            socket_mod.AF_UNIX, socket_mod.SOCK_STREAM
        )
        mock_sock.settimeout.assert_called_once_with(3)
        mock_sock.connect.assert_called_once_with("/var/run/docker.sock")


# ---------------------------------------------------------------------------
# _docker_get
# ---------------------------------------------------------------------------


def _make_fake_response(body: bytes, status: int = 200):
    """Return a fake http.client.HTTPResponse-like object."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    return resp


class TestDockerGet:
    def test_returns_parsed_json(self):
        """_docker_get() parses the JSON response body."""
        payload = [{"Id": "abc123def456ghij", "Names": ["/mycontainer"]}]
        body = json.dumps(payload).encode()

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = _make_fake_response(body)

        with patch.object(docker_collector, "UnixHTTPConnection", return_value=mock_conn):
            result = _docker_get("/v1.41/containers/json?all=true")

        assert result == payload
        mock_conn.request.assert_called_once_with("GET", "/v1.41/containers/json?all=true")
        mock_conn.close.assert_called_once()

    def test_raises_on_non_200(self):
        """_docker_get() raises RuntimeError when the API returns a non-200 status."""
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = _make_fake_response(b"Not found", status=404)

        with patch.object(docker_collector, "UnixHTTPConnection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="404"):
                _docker_get("/v1.41/containers/json")

    def test_closes_connection_on_error(self):
        """_docker_get() closes the connection even when an exception is raised."""
        mock_conn = MagicMock()
        mock_conn.request.side_effect = OSError("connection refused")

        with patch.object(docker_collector, "UnixHTTPConnection", return_value=mock_conn):
            with pytest.raises(OSError):
                _docker_get("/v1.41/containers/json")

        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# _docker_post
# ---------------------------------------------------------------------------


class TestDockerPost:
    def test_posts_to_path(self):
        """_docker_post() sends a POST request to the given path."""
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = _make_fake_response(b"{}", status=200)

        with patch.object(docker_collector, "UnixHTTPConnection", return_value=mock_conn):
            result = _docker_post("/v1.41/containers/abc123/start")

        assert result == {}
        mock_conn.request.assert_called_once()
        call_args = mock_conn.request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/v1.41/containers/abc123/start"

    def test_returns_empty_dict_on_204(self):
        """_docker_post() returns {} on 204 No Content responses."""
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = _make_fake_response(b"", status=204)

        with patch.object(docker_collector, "UnixHTTPConnection", return_value=mock_conn):
            result = _docker_post("/v1.41/containers/abc123/stop")

        assert result == {}

    def test_raises_on_error_status(self):
        """_docker_post() raises RuntimeError on 500."""
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = _make_fake_response(b"Server Error", status=500)

        with patch.object(docker_collector, "UnixHTTPConnection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="500"):
                _docker_post("/v1.41/containers/abc123/restart")


# ---------------------------------------------------------------------------
# collect — unavailable
# ---------------------------------------------------------------------------


class TestCollectUnavailable:
    def test_returns_unavailable_when_no_socket(self, tmp_path):
        """collect() returns available=False when Docker is not present and no remotes."""
        with patch.object(docker_collector, "_DOCKER_SOCKET", str(tmp_path / "no.sock")), \
             patch.object(docker_collector, "_collect_remote_all", return_value=[]):
            result = collect()

        assert result["available"] is False
        assert result["error"] == "Docker not available"
        assert result["containers"] == []

    def test_returns_permission_error_message(self, tmp_path):
        """collect() returns a helpful message when permission is denied."""
        with patch.object(docker_collector, "docker_available", return_value=True), \
             patch.object(docker_collector, "_docker_get", side_effect=PermissionError("denied")), \
             patch.object(docker_collector, "_collect_remote_all", return_value=[]):
            result = collect()

        assert result["available"] is False
        assert "docker group" in result["error"]
        assert result["containers"] == []

    def test_returns_error_on_exception(self):
        """collect() returns available=False and includes the error string on any other exception."""
        with patch.object(docker_collector, "docker_available", return_value=True), \
             patch.object(docker_collector, "_docker_get", side_effect=RuntimeError("connection reset")), \
             patch.object(docker_collector, "_collect_remote_all", return_value=[]):
            result = collect()

        assert result["available"] is False
        assert "connection reset" in result["error"]
        assert result["containers"] == []


# ---------------------------------------------------------------------------
# collect — happy path
# ---------------------------------------------------------------------------


_FAKE_CONTAINERS_JSON = [
    {
        "Id": "abc123def456ghij0000",
        "Names": ["/myapp"],
        "Image": "python:3.12",
        "State": "running",
        "Status": "Up 3 hours",
        "Created": 1711929600,  # 2024-04-01 00:00:00 UTC
    },
    {
        "Id": "deadbeef00001234abcd",
        "Names": ["/redis"],
        "Image": "redis:7-alpine",
        "State": "exited",
        "Status": "Exited (0) 2 hours ago",
        "Created": 1711926000,
    },
]


class TestCollectHappyPath:
    def _run_collect(self):
        # Local containers are now tagged via _local_name() (config-driven /
        # hostname fallback); pin it to "M5" so the machine assertions hold.
        with patch.object(docker_collector, "docker_available", return_value=True), \
             patch.object(docker_collector, "_docker_get", return_value=_FAKE_CONTAINERS_JSON), \
             patch.object(docker_collector, "_local_name", return_value="M5"), \
             patch.object(docker_collector, "_collect_remote_all", return_value=[]):
            return collect()

    def test_returns_available_true(self):
        result = self._run_collect()
        assert result["available"] is True
        assert result["error"] is None

    def test_returns_correct_container_count(self):
        result = self._run_collect()
        assert len(result["containers"]) == 2

    def test_id_is_12_chars(self):
        result = self._run_collect()
        for c in result["containers"]:
            assert len(c["id"]) == 12

    def test_name_strips_leading_slash(self):
        result = self._run_collect()
        names = {c["name"] for c in result["containers"]}
        assert "myapp" in names
        assert "redis" in names

    def test_state_and_status_present(self):
        result = self._run_collect()
        by_name = {c["name"]: c for c in result["containers"]}
        assert by_name["myapp"]["state"] == "running"
        assert by_name["redis"]["state"] == "exited"
        assert "Up 3 hours" in by_name["myapp"]["status"]

    def test_image_preserved(self):
        result = self._run_collect()
        by_name = {c["name"]: c for c in result["containers"]}
        assert by_name["myapp"]["image"] == "python:3.12"

    def test_created_is_iso_string(self):
        result = self._run_collect()
        for c in result["containers"]:
            assert "T" in c["created"], f"Expected ISO string, got: {c['created']!r}"

    def test_local_containers_tagged_m5(self):
        result = self._run_collect()
        for c in result["containers"]:
            assert c["machine"] == "M5", f"Expected M5 tag, got: {c['machine']!r}"

    def test_empty_container_list(self):
        with patch.object(docker_collector, "docker_available", return_value=True), \
             patch.object(docker_collector, "_docker_get", return_value=[]), \
             patch.object(docker_collector, "_collect_remote_all", return_value=[]):
            result = collect()

        assert result["available"] is True
        assert result["containers"] == []

    def test_collect_passes_all_keyword_args(self):
        """collect() accepts config, adapters, and widget_config without error."""
        with patch.object(docker_collector, "docker_available", return_value=True), \
             patch.object(docker_collector, "_docker_get", return_value=[]), \
             patch.object(docker_collector, "_collect_remote_all", return_value=[]):
            result = collect(config={}, adapters=None, widget_config={})
        assert result["available"] is True

    def test_merges_remote_containers(self):
        """collect() merges M5 local + remote TC1/TC2 containers."""
        tc1_containers = [
            {"id": "", "name": "tc1app", "image": "nginx", "state": "running",
             "status": "Up 1 hour", "created": "", "machine": "TC1"},
        ]
        with patch.object(docker_collector, "docker_available", return_value=True), \
             patch.object(docker_collector, "_docker_get", return_value=_FAKE_CONTAINERS_JSON), \
             patch.object(docker_collector, "_local_name", return_value="M5"), \
             patch.object(docker_collector, "_collect_remote_all", return_value=tc1_containers):
            result = collect()

        assert result["available"] is True
        assert len(result["containers"]) == 3
        machines = {c["machine"] for c in result["containers"]}
        assert "M5" in machines
        assert "TC1" in machines

    def test_remote_ssh_failure_does_not_fail_collect(self):
        """collect() returns M5 containers even when remote SSH fails."""
        with patch.object(docker_collector, "docker_available", return_value=True), \
             patch.object(docker_collector, "_docker_get", return_value=_FAKE_CONTAINERS_JSON), \
             patch.object(docker_collector, "_collect_remote_all", return_value=[]):
            result = collect()

        assert result["available"] is True
        assert len(result["containers"]) == 2


# ---------------------------------------------------------------------------
# _fetch_remote_containers
# ---------------------------------------------------------------------------

class TestFetchRemoteContainers:
    def test_parses_docker_ps_output(self):
        """_fetch_remote_containers parses pipe-delimited docker ps lines."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "myapp|nginx:latest|running|Up 2 hours\n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = _fetch_remote_containers({"name": "TC1", "host": "10.0.0.11", "user": "testuser"})

        assert len(result) == 1
        assert result[0]["name"] == "myapp"
        assert result[0]["image"] == "nginx:latest"
        assert result[0]["state"] == "running"
        assert result[0]["machine"] == "TC1"

    def test_returns_empty_on_ssh_failure(self):
        """_fetch_remote_containers returns [] when SSH returns non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 255
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = _fetch_remote_containers({"name": "TC1", "host": "10.0.0.11", "user": "testuser"})

        assert result == []

    def test_returns_empty_on_timeout(self):
        """_fetch_remote_containers returns [] on subprocess.TimeoutExpired."""
        import subprocess as _sp
        with patch("subprocess.run", side_effect=_sp.TimeoutExpired(cmd=[], timeout=6)):
            result = _fetch_remote_containers({"name": "TC2", "host": "10.0.0.12", "user": "testuser"})

        assert result == []
