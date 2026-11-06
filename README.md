<header>

# **unity-ref-check**

_**Acción de GitHub que revisa un proyecto Unity sin abrir Unity: .meta, GUID duplicados, referencias rotas y claves subidas por error.**_

</header>

## Qué comprueba

* **`.meta`** que faltan (asset sin `.meta`) o huérfanos (`.meta` sin asset) en `Assets/`.
* **GUID duplicados** entre `.meta` del proyecto.
* **Referencias rotas**: GUID usados en escenas, prefabs, materiales, Shader Graph, VFX Graph, controladores, etc. que no existen ni en el proyecto ni en los paquetes que usa. Se clasifican como en el Inspector: material rosa (shader), Missing Script, prefab anidado, malla, subgrafo/textura de Shader Graph, VFX Graph u otros.
* **Claves y tokens** subidos por error (npm, GitHub, claves privadas, AWS, Google, Slack).

Los GUID de los paquetes salen de `Packages/packages-lock.json`:

| Origen del paquete | De dónde salen sus GUID |
|---|---|
| Registro (packages.unity.com, npm) | Tarball del paquete (con caché) |
| Integrado en el editor (URP, Shader Graph, VFX Graph, ugui…) | `data/builtin-<versión>.txt.gz`. Si la versión de Unity no está, todas las bases de datos más el repo [Unity-Technologies/Graphics](https://github.com/Unity-Technologies/Graphics) (rama `<major.minor>/staging`) para los de render |
| Embebido o local | Sus `.meta` en `Packages/` |

No se consideran errores: GUID que empiezan por 16 ceros (recursos internos de Unity), texturas que los materiales guardan de shaders anteriores, campos obsoletos de URP 17 (Probe Volumes, XR), datos horneados de las escenas y el objeto vacío `081ffb00…` de versiones antiguas de VFX Graph. Se ignoran `Assets/Samples/**` y `Assets/TextMesh Pro/**`.

## Uso

```yaml
# .github/workflows/unity-ref-check.yml
name: Revisión de referencias
on: [push, pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: JaimeCamachoDev/unity-ref-check@v1
```

Entradas opcionales: `project` (carpeta del proyecto, por defecto `.`), `baseline`, `ignore` (globs, uno por línea) y `secrets` (`"false"` para no buscar claves).

## Línea base

Los problemas que ya se conocen y no tienen arreglo (por ejemplo, partes de assets de terceros importados incompletos) se guardan en `.github/unity-ref-check-baseline.txt` y no hacen fallar la revisión. Solo falla con problemas **nuevos**. Los posibles secretos nunca entran en la línea base.

Para generarla o regenerarla en local:

```bash
python unity_ref_check.py --project <ruta del proyecto> --write-baseline
```

## Actualizar la base de datos de un editor

```bash
python tools/build_builtin_db.py "C:/Program Files/Unity/Hub/Editor/<versión>"
```
