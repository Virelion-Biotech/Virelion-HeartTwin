"""Build the display-only BodyParts3D reference. Optional: numpy, fast-simplification==0.2.0.

python scripts/build_reference_heart.py /path/to/organui-heart.glb
Input is https://raw.githubusercontent.com/organui/heart-3d/main/public/models/heart.glb,
pinned by SHA-256 below. No network access is performed by this converter.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path
import struct
import sys

import fast_simplification
import numpy as np

SOURCE_SHA = "b80a727d022a4ab07ccfcf15caccc228eaccb6d69ec0e72923324946ac35a946"
# Keep the ventricular tissue combined, and label cavity surfaces as spaces.
LAYERS = [
    ("Right atrial wall", [190, 103, 122], 2800),
    ("Left atrial wall", [174, 77, 101], 1800),
    ("Ventricular wall (combined)", [190, 75, 92], 8500),
    ("Right ventricular cavity", [101, 154, 189], 950),
    ("Left ventricular cavity", [214, 139, 149], 850),
    ("Ascending aorta", [204, 91, 103], 396),
    ("Aortic arch", [204, 91, 103], 670),
    ("Pulmonary trunk", [82, 143, 174], 900),
    ("Superior vena cava", [83, 137, 180], 598),
    ("Left coronary artery tree", [225, 168, 109], 2600),
    ("Right coronary artery tree", [225, 168, 109], 2800),
]


def build(path: Path):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA:
        raise ValueError(
            "Reference source checksum changed; review the source before updating"
        )
    magic, version, length = struct.unpack_from("<III", data)
    assert magic == 0x46546C67 and version == 2 and length == len(data)
    count, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A
    gltf = json.loads(data[20 : 20 + count])
    offset = 20 + count
    binary_count, binary_kind = struct.unpack_from("<II", data, offset)
    assert binary_kind == 0x004E4942
    binary = data[offset + 8 : offset + 8 + binary_count]

    def read(index):
        accessor = gltf["accessors"][index]
        view = gltf["bufferViews"][accessor["bufferView"]]
        assert not accessor.get("sparse")
        dtype = {5126: "<f4", 5125: "<u4", 5123: "<u2"}[accessor["componentType"]]
        width = {"SCALAR": 1, "VEC3": 3}[accessor["type"]]
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        item = np.dtype(dtype).itemsize
        return np.ndarray(
            (accessor["count"], width),
            dtype=dtype,
            buffer=binary,
            offset=start,
            strides=(view.get("byteStride", width * item), item),
        ).copy()

    assert len(gltf["meshes"]) == len(LAYERS)
    for node in gltf["nodes"]:
        assert not any(
            key in node
            for key in ("matrix", "translation", "rotation", "scale", "children")
        )
    nodes, faces, face_layers, layers = [], [], [], []
    records = []
    source_manifest = json.loads(
        Path("docs/anatomy/upstream-manifest.json").read_text()
    )
    for i, (mesh, (name, color, budget)) in enumerate(zip(gltf["meshes"], LAYERS)):
        assert len(mesh["primitives"]) == 1
        primitive = mesh["primitives"][0]
        assert primitive.get("mode", 4) == 4
        points = read(primitive["attributes"]["POSITION"]).astype(float) / 0.02
        triangles = read(primitive["indices"]).reshape(-1, 3).astype(np.int32)
        before = {"vertices": len(points), "faces": len(triangles)}
        if len(triangles) > budget:
            points, triangles = fast_simplification.simplify(
                points, triangles, target_count=budget
            )
        assert (
            np.isfinite(points).all()
            and triangles.min() >= 0
            and triangles.max() < len(points)
        )
        start = len(nodes)
        nodes.extend(np.round(points, 5).tolist())
        faces.extend((triangles + start).tolist())
        face_layers.extend([i] * len(triangles))
        mapping = source_manifest["structures"][i]
        assert mapping["id"] == mesh["name"]
        layers.append(
            {
                "name": name,
                "color": color,
                "visible": "cavity" not in name,
                "source_id": mapping["fma"],
            }
        )
        records.append(
            {
                "id": mesh["name"],
                "label": name,
                "source_fma": mapping["fma"],
                "source_elements": [s["element"] for s in mapping["sources"]],
                "before": before,
                "after": {"vertices": len(points), "faces": len(triangles)},
            }
        )
    asset = {
        "geometry": {
            "node_xyz": nodes,
            "triangles": faces,
            "units": {"node_xyz": "mm"},
        },
        "layers": layers,
        "face_layers": face_layers,
    }
    raw = json.dumps(asset, separators=(",", ":"), allow_nan=False).encode()
    compressed = gzip.compress(raw, mtime=0)
    envelope = {
        "schema": "hearttwin-anatomy-reference/1",
        "encoding": "gzip-base64",
        "geometry_sha256": hashlib.sha256(raw).hexdigest(),
        "data": base64.b64encode(compressed).decode(),
    }
    output = Path("hearttwin/webui/heart-reference.json")
    output.write_text(json.dumps(envelope, separators=(",", ":")) + "\n")
    manifest = {
        "dataset": "BodyParts3D 4.0 adult male reference",
        "source_url": "https://raw.githubusercontent.com/organui/heart-3d/main/public/models/heart.glb",
        "source_glb_sha256": SOURCE_SHA,
        "source_license": "CC-BY-4.0",
        "license_url": "https://dbarchive.biosciencedbc.jp/en/bodyparts3d/lic.html",
        "modifications": [
            "OrganUI's source selection and OBJ-to-GLB conversion retained",
            "GLB display scale divided by 0.02 to restore millimetre dimensions, retaining centered Y-up axes",
            "Per-structure quadric surface decimation for display only; coordinates rounded to 5 decimal places",
            "Converted to compact JSON, gzipped and base64 encoded; illustrative colors adjusted",
            "Ventricular cavity surfaces hidden initially; no activation or physiological field added",
        ],
        "tool_versions": {
            "numpy": np.__version__,
            "fast_simplification": fast_simplification.__version__,
        },
        "asset_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "geometry_sha256": hashlib.sha256(raw).hexdigest(),
        "vertices": len(nodes),
        "surface_faces": len(faces),
        "structures": records,
    }
    Path("docs/anatomy/reference-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "vertices": len(nodes),
                "faces": len(faces),
                "file_bytes": output.stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]))
