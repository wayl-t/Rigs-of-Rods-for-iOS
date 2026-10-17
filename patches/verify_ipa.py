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

  * the executable must actually carry the RTShader System bootstrap, and
    ``rorres/rtshader.zip`` must carry the ``FFPLib_*.glsl`` library it
    parses: GLES2 has no fixed function, and 240 of RoR's 273 materials are
    written without shaders, so without the RTSS the first thing the app
    renders - the main menu wallpaper - throws out of ``RoRMain`` with
    "RenderSystem does not support FixedFunction" (PORT_FINDINGS 7.27).

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

    # ------------------------------------------------------------------ #
    # 4. The RTShader System must be compiled in and have its media.      #
    # ------------------------------------------------------------------ #
    # 240 of RoR's 273 materials are written without shaders, and GLES2 has
    # no fixed function, so without the RTSS the very first thing the app
    # renders - the main menu wallpaper - throws out of RoRMain with
    #   "RenderSystem does not support FixedFunction, but technique of
    #    'rigsofrods/WallpaperMat' has no Vertex Shader. Use the RTSS or
    #    write custom shaders."
    # which the main loop's catch turns into yet another silent black screen.
    exe = [n for n in names
           if n.startswith("Payload/") and n.split("/")[-1] == "RoR"]
    if exe:
        binary = ipa.read(exe[0])
        markers = (b"RTShader System up, active material scheme",
                   b"generating shaders for fixed-function material",
                   b"RTShader System FAILED to initialise",
                   b"ShaderGenerator::initialize() threw",
                   # The listener must use validateMaterial() (not
                   # invalidateMaterial()+validateScheme()) and must rebuild the
                   # supported-technique list when the technique build left the
                   # material needing a compile - otherwise Material::
                   # getBestTechnique() returns NULL and the ImGui overlay
                   # dereferences it.  See PORT_FINDINGS 7.30.
                   b"needed a recompile after the technique build")
        missing = [m for m in markers if m not in binary]
        if missing:
            bad.append(
                "the executable carries no %s - the RTShader System bootstrap "
                "(patch_rtshader) is not in this build, so every "
                "fixed-function material will throw from SceneManager::_setPass"
                % " / ".join(m.decode() for m in missing))
        else:
            print("  ok      executable: RTShader System bootstrap present")

    # The generator reads the FFP function library out of this pack when it
    # builds a shader, with ResourceGroupManager::openResource() - any group
    # will do, but the files have to exist.
    #
    # Existing is not enough: the library is version locked to the RTSS that
    # generates the shaders.  RoR ships the 2009 (OGRE 1.6/1.7) set while this
    # port links OGRE 1.11.6, and the sub-render-states call e.g.
    # SGX_FetchNormal with an out vec4 and SGX_Light_* with in vec4 vTSNormal.
    # OgreShaderGLSLESProgramWriter matches those calls against the signatures
    # parsed out of these files (FunctionInvocation::operator== compares name,
    # return type, operand count and every operand's semantic and type), so the
    # 2009 signatures are simply not found - "Error: Cached function not found
    # SGX_FetchNormal" - and the emitted shader calls a function it never
    # defines.  patch_rtshader_media() installs OGRE 1.11.6's own library; these
    # assertions are what keep that from silently regressing.
    rtsh = [n for n in names if n.endswith("/rorres/rtshader.zip")]
    if not rtsh:
        bad.append("rorres/rtshader.zip is missing - the RTShader System "
                   "cannot find FFPLib_*.glsl and shader generation fails")
    else:
        inner = zipfile.ZipFile(io.BytesIO(ipa.read(rtsh[0])))
        libs = [n for n in inner.namelist() if n.endswith(".glsl")]
        if not any(n.endswith("FFPLib_Transform.glsl") for n in libs):
            bad.append("rorres/rtshader.zip has no FFPLib_Transform.glsl - "
                       "the RTShader System parses it to build every shader")
        else:
            def _lib(suffix):
                hit = [n for n in libs if n.endswith(suffix)]
                return inner.read(hit[0]).decode("utf-8", "replace") if hit else ""

            def _norm(text):
                return " ".join(text.split())

            # Signatures the 1.11.6 generator calls and the 2009 file lacks /
            # declares with different types.
            normalmap = _norm(_lib("SGXLib_NormalMapLighting.glsl"))
            common = _lib("FFPLib_Common.glsl")
            fog = _lib("FFPLib_Fog.glsl")
            wrong = []
            if "void SGX_FetchNormal(in sampler2D s, in vec2 uv, out vec4 vOut)" \
                    not in normalmap:
                wrong.append("SGX_FetchNormal(in sampler2D, in vec2, out vec4)")
            if "void SGX_Light_Point_DiffuseSpecular( in vec4 vTSNormal," \
                    not in normalmap:
                wrong.append("SGX_Light_*(in vec4 vTSNormal)")
            if "FFP_Normalize" not in common:
                wrong.append("FFP_Normalize")
            if "FFP_PixelFog_PositionDepth" not in fog:
                wrong.append("FFP_PixelFog_PositionDepth")
            if wrong:
                bad.append(
                    "rorres/rtshader.zip carries the pre-1.11 RTShader System "
                    "library: %s not declared the way OGRE 1.11.6's "
                    "sub-render-states call it, so the generated shaders "
                    "reference undefined functions ('Error: Cached function "
                    "not found').  patch_rtshader_media() must install "
                    "patches/ror/rtshader_glsl/." % ", ".join(wrong))
            else:
                print("  ok      rorres/rtshader.zip: %d GLSL FFP library files,"
                      " signatures match OGRE 1.11.6" % len(libs))

    # OGRE's StdQuad_vp.program asks for four GLSL ES sources that upstream
    # never wrote (nothing selects a GLSLES delegate on desktop).  Without
    # them every run logs FileNotFoundException + InvalidParametersException.
    ogrecore = [n for n in names if n.endswith("/rorres/OgreCore.zip")]
    if not ogrecore:
        bad.append("rorres/OgreCore.zip is missing")
    else:
        inner = zipfile.ZipFile(io.BytesIO(ipa.read(ogrecore[0])))
        have = set(inner.namelist())
        want = ("StdQuad_Tex2_vp.glsles", "StdQuad_Tex2a_vp.glsles",
                "StdQuad_Tex3_vp.glsles", "StdQuad_Tex4_vp.glsles")
        absent = [w for w in want if w not in have]
        if absent:
            bad.append(
                "rorres/OgreCore.zip is missing %s - StdQuad_vp.program "
                "references them and OGRE reports FileNotFoundException + "
                "'Named constants have not been initialised' on every run"
                % ", ".join(absent))
        else:
            print("  ok      rorres/OgreCore.zip: all five StdQuad_vp.glsles present")

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
