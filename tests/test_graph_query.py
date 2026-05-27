from __future__ import annotations

from graph.query import GraphQuery


class FakeNode(dict):
    def __init__(self, name, labels):
        super().__init__(name=name)
        self.labels = labels


class FakeRel(dict):
    def __init__(self, start_node, end_node, rel_type):
        super().__init__()
        self.start_node = start_node
        self.end_node = end_node
        self.type = rel_type


class FakeClient:
    def __init__(self, records):
        self.records = records
        self.queries = []

    def execute_query(self, query, parameters=None):
        self.queries.append((query, parameters or {}))
        return self.records.pop(0)


def test_subgraph_includes_intermediate_path_nodes():
    start = FakeNode("A", ["Robot"])
    middle = FakeNode("B", ["Component"])
    end = FakeNode("C", ["Controller"])
    records = [{"n": start, "m": end, "r": [FakeRel(start, middle, "USES"), FakeRel(middle, end, "CONTROLS")]}]

    result = GraphQuery(FakeClient([records])).subgraph("Robot", "A", depth=2)

    assert {node["id"] for node in result["nodes"]} == {"Robot::A", "Component::B", "Controller::C"}
    assert result["edges"] == [
        {"source": "Robot::A", "target": "Component::B", "type": "USES", "properties": {}},
        {"source": "Component::B", "target": "Controller::C", "type": "CONTROLS", "properties": {}},
    ]


def test_full_graph_returns_all_named_nodes_and_relationships():
    a = FakeNode("A", ["Robot"])
    b = FakeNode("B", ["Component"])
    c = FakeNode("C", ["Controller"])
    client = FakeClient([
        [{"n": a}, {"n": b}, {"n": c}],
        [{"a": a, "r": FakeRel(a, b, "USES"), "b": b}],
    ])

    result = GraphQuery(client).full_graph(limit=5000)

    assert {node["id"] for node in result["nodes"]} == {"Robot::A", "Component::B", "Controller::C"}
    assert result["edges"] == [{"source": "Robot::A", "target": "Component::B", "type": "USES", "properties": {}}]
    assert client.queries[0][1]["limit"] == 5000
