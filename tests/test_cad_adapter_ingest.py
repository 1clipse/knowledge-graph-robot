from __future__ import annotations

from loaders.cad_adapter import CADGraphData
from api.routes.ingest import _cad_graph_data_to_result


def test_cad_graph_data_converts_to_extraction_result():
    data = CADGraphData(
        file_path="part.dxf",
        file_format="DXF",
        entities=[
            {
                "name": "part",
                "type": "Drawing",
                "properties": {"file_format": "DXF"},
                "confidence": 0.95,
                "source": "part.dxf",
            },
            {
                "name": "BasePlate",
                "type": "Part",
                "properties": {"part_number": "BasePlate"},
                "confidence": 0.9,
                "source": "part.dxf",
            },
        ],
        relations=[
            {
                "source": {"name": "part", "type": "Drawing"},
                "target": {"name": "BasePlate", "type": "Part"},
                "relation_type": "drawing_defines",
                "properties": {},
                "confidence": 0.9,
            }
        ],
    )

    result = _cad_graph_data_to_result(data)

    assert [entity.type for entity in result.entities] == ["Drawing", "Part"]
    assert result.relations[0].relation_type == "drawing_defines"
    assert result.relations[0].source.type == "Drawing"
    assert result.relations[0].target.type == "Part"


def test_cad_graph_data_drops_relations_to_filtered_entities():
    data = CADGraphData(
        file_path="part.dxf",
        file_format="DXF",
        entities=[
            {"name": "*Model_Space", "type": "Part", "properties": {}},
            {"name": "BasePlate", "type": "Part", "properties": {}},
        ],
        relations=[
            {
                "source": {"name": "*Model_Space", "type": "Part"},
                "target": {"name": "BasePlate", "type": "Part"},
                "relation_type": "component_compatible",
                "properties": {},
            }
        ],
    )

    result = _cad_graph_data_to_result(data)

    assert [entity.name for entity in result.entities] == ["BasePlate"]
    assert result.relations == []
