"""Lossless JSON subtree interning for repetitive dashboard metadata."""
import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

FORMAT = "json-dag-1"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def pack_metadata(value):
    """References preserve insertion order and decode as fresh containers."""
    nodes = []
    known = {}

    def convert(item):
        if isinstance(item, dict):
            fields = []
            for key, val in item.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                fields.extend((key, convert(val)))
            node = [1, fields]
        elif isinstance(item, list):
            node = [0, [convert(val) for val in item]]
        else:
            return item
        key = compact(node)
        index = known.get(key)
        if index is None:
            index = len(nodes)
            known[key] = index
            nodes.append(node)
        return [index]

    root = convert(value)
    return dict(format=FORMAT, root=root, nodes=nodes)


def unpack_metadata(value):
    if value.get("format") != FORMAT:
        raise ValueError("Unsupported metadata format")
    nodes = value["nodes"]

    def expand(item):
        if not isinstance(item, list):
            return item
        if len(item) != 1 or type(item[0]) is not int or not 0 <= item[0] < len(nodes):
            raise ValueError("Invalid metadata reference")
        kind, fields = nodes[item[0]]
        if kind == 0:
            return [expand(field) for field in fields]
        if kind == 1:
            return {fields[i]: expand(fields[i+1]) for i in range(0, len(fields), 2)}
        raise ValueError("Invalid metadata node")

    return expand(value["root"])


JS_DECODER = r"""
window.__UNPACK_METADATA__ = function(value) {
    if (value.format !== 'json-dag-1') throw new Error('Unknown metadata format');
    const nodes = value.nodes;
    function expand(item) {
        if (!Array.isArray(item)) return item;
        if (item.length !== 1 || !Number.isInteger(item[0]) || item[0] < 0 || item[0] >= nodes.length)
            throw new Error('Invalid metadata reference');
        const [kind, fields] = nodes[item[0]];
        if (kind === 0) return fields.map(expand);
        if (kind === 1) {
            const entries = [];
            for (let i = 0; i < fields.length; i += 2)
                entries.push([fields[i], expand(fields[i+1])]);
            return Object.fromEntries(entries);
        }
        throw new Error('Invalid metadata node');
    }
    return expand(value.root);
};
"""


def main():
    import argparse
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    html = args.source.read_bytes()
    results = []
    for name in ("portable-catalog", "plotly-figures", "plotly-events", "incident-analysis"):
        pattern = rb'<script[^>]*id="' + name.encode() + rb'"[^>]*>(.*?)</script>'
        body = re.search(pattern, html, re.S)[1]
        raw = gzip.decompress(base64.b64decode(body))
        original = json.loads(raw)
        start = time.perf_counter()
        encoded = pack_metadata(original)
        out = compact(encoded).encode("utf8")
        decoded = unpack_metadata(encoded)
        assert compact(decoded) == compact(original), name
        packed = gzip.compress(out, compresslevel=9, mtime=0)
        Path(name + ".dag.json").write_bytes(out)
        Path(name + ".original.json").write_bytes(raw)
        row = dict(id=name, old_gzip=len(base64.b64decode(body)), new_gzip=len(packed),
                   old_base64=len(body), new_base64=len(base64.b64encode(packed)),
                   new_ascii85=((len(packed)+3)//4)*5, dag_nodes=len(encoded["nodes"]),
                   seconds=round(time.perf_counter()-start, 3),
                   original_sha256=hashlib.sha256(compact(original).encode("utf8")).hexdigest())
        results.append(row)
        print(json.dumps(row), flush=True)
    Path("metadata-results.json").write_text(json.dumps(results, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()

