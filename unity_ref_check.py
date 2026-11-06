#!/usr/bin/env python3
"""Revisa un proyecto Unity sin abrir Unity.

Comprueba:
  - .meta que faltan (asset sin .meta) o huérfanos (.meta sin asset)
  - GUID duplicados entre .meta del proyecto
  - referencias GUID (escenas, prefabs, materiales, Shader Graph, VFX Graph...) que no existen
    ni en el proyecto ni en los paquetes que usa (packages-lock.json)
  - claves o tokens subidos por error

Los GUID de los paquetes salen de:
  - registro (packages.unity.com, npm): tarball del paquete
  - integrados (builtin): data/builtin-<versión>.txt.gz; si la versión de Unity no está, el repo
    Unity-Technologies/Graphics (rama <major.minor>/staging) para los de render
  - embebidos o locales: sus .meta en Packages/

Línea base: lo que aparece en el fichero de línea base se informa como conocido y no hace fallar.
Solo con Python 3 estándar.
"""
import argparse
import fnmatch
import gzip
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

TEXT_EXT = {".mat", ".prefab", ".unity", ".asset", ".shadergraph", ".shadersubgraph", ".vfx", ".vfxoperator",
            ".vfxblock", ".controller", ".overridecontroller", ".playable", ".anim", ".lighting", ".mixer",
            ".terrainlayer", ".spriteatlas", ".signal", ".preset"}
GRAPH_EXT = {".shadergraph", ".shadersubgraph"}
YAML_REF = re.compile(r"(?m)^\s*-?\s*(\w+)?:?\s*\{fileID: -?\d+, guid: ([0-9a-f]{32})")
JSON_REF = re.compile(r'\\*"guid\\*"\s*:\s*\\*"([0-9a-f]{32})')
GRAPHICS_PACKAGES = ("com.unity.render-pipelines.", "com.unity.shadergraph", "com.unity.visualeffectgraph")
# Objeto vacío que versiones antiguas de VFX Graph guardaban en los .vfx; Unity lo ignora
KNOWN_HARMLESS = {"081ffb0090424ba4cb05370a42ead6b9"}
# Campos que no rompen nada si faltan: recursos obsoletos de URP 17 (Probe Volumes, XR) y datos
# horneados de las escenas (iluminación, oclusión), que se regeneran al volver a hornear
HARMLESS_KEY_PREFIXES = ("probeVolume", "probeSampling", "xrSystemData", "m_LightingDataAsset", "m_OcclusionCullingData")
DEFAULT_IGNORE = ["Assets/Samples/**", "Assets/TextMesh Pro/**"]
SKIP_DIRS = {".git", "Library", "Temp", "Logs", "obj", "UserSettings", "Build", "Builds", "node_modules"}
SECRET_PATTERNS = [
    ("token de npm", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("token de GitHub", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("clave privada", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("clave de AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("clave de Google", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("token de Slack", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")),
]


def category(ext, key):
    if ext in GRAPH_EXT:
        return "subgrafo/textura en Shader Graph"
    if ext.startswith(".vfx"):
        return "referencia en VFX Graph"
    return {
        "m_Shader": "shader (material rosa)",
        "m_Script": "script (Missing Script)",
        "m_SourcePrefab": "prefab anidado",
        "m_ParentPrefab": "prefab anidado",
        "m_Mesh": "malla",
        "m_Texture": "textura residual en material" if ext == ".mat" else "textura",
    }.get(key, "otros")


def read_guid(meta_path):
    try:
        with open(meta_path, encoding="utf-8", errors="ignore") as fh:
            for _ in range(5):
                line = fh.readline()
                if line.startswith("guid: "):
                    return line[6:].strip()
    except OSError:
        pass
    return None


def is_folder_meta(meta_path):
    try:
        with open(meta_path, encoding="utf-8", errors="ignore") as fh:
            return "folderAsset: yes" in fh.read(500)
    except OSError:
        return False


def meta_guids(folder):
    found = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith("~")]
        for f in files:
            if f.endswith(".meta"):
                g = read_guid(os.path.join(root, f))
                if g:
                    found.setdefault(g, []).append(os.path.join(root, f))
    return found


def rel(path, project):
    return os.path.relpath(path, project).replace("\\", "/")


def matches_any(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "unity-ref-check"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def registry_guids(name, version, url, cache):
    path = os.path.join(cache, "registry", f"{name}@{version}.txt")
    if os.path.exists(path):
        with open(path, encoding="ascii") as fh:
            return set(fh.read().split())
    base = (url or "https://packages.unity.com").rstrip("/")
    meta = json.loads(http_get(f"{base}/{name}"))
    tarball = meta["versions"][version]["dist"]["tarball"]
    guids = set()
    with tarfile.open(fileobj=io.BytesIO(http_get(tarball)), mode="r:gz") as tar:
        for member in tar:
            if member.isfile() and member.name.endswith(".meta"):
                for line in tar.extractfile(member).read().decode("utf-8", "ignore").splitlines()[:5]:
                    if line.startswith("guid: "):
                        guids.add(line[6:].strip())
                        break
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as fh:
        fh.write("\n".join(sorted(guids)))
    return guids


def builtin_db(unity_version):
    path = os.path.join(HERE, "data", f"builtin-{unity_version}.txt.gz")
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="ascii") as fh:
        return set(fh.read().split())


def graphics_guids(major_minor, cache):
    """Sparse clone de Unity-Technologies/Graphics (solo .meta de Packages/) en la rama <major.minor>/staging."""
    dest = os.path.join(cache, f"graphics-{major_minor}")
    done = os.path.join(dest, ".done")
    if not os.path.exists(done):
        branch = f"{major_minor}/staging"
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--branch", branch,
                        "https://github.com/Unity-Technologies/Graphics.git", dest], check=True)
        subprocess.run(["git", "-C", dest, "sparse-checkout", "set", "--no-cone", "/Packages/**/*.meta"], check=True)
        subprocess.run(["git", "-C", dest, "checkout"], check=True)
        open(done, "w").close()
    return set(meta_guids(os.path.join(dest, "Packages")).keys())


