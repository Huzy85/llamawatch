"""Tests for the service topology collector."""

import pytest
from llamawatch.collectors import topology


def test_module_constants():
    assert topology.WIDGET_ID == "topology"
    assert topology.WIDGET_NAME == "Service Topology"
    assert topology.WIDGET_MULTI_INSTANCE is False


class TestBuildGraph:
    def test_empty_config_returns_empty_graph(self):
        result = topology.build_graph({})
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_backend_creates_node(self):
        config = {"backends": [{"name": "llama", "url": "http://localhost:8080", "type": "llamacpp"}]}
        result = topology.build_graph(config)
        names = {n["id"] for n in result["nodes"]}
        assert "llama" in names

    def test_service_creates_node(self):
        config = {"services": [{"name": "atlas", "unit": "atlas.service", "type": "user"}]}
        result = topology.build_graph(config)
        names = {n["id"] for n in result["nodes"]}
        assert "atlas" in names

    def test_docker_containers_become_nodes(self):
        containers = [{"name": "myapp", "state": "running"}, {"name": "searxng", "state": "running"}]
        result = topology.build_graph({"services": []}, docker_containers=containers)
        names = {n["id"] for n in result["nodes"]}
        assert "myapp" in names
        assert "searxng" in names

    def test_node_has_required_fields(self):
        config = {"backends": [{"name": "llm", "url": "http://localhost:8080", "type": "llamacpp"}]}
        result = topology.build_graph(config)
        node = result["nodes"][0]
        for key in ("id", "label", "type", "x", "y"):
            assert key in node

    def test_known_edges_added(self):
        # Edges now come entirely from config["topology_edges"] — there are no
        # hardcoded defaults — so the test supplies the edge map.
        config = {
            "backends": [],
            "services": [
                {"name": "open-webui", "unit": "open-webui.service", "type": "user"},
                {"name": "swap-proxy", "unit": "swap-proxy.service", "type": "root"},
            ],
            "topology_edges": {
                "open-webui": ["swap-proxy"],
            },
        }
        result = topology.build_graph(config)
        edge_pairs = {(e["from"], e["to"]) for e in result["edges"]}
        assert ("open-webui", "swap-proxy") in edge_pairs


class TestCollect:
    def test_collect_returns_graph_structure(self):
        result = topology.collect(config={"backends": [], "services": []})
        assert "nodes" in result
        assert "edges" in result

    def test_collect_accepts_all_kwargs(self):
        result = topology.collect(config={}, adapters=None, widget_config={})
        assert "nodes" in result
