"""Diagnose the AssetLibResolver package inside a running Houdini session.

Run from Houdini's Python Shell (one line, so it pastes cleanly):

    exec(open("/path/to/understage-resolver/houdini/tools/diagnose.py").read())

Optionally set TEST_URI first to resolve a particular asset:

    TEST_URI = "ustage:robots/arm2dof?v=1"; exec(open(".../diagnose.py").read())

Every step runs even if an earlier one fails; failures print a traceback.
"""


def _assetlib_diagnose(test_uri=None):
    import os
    import traceback

    import hou

    def step(title, fn):
        try:
            print(f"  {title}: {fn()}")
        except Exception:
            print(f"  {title}: FAILED")
            print("    " + traceback.format_exc().replace("\n", "\n    "))

    print(f"AssetLibResolver diagnostics — {hou.applicationVersionString()}")

    print("Package")
    step("package dir", lambda: hou.text.expandString("$ASSETLIB_HOUDINI_PACKAGE") or "(not set: package not loaded?)")
    step("HOUDINI_PATH has package", lambda: any(
        "AssetLibResolver" in p or "houdini/install" in p
        for p in hou.text.expandString("$HOUDINI_PATH").split(":")))
    for var in ("ASSET_LIBRARY_ROOT", "ASSET_LIBRARY_MANIFEST", "ASSET_LIBRARY_OFFLINE",
                "PXR_PLUGINPATH_NAME", "TF_DEBUG"):
        step(var, lambda var=var: os.environ.get(var))

    print("USD plugin")

    def plugin():
        from pxr import Plug
        p = Plug.Registry().GetPluginWithName("AssetLibResolver")
        if not p:
            return "NOT REGISTERED (dso/usd_plugins not on HOUDINI_PATH?)"
        return f"{p.path} (loaded: {p.isLoaded})"
    step("AssetLibResolver", plugin)

    def resolvers():
        from pxr import Plug
        ar = Plug.Registry.FindTypeByName("ArResolver")
        names = [t.typeName for t in Plug.Registry.GetAllDerivedTypes(ar)]
        return ", ".join(sorted(names))
    step("registered ArResolver types", resolvers)

    def primary():
        from pxr import Ar
        return type(Ar.GetUnderlyingResolver()).__name__
    step("primary resolver", primary)

    print("Resolution")

    def scheme():
        import AssetLibResolver
        return AssetLibResolver.GetUriScheme()
    step("python module", lambda: __import__("AssetLibResolver").__file__)
    step("URI scheme", scheme)

    def identifier():
        from pxr import Ar
        s = __import__("AssetLibResolver").GetUriScheme()
        out = Ar.GetResolver().CreateIdentifier(f"{s}://robots//probe")
        ok = out == f"{s}:robots/probe"
        return f"{out!r} ({'handled by AssetLibResolver' if ok else 'NOT handled by AssetLibResolver'})"
    step("canonicalization", identifier)

    root = os.environ.get("ASSET_LIBRARY_ROOT")
    if root:
        step("library root exists", lambda: os.path.isdir(root))
        step("library root contents", lambda: sorted(os.listdir(root))[:20])

    if test_uri:
        def resolve():
            from pxr import Ar
            r = Ar.GetResolver().Resolve(test_uri).GetPathString()
            return repr(r) if r else "'' (did not resolve)"
        step(f"Resolve({test_uri!r})", resolve)
        step(f"hou.readFile({test_uri!r})", lambda: f"{len(hou.readFile(test_uri))} bytes")
        step("why", lambda: _explain(test_uri))
    else:
        print("  (set TEST_URI to resolve a specific asset)")


def _explain(uri):
    """Where the resolver looked for `uri`, and what it found there."""
    import json
    import os

    from pxr import Ar

    if Ar.GetResolver().Resolve(uri):
        return "resolved fine"
    asset = uri.split(":", 1)[1].split("?", 1)[0].strip("/")
    manifest = os.environ.get("ASSET_LIBRARY_MANIFEST")
    root, source = os.environ.get("ASSET_LIBRARY_ROOT"), "$ASSET_LIBRARY_ROOT"
    pins = {}
    if manifest:
        doc = json.load(open(manifest))
        pins = doc.get("assets", {})
        if doc.get("root"):
            root = os.path.normpath(os.path.join(os.path.dirname(manifest), doc["root"]))
            source = f"manifest {manifest}"
    if not root:
        return "no library root: set ASSET_LIBRARY_ROOT or ASSET_LIBRARY_MANIFEST"
    adir = os.path.join(root, asset)
    if not os.path.isdir(adir):
        return (f"no asset '{asset}' in library {root} (from {source}); "
                f"set ASSET_LIBRARY_ROOT / ASSET_LIBRARY_MANIFEST to the library that has it")
    versions = sorted(v for v in os.listdir(adir) if v.startswith("v"))
    if os.path.exists(os.path.join(root, ".assetlib.json")):
        versions.append("(+ remote versions via .assetlib.json)")
    return (f"asset folder {adir} exists; local versions {versions}; "
            f"manifest pin {pins.get(asset)!r}; requested {uri!r}. "
            f"Set TF_DEBUG=ASSETLIB_RESOLVER before launching Houdini for a trace")


_assetlib_diagnose(globals().get("TEST_URI"))
