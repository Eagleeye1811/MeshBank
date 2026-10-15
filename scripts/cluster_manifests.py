#!/usr/bin/env python3
"""Print the manifests that belong in one cluster of the multi-cluster setup.

An object belongs to a cluster when its ``meshbank.io/workload`` label names a
workload placed there (scripts/mesh_topology.py), or its ``meshbank.io/cluster``
label is that cluster. Objects with neither label are shared and go everywhere.

    scripts/cluster_manifests.py aws k8s/*.yaml | kubectl --context kind-meshbank-aws apply -f -
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from mesh_topology import CLUSTERS, WORKLOADS


def belongs(doc: dict, cluster: str) -> bool:
    labels = doc.get("metadata", {}).get("labels") or {}
    if "meshbank.io/cluster" in labels:
        return labels["meshbank.io/cluster"] == cluster
    workload = labels.get("meshbank.io/workload")
    if workload is None:
        return True
    if workload not in WORKLOADS:
        raise ValueError(f"{doc['metadata']['name']}: unknown workload {workload}")
    return WORKLOADS[workload].cluster == cluster


def select(cluster: str, paths: list[Path]) -> list[dict]:
    return [
        doc
        for path in paths
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if doc and belongs(doc, cluster)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cluster", choices=CLUSTERS)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--names", action="store_true", help="list kind/name only")
    args = parser.parse_args()
    docs = select(args.cluster, args.files)
    if args.names:
        for doc in docs:
            print(f"{doc['kind']}/{doc['metadata']['name']}")
    else:
        yaml.safe_dump_all(docs, sys.stdout, sort_keys=False, explicit_start=True, width=1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
