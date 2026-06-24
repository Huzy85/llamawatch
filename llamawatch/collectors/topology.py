"""Service topology collector — builds a node-edge graph of services, backends, and containers."""

import math

WIDGET_ID = "topology"
WIDGET_NAME = "Service Topology"
WIDGET_ICON = "\U0001f578"  # spider web
WIDGET_DESCRIPTION = "Visual graph of how services connect"
WIDGET_DEFAULT_SIZE = {"w": 6, "h": 4, "minW": 4, "minH": 3}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

# Service connections come entirely from config["topology_edges"], a map of
# {service_name: [connected_service, ...]}. Empty by default — the graph is
# built from your configured backends, services and containers plus any edges
# you define. No personal topology is baked into source.
_DEFAULT_EDGES: dict[str, list] = {}


def build_graph(config: dict, docker_containers: list[dict] | None = None) -> dict:
    """Build {nodes: [{id, label, type, status, x, y}], edges: [{from, to}]}."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    for backend in config.get("backends", []):
        name = backend.get("name", "")
        if name:
            nodes[name] = {"id": name, "label": name, "type": "backend", "status": "unknown"}

    for svc in config.get("services", []):
        name = svc.get("name", "")
        if name:
            nodes[name] = {"id": name, "label": name, "type": "service", "status": "unknown"}

    if docker_containers:
        for c in docker_containers:
            name = c.get("name", "")
            if name and name not in nodes:
                nodes[name] = {"id": name, "label": name, "type": "container", "status": c.get("state", "unknown")}

    custom_edges = config.get("topology_edges", {})
    all_edge_map = {**_DEFAULT_EDGES, **custom_edges}
    for source, targets in all_edge_map.items():
        if source not in nodes:
            continue
        for target in targets:
            if target in nodes:
                edge_key = (source, target)
                if edge_key not in seen_edges:
                    edges.append({"from": source, "to": target})
                    seen_edges.add(edge_key)

    # Circular layout
    node_list = list(nodes.values())
    n = len(node_list)
    cx, cy, radius = 200, 150, min(120, max(60, n * 15))
    for i, node in enumerate(node_list):
        angle = (2 * math.pi * i) / max(n, 1)
        node["x"] = round(cx + radius * math.cos(angle))
        node["y"] = round(cy + radius * math.sin(angle))

    return {"nodes": node_list, "edges": edges}


def _get_docker_containers() -> list[dict]:
    try:
        from llamawatch.collectors.docker_collector import collect as docker_collect
        data = docker_collect()
        return data.get("containers", []) if data.get("available") else []
    except Exception:
        return []


def collect(config=None, adapters=None, widget_config=None) -> dict:
    cfg = config or {}
    containers = _get_docker_containers()
    return build_graph(cfg, docker_containers=containers)