def package_guids(project, cache, log):
    known = set()
    pkg_dir = os.path.join(project, "Packages")
    lock_path = os.path.join(pkg_dir, "packages-lock.json")
    version_file = os.path.join(project, "ProjectSettings", "ProjectVersion.txt")
    unity_version = ""
    if os.path.exists(version_file):
        with open(version_file, encoding="utf-8") as fh:
            m = re.search(r"m_EditorVersion:\s*(\S+)", fh.read())
            unity_version = m.group(1) if m else ""
    # embebidos (carpetas con package.json dentro de Packages/)
    if os.path.isdir(pkg_dir):
        for entry in os.listdir(pkg_dir):
            full = os.path.join(pkg_dir, entry)
            if os.path.isdir(full) and os.path.exists(os.path.join(full, "package.json")):
                known |= set(meta_guids(full).keys())
    if not os.path.exists(lock_path):
        log.append("Sin Packages/packages-lock.json: no se comprueban los paquetes")
        return known, unity_version
    with open(lock_path, encoding="utf-8") as fh:
        deps = json.load(fh).get("dependencies", {})
    builtin = builtin_db(unity_version)
    need_graphics = False
    if builtin is None:
        log.append(f"Unity {unity_version} no está en data/: se usan todas las bases de datos y el repo Graphics")
        builtin = set()
        for f in os.listdir(os.path.join(HERE, "data")):
            if f.startswith("builtin-"):
                with gzip.open(os.path.join(HERE, "data", f), "rt", encoding="ascii") as fh:
                    builtin |= set(fh.read().split())
    for name, info in deps.items():
        source = info.get("source")
        if name.startswith("com.unity.modules."):
            continue
        if source == "registry":
            try:
                known |= registry_guids(name, info["version"], info.get("url"), cache)
            except Exception as exc:  # paquete retirado, red...
                log.append(f"No se pudo descargar {name}@{info['version']}: {exc}")
        elif source == "builtin":
            if name.startswith(GRAPHICS_PACKAGES) and builtin_db(unity_version) is None:
                need_graphics = True
        elif source == "local":
            path = info["version"].replace("file:", "")
            path = path if os.path.isabs(path) else os.path.join(pkg_dir, path)
            if os.path.isdir(path):
                known |= set(meta_guids(path).keys())
        elif source == "git":
            log.append(f"Paquete git {name}: no se comprueba (sus referencias pueden salir como rotas)")
    known |= builtin
    if need_graphics and unity_version.count(".") >= 2:
        major_minor = ".".join(unity_version.split(".")[:2])
        try:
            known |= graphics_guids(major_minor, cache)
        except Exception as exc:
            log.append(f"No se pudo leer Unity-Technologies/Graphics ({major_minor}/staging): {exc}")
    return known, unity_version


def check_metas(project, ignore):
    problems = []
    assets = os.path.join(project, "Assets")
    for root, dirs, files in os.walk(assets):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith("~")]
        names = set(dirs) | set(files)
        for name in dirs + files:
            if name.endswith(".meta"):
                target = name[:-5]
                if target not in names and not target.startswith("."):
                    path = rel(os.path.join(root, name), project)
                    # .meta de carpeta vacía: git no guarda carpetas vacías y Unity lo borra al abrir; no rompe nada
                    if not matches_any(path, ignore) and not is_folder_meta(os.path.join(root, name)):
                        problems.append(("meta huérfano", path, ""))
            elif not name.startswith(".") and not name.endswith("~") and name + ".meta" not in names:
                path = rel(os.path.join(root, name), project)
                if not matches_any(path, ignore):
                    problems.append(("falta .meta", path, ""))
    return problems


