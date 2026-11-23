"""Tests for backward-compatible IR visual quality extensions (paper, gradient, shaderRef, text)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.engine.sample import IrSampleError, sample_scene
from app.engine.validate import validate_ir
from app.schemas.ir import DrawingIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ART_DIR = PROJECT_ROOT / "shared" / "demo" / "templates" / "art"


def _base_ir(**overrides) -> dict:
    doc = {
        "schemaVersion": "1.0",
        "seed": 42,
        "canvas": {"width": 800, "height": 600, "background": "#F6F1E7"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": [
            {
                "id": "n1",
                "parentId": None,
                "type": "circle",
                "geometry": {"cx": 400, "cy": 300, "r": 50},
                "style": {"fill": "#D97388", "stroke": None, "strokeWidth": 0, "opacity": 1.0},
            }
        ],
        "animations": [],
        "narrative": [],
    }
    doc.update(overrides)
    return doc


class ArtIRExtensionTests(unittest.TestCase):
    def test_paper_and_gradients_and_materials_valid(self) -> None:
        doc = _base_ir(
            canvas={"width": 800, "height": 600, "background": "#F6F1E7", "paper": "xuan"},
            nodes=[
                {
                    "id": "p1",
                    "parentId": None,
                    "type": "polygon",
                    "geometry": {"points": [[100, 100], [260, 100], [260, 220], [100, 220]]},
                    "style": {
                        "fill": "#D97388",
                        "stroke": "#8A3042",
                        "strokeWidth": 1.5,
                        "opacity": 0.92,
                        "lineCap": "round",
                        "lineJoin": "round",
                        "feather": 2.0,
                        "gradient": {
                            "type": "linear",
                            "angle": 120,
                            "tintFromFill": True,
                            "stops": [
                                {"offset": 0.0, "color": "#FBE3E8", "opacity": 1.0},
                                {"offset": 1.0, "color": "#B84764", "opacity": 0.9},
                            ],
                        },
                    },
                    "material": {"shaderRef": "gongbi"},
                },
                {
                    "id": "poem",
                    "parentId": None,
                    "type": "text",
                    "geometry": {
                        "x": 96,
                        "y": 64,
                        "text": "出淤泥而不染",
                        "fontSize": 20,
                        "writingMode": "vertical",
                        "fontFamily": "kai",
                        "letterSpacing": 4,
                    },
                    "style": {"fill": "#3D332B", "stroke": None, "strokeWidth": 0, "opacity": 0.85},
                },
            ],
        )
        ir = DrawingIR.model_validate(doc)
        report = validate_ir(ir)
        self.assertEqual(report.issues, [])
        self.assertEqual(report.outOfBoundsNodeCount, 0)
        scene = sample_scene(ir, 0)
        self.assertEqual(scene.paper, "xuan")
        self.assertEqual(scene.nodes[0].shader_ref, "gongbi")
        self.assertEqual(scene.nodes[1].type, "text")
        self.assertEqual(len(scene.nodes[1].points), 4)

    def test_invalid_shader_and_feather_and_text_bounds_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DrawingIR.model_validate(
                _base_ir(canvas={"width": 800, "height": 600, "background": "#FFFFFF", "paper": "invalid"})
            )
        with self.assertRaises(ValidationError):
            bad = _base_ir()
            bad["nodes"][0]["style"]["feather"] = 50.0
            DrawingIR.model_validate(bad)
        with self.assertRaises(ValidationError):
            bad = _base_ir()
            bad["nodes"][0]["material"] = {"shaderRef": "unknown_shader"}
            DrawingIR.model_validate(bad)

        # Text node placed outside 800x600 is caught by validate_ir
        oob = _base_ir(
            nodes=[
                {
                    "id": "oob-text",
                    "parentId": None,
                    "type": "text",
                    "geometry": {"x": 795, "y": 580, "text": "出淤泥而不染", "fontSize": 24, "writingMode": "vertical"},
                    "style": {"fill": "#333333", "stroke": None, "strokeWidth": 0, "opacity": 1.0},
                }
            ]
        )
        report = validate_ir(DrawingIR.model_validate(oob))
        self.assertEqual(report.outOfBoundsNodeCount, 1)

        # Sample layer still rejects unapproved dynamic shaderRef
        ir = DrawingIR.model_validate(_base_ir())
        object.__setattr__(ir.nodes[0], "material", type("M", (), {"shaderRef": "noise"})())
        with self.assertRaises(IrSampleError):
            sample_scene(ir, 0)

    def test_all_ten_art_templates_validate_and_sample(self) -> None:
        files = sorted(ART_DIR.glob("*.json"))
        self.assertEqual(len(files), 10)
        for path in files:
            spec = json.loads(path.read_text(encoding="utf-8"))
            ir = DrawingIR.model_validate(spec["irTemplate"])
            report = validate_ir(ir)
            self.assertEqual(report.issues, [], f"{path.name}: {report.issues}")
            self.assertEqual(report.outOfBoundsNodeCount, 0, path.name)
            sample_scene(ir, 0)


if __name__ == "__main__":
    unittest.main()
