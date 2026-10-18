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

    # ------------------------------------------------------------------ #
    # 5. FetchIcon() must be able to find the images it is asked for.      #
    # ------------------------------------------------------------------ #
    # FetchIcon() (GUIUtils.cpp) is the one entry point for every small PNG
    # the ImGui panels draw - the vehicle dashboard, the survey map, the
    # repository selector, the multiplayer flags and the main menu's notice
    # box.  Upstream asks for all of them in the group "FlagsRG" (that is
    # resources/flags.zip, the country flags) and returns an *empty*
    # Ogre::TexturePtr when the lookup throws, while all ~30 callers do
    # tex->getHandle() with no null check.
    #
    # OGRE cannot paper over the wrong group either: Texture::readImage()
    # opens the image with openResource(name, mGroup, this), the overload that
    # passes searchGroupsIfNotFound=false, so only that single group is ever
    # searched.  On the first launch after a mod-cache rebuild the main menu
    # draws its "Cache updated" notice box, which asks for accept.png - an
    # icon, not a flag - and the app died on every fresh install with
    #
    #   FileNotFoundException: Cannot locate resource accept.png in resource
    #       group FlagsRG.                       (OgreResourceGroupManager.cpp)
    #   [crash] SIGSEGV addr=0x0
    #       -> RoR::GUI::GameMainMenu::DrawNoticeBox() +0x2f4
    #
    # while the second launch, reading a valid cache, never draws the notice
    # and runs fine.  patch_fetchicon() makes FetchIcon() ask OGRE which
    # mounted group owns the file and hand back the warning texture instead of
    # null; these assertions keep both halves of that from regressing.
    if exe:
        marker = b"[RoR|FetchIcon]"
        if marker not in binary:
            bad.append(
                "the executable carries no %r - patch_fetchicon() is not in "
                "this build, so FetchIcon() still asks for every image in "
                "FlagsRG and returns a null texture on a miss, and the first "
                "launch after a mod-cache rebuild crashes in "
                "GameMainMenu::DrawNoticeBox()" % marker.decode())
        else:
            print("  ok      executable: FetchIcon() group fallback present")

    # The names FetchIcon() is asked for, and the packs they live in.  Two of
    # them - control_pause.png / control_play.png, drawn by the multiplayer
    # actor list - are in neither icons.zip nor flags.zip but in famicons.zip,
    # and the multiplayer flag names resolve out of flags.zip.
    def _pack(name):
        hit = [n for n in names if n.endswith("/rorres/%s" % name)]
        return zipfile.ZipFile(io.BytesIO(ipa.read(hit[0]))) if hit else None

    for pack, want in (("icons.zip", ("accept.png", "error.png", "cancel.png")),
                       ("famicons.zip", ("control_pause.png", "control_play.png")),
                       ("flags.zip", ("de.png", "us.png"))):
        inner = _pack(pack)
        if inner is None:
            bad.append("rorres/%s is missing - FetchIcon() asks for images "
                       "from it" % pack)
            continue
        have = set(inner.namelist())
        absent = [w for w in want if w not in have]
        if absent:
            bad.append("rorres/%s is missing %s - FetchIcon() asks for it"
                       % (pack, ", ".join(absent)))
        else:
            print("  ok      rorres/%s: %s" % (pack, ", ".join(want)))

    # ------------------------------------------------------------------ #
    # 6. Touch must reach ImGui, not only the camera.                      #
    # ------------------------------------------------------------------ #
    # OIS's iPhone backend has no mouse and no keyboard: it offers only the
    # accelerometer and OISMultiTouch (iPhoneInputManager::freeDeviceList), so
    # RoR's own try/catch leaves InputEngine::mMouse and mKeyboard null, and
    # InputEngine is made a OIS::MultiTouchListener that translates a touch
    # into the mouse events RoR already handles.
    #
    # The translation has one non-obvious requirement.  ImGui learns the
    # pointer position *only* from a move event:
    #
    #   InputEngine touchMoved -> mMouseListener->mouseMoved()
    #     -> AppContext::mouseMoved()          (AppContext.cpp:80, sole caller)
    #       -> OgreImGui::InjectMouseMoved()   -> io.MousePos
    #
    # and OIS's iPhone backend sends touchesBegan *without* a preceding
    # touchesMoved (its _touchBegan emits MT_Pressed alone,
    # src/iphone/iPhoneMultiTouch.mm).  While the position was published only
    # for MT_Moved, io.MousePos kept its initial (-FLT_MAX,-FLT_MAX), ImGui
    # never hovered a widget, and a plain tap did nothing: the touch was
    # offered to the camera instead of the menu behind it.  IosHandleTouch now
    # publishes the position for every action - with a zeroed delta, so a press
    # is not read as a flick - before dispatching the button.
    if exe:
        marker = b"[RoR|Input] first touch arrived"
        if marker not in binary:
            bad.append(
                "the executable carries no %r - the touch translation in "
                "InputEngine.cpp is not in this build, so a tap cannot hover "
                "an ImGui widget and the menus do not respond to touch"
                % marker.decode())
        else:
            print("  ok      executable: touch -> mouse -> ImGui bridge present")

    # The ImGui overlay draws its own command lists - each command needs its own
    # scissor rect and texture, so preRender() does the drawing itself and
    # returns false to stop SceneManager::_issueRenderOp() from drawing it a
    # second time.  That same false return, however, is also what stops OGRE
    # from calling updateGpuProgramParameters() for this renderable - the only
    # caller of GpuProgramParameters::_updateAutoParams() and
    # RenderSystem::bindGpuProgramParameters() in the whole engine.
    #
    # That is harmless on the desktop (RoR's ImGui material has no shader there,
    # so the matrices reach the GPU through the fixed-function world matrix
    # SceneManager::setWorldTransform() sets) and fatal on GLES2, where there is
    # no fixed function and the RTSS emulates it: the generated vertex shader
    # kept the auto parameters of whichever renderable had been drawn before it
    # - an overlay element of the wallpaper or the loading screen, all of which
    # have identity view, identity projection *and* identity world matrix,
    # because OGRE's overlay geometry is already in clip space.  ImGui's
    # vertices are in *pixels* (its mXform is the projection that maps them),
    # so under an identity transform a pixel coordinate is a clip coordinate and
    # the entire menu - x in [0, 844] - collapsed into the one-pixel-wide sliver
    # x in [0, 1] at the left edge.  This is why the device trace showed the
    # menu state, three command lists and 904 vertices every frame, with
    # hit-testing working and nothing on screen.
    #
    # patch_ogreimgui_overlay() refreshes those parameters inside preRender(),
    # with public OGRE entry points only (no OGRE patch and no OGRE rebuild), so
    # this marker means the menu is uploaded with worldViewProj == mXform.
    if exe:
        marker = b"[RoR|GUI] ImGui overlay: refreshed the shader auto params"
        if marker not in binary:
            bad.append(
                "the executable carries no %r - the ImGui overlay is not "
                "refreshing its shader auto parameters (patch_ogreimgui_overlay),"
                " so on GLES2 the menu stays invisible: its pixel coordinates "
                "are transformed by a stale identity worldViewProj"
                % marker.decode())
        else:
            print("  ok      executable: ImGui overlay shader-param refresh present")

    # ------------------------------------------------------------------ #
    # 7. There must be something to load: the base content.                #
    # ------------------------------------------------------------------ #
    # Rigs of Rods ships no built-in terrain.  Its only content is the
    # `content` submodule (RigsOfRods/content), which CMake packs one zip per
    # mod folder into bin/content when ROR_CREATE_CONTENT_FOLDER is ON, and
    # which the packaging step copies to RoR.app/content.  RoR then mounts
    # that directory itself:
    #
    #   ContentManager.cpp:295
    #     addResourceLocation(<process_dir>/content, "FileSystem", RGN_CONTENT)
    #   CacheSystem.cpp:173 -> ParseZipArchives(RGN_CONTENT)
    #     findResourceFileInfo(RGN_CONTENT, "*.zip") -> ParseSingleZip()
    #       addResourceLocation(<the zip>, "Zip", RGN_TEMP)
    #
    # and ListAllUserContent() fills the terrain/vehicle lists from
    # RGN_CONTENT by extension (.terrn2 .truck .trailer .otc .os ...).
    #
    # Without this the app reaches the main menu and then offers an empty
    # terrain list - nothing to load, nothing to drive, nothing to test.  That
    # failure is invisible to the compiler, so it is checked here.
    #
    # The textures in these zips are DXT-compressed .dds, which the device
    # cannot sample directly (RoR.log: "Texture Compression: yes" but
    # "- DXT: no", and GL_EXTENSIONS has no S3TC - only GL_IMG/pvrtc).  That is
    # not a problem: OgreDDSCodec::decode() sees
    # !RSC_TEXTURE_COMPRESSION_DXT and decompresses DXT1/3/5 to
    # PF_BYTE_RGB(A) in software (OgreDDSCodec.cpp:836-890), so the .dds stay
    # as they are and no material reference has to be rewritten.
    top_dirs = {n.split("/")[2] for n in names
                if n.startswith("Payload/") and len(n.split("/")) > 2}
    if "content" not in top_dirs:
        bad.append(
            "the bundle has no top-level content/ directory - RoR would start "
            "with an empty terrain list and no vehicle to load (nothing to test "
            "past the main menu); the content submodule was probably not "
            "checked out or the CMake flag is still OFF")
    else:
        print("  ok      bundle: content/ present")
        want = {"simple2-terrain.zip": ".terrn2",
                "agora.zip": ".truck",
                "dafsemi.zip": ".truck"}
        for zipname, ext in sorted(want.items()):
            hit = [n for n in names if n.endswith("/content/" + zipname)]
            if not hit:
                bad.append("content/%s is missing - RoR's cache mounts every "
                           "zip in RGN_CONTENT, so this mod would simply not "
                           "exist in the game" % zipname)
                continue
            inner = zipfile.ZipFile(io.BytesIO(ipa.read(hit[0])))
            members = inner.namelist()
            found = [m for m in members if m.endswith(ext)]
            if not found:
                bad.append("content/%s carries no %s - the mod would be listed "
                           "as 'no usable content' on the device"
                           % (zipname, ext))
            else:
                print("  ok      content/%-20s %3d entries, %d x %s  (%s)"
                      % (zipname, len(members), len(found), ext,
                         ", ".join(sorted(found)[:3])))

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
