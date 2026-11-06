#!/usr/bin/env python3
"""Genera data/builtin-<versión>.txt.gz con los GUID de los paquetes integrados de un editor de Unity.

Uso: python tools/build_builtin_db.py "C:/Program Files/Unity/Hub/Editor/6000.3.27f1" [más editores...]
"""
import gzip
import os
import sys

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

for editor in sys.argv[1:]:
    version = os.path.basename(os.path.normpath(editor))
    base = os.path.join(editor, "Editor", "Data", "Resources", "PackageManager", "BuiltInPackages")
    guids = set()
    for root, _, files in os.walk(base):
        for f in files:
            if f.endswith(".meta"):
                with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if line.startswith("guid: "):
                            guids.add(line[6:].strip())
                            break
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"builtin-{version}.txt.gz")
    with gzip.open(path, "wt", encoding="ascii") as fh:
        fh.write("\n".join(sorted(guids)) + "\n")
    print(f"{version}: {len(guids)} GUIDs -> {path}")