def check_refs(project, known, ignore):
    refs = {}
    assets = os.path.join(project, "Assets")
    for root, dirs, files in os.walk(assets):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith("~")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in TEXT_EXT:
                continue
            full = os.path.join(root, f)
            path = rel(full, project)
            if matches_any(path, ignore):
                continue
            try:
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            hits = [(m.group(1) or "", m.group(2)) for m in YAML_REF.finditer(text)]
            if ext in GRAPH_EXT:
                hits += [("", m.group(1)) for m in JSON_REF.finditer(text)]
            for key, guid in hits:
                if guid.startswith("0000000000000000") or guid in known or guid in KNOWN_HARMLESS:
                    continue
                if key.startswith(HARMLESS_KEY_PREFIXES):
                    continue
                cat = category(ext, key)
                if cat == "textura residual en material":
                    continue  # texturas guardadas de shaders anteriores: Unity las ignora
                refs.setdefault((cat, guid), set()).add(path)
    return [(cat, guid, path) for (cat, guid), paths in refs.items() for path in sorted(paths)]


def check_secrets(project):
    found = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            full = os.path.join(root, f)
            try:
                if os.path.getsize(full) > 2_000_000:
                    continue
                with open(full, encoding="utf-8", errors="strict") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for label, rx in SECRET_PATTERNS:
                if rx.search(text):
                    found.append(("posible secreto", label, rel(full, project)))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=".")
    ap.add_argument("--baseline", default=".github/unity-ref-check-baseline.txt")
    ap.add_argument("--ignore", action="append", default=[], help="glob de rutas a ignorar (repetible; admite varias separadas por salto de línea)")
    ap.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".cache", "unity-ref-check"))
    ap.add_argument("--write-baseline", action="store_true", help="guarda todo lo encontrado como línea base")
    ap.add_argument("--no-secrets", action="store_true")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    if not os.path.exists(os.path.join(project, "ProjectSettings", "ProjectVersion.txt")):
        print(f"{project} no es un proyecto Unity (falta ProjectSettings/ProjectVersion.txt): nada que revisar")
        return 0
    ignore = DEFAULT_IGNORE + [g.strip() for item in args.ignore for g in re.split(r"[\n,]", item) if g.strip()]
    log = []
    known_packages, unity_version = package_guids(project, args.cache, log)
    project_metas = meta_guids(os.path.join(project, "Assets"))
    known = known_packages | set(project_metas.keys())

    findings = check_metas(project, ignore)
    for guid, paths in project_metas.items():
        visible = [rel(p, project) for p in paths if not matches_any(rel(p, project), ignore)]
        if len(paths) > 1 and visible:
            for p in visible:
                findings.append(("GUID duplicado", guid, p))
    findings += check_refs(project, known, ignore)
    if not args.no_secrets:
        findings += check_secrets(project)

    baseline_path = os.path.join(project, args.baseline)
    keys = ["\t".join(f) for f in findings]
    if args.write_baseline:
        os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
        secrets = [k for k in keys if k.startswith("posible secreto")]
        with open(baseline_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Línea base de unity-ref-check: problemas conocidos que no hacen fallar la revisión.\n")
            fh.write("# Formato: tipo<TAB>guid o ruta<TAB>fichero. Se regenera con --write-baseline.\n")
            fh.write("\n".join(sorted(set(k for k in keys if k not in secrets))) + "\n")
        print(f"Línea base escrita: {len(set(keys)) - len(secrets)} entradas en {args.baseline}")
        if secrets:
            print("Los posibles secretos no se añaden nunca a la línea base:")
            for s in secrets:
                print("  " + s.replace("\t", " | "))
        return 1 if secrets else 0
    accepted = set()
    if os.path.exists(baseline_path):
        with open(baseline_path, encoding="utf-8") as fh:
            accepted = {l.rstrip("\n") for l in fh if l.strip() and not l.startswith("#")}
    new = [f for f, k in zip(findings, keys) if k not in accepted or f[0] == "posible secreto"]
    known_count = len(findings) - len(new)

    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    print(f"Unity {unity_version or '?'} · {len(project_metas)} GUIDs del proyecto · {len(known_packages)} de paquetes")
    for line in log:
        print(f"  aviso: {line}")
    for kind, what, path in new:
        msg = f"{kind}: {what}" + (f" en {path}" if path else "")
        print(f"::error file={path or what}::{msg}" if in_actions else f"NUEVO   {msg}")
    print(f"Problemas nuevos: {len(new)} · conocidos (línea base): {known_count}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## Revisión de referencias de Unity\n\nUnity `{unity_version}` · problemas nuevos: **{len(new)}** · conocidos: {known_count}\n\n")
            if new:
                fh.write("| Tipo | GUID / ruta | Fichero |\n|---|---|---|\n")
                for kind, what, path in new[:200]:
                    fh.write(f"| {kind} | `{what}` | `{path}` |\n")
            for line in log:
                fh.write(f"\n> {line}\n")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
