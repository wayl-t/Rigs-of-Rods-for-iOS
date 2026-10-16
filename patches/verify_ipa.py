#!/usr/bin/env python3
"""Check that an assembled RoR IPA can actually start.

Everything this script tests is something that has already burned a full
build + install + walk through the Files app, and none of it is visible from
the fact that the build was green:

  * a top-level directory named ``resources``/``Resources`` makes iOS refuse to
    install the bundle at all (PORT_FINDINGS 7.22);
  * ``rorres/skeleton.zip`` is hard-required by
    ``AppContext::SetUpConfigSkeleton()``;
  * ``rorres/mygui.zip`` must carry ``MyGUI_VP.glsles`` and ``MyGUI_FP.glsles``.
    ``MyGUI::OgreRenderManager::initialise()`` (3.4.0) creates GPU programs with
    those exact names and loads each from a same-named source file.  RoR ships
    the pre-3.4 names ``MyGUI_Ogre_VP.*`` / ``MyGUI_Ogre_FP.*``, so on a render
    system without fixed function - every render system iOS has - MyGUI throws
    out of ``RoRMain``, the main loop's ``catch`` swallows it and the app opens
    onto a black screen without crashing.  Desktop never sees it: there OGRE
    reports RSC_FIXED_FUNCTION and the shader path is skipped.

Usage:
    python3 patches/verify_ipa.py RoR-ios.ipa
"""
import io
import sys
import zipfile

MYGUI_SHADERS = ("MyGUI_VP.glsles", "MyGUI_FP.glsles")


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2

    ipa = zipfile.ZipFile(argv[1])
    names = ipa.namelist()
    bad = []

    # ------------------------------------------------------------------ #
    # 1. The bundle must not contain a directory named resources/.         #
    # ------------------------------------------------------------------ #
    top = {n.split("/")[2] for n in names
           if n.startswith("Payload/") and len(n.split("/")) > 2}
    for dirname in ("resources", "Resources"):
        if dirname in top:
            bad.append(
                "the bundle carries a top-level %s/ directory - iOS refuses to "
                "install such an app (PORT_FINDINGS 7.22)" % dirname)
    print("  ok      bundle top level: %s" % ", ".join(sorted(top)))

    if not any(n.startswith("Payload/") and n.split("/")[-1] == "RoR" for n in names):
        bad.append("no Payload/RoR.app/RoR executable in the archive")

    # ------------------------------------------------------------------ #
    # 2. skeleton.zip, which SetUpConfigSkeleton() hard-requires.          #
    # ------------------------------------------------------------------ #
    if not any(n.endswith("/rorres/skeleton.zip") for n in names):
        bad.append("rorres/skeleton.zip is missing - SetUpConfigSkeleton() "
                   "reports a faulty resource folder and RoR exits")
    else:
        print("  ok      rorres/skeleton.zip")

    # ------------------------------------------------------------------ #
    # 3. MyGUI's GLES2 shaders, under the names 3.4.0 asks for.           #
    # ------------------------------------------------------------------ #
    nested = [n for n in names if n.endswith("/rorres/mygui.zip")]
    if not nested:
        bad.append("rorres/mygui.zip is missing - MyGUI cannot start")
    else:
        inner = zipfile.ZipFile(io.BytesIO(ipa.read(nested[0])))
        have = set(inner.namelist())
        missing = [n for n in MYGUI_SHADERS if n not in have]
        if missing:
            bad.append(
                "rorres/mygui.zip is missing %s - MyGUI::OgreRenderManager "
                "creates GPU programs of exactly those names and loads them "
                "from same-named source files, so RoRMain throws, the main "
                "loop's catch swallows it and the app opens onto a black "
                "screen" % ", ".join(missing))
        else:
            versions = {}
            for name in MYGUI_SHADERS:
                body = inner.read(name).decode("utf-8", "replace")
                versions[name] = body.splitlines()[0].strip()
                if "void main" not in body:
                    bad.append("%s declares no main()" % name)
            # The pair has to link into one program, and a linked program may
            # not mix GLSL ES versions.
            if len(set(versions.values())) != 1:
                bad.append(
                    "the MyGUI shaders disagree on the GLSL ES version, which "
                    "a linked program cannot do: %r" % versions)
            print("  ok      rorres/mygui.zip: %s (%s)"
                  % (", ".join(MYGUI_SHADERS), versions[MYGUI_SHADERS[0]]))

    for line in bad:
        print("  MISSING " + line)
        print("::error::" + line)

    if bad:
        print("%d bundle problem(s) - the app would install but not start" % len(bad))
        return 1
    print("bundle: everything the startup path needs is present")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
