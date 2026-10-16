#!/usr/bin/env python3
"""Adapt a fresh Rigs of Rods checkout for the Apple/iOS port probe.

Why this exists
---------------
RoR's dependency ``ois/1.4.1@rigsofrods/custom`` cannot be built by its conan
recipe on modern Apple clang (the macOS backend uses ``std::unary_function``,
removed in C++17) and the recipe has no iOS branch (it falls into the Cocoa
backend even when targeting iPhone, where AppKit does not exist).

The OIS source itself *does* have an iPhone backend (``src/iphone/*.mm``), it is
just selected by the CMake variable ``OGRE_BUILD_PLATFORM_APPLE_IOS``. So we
build OIS ourselves (see ``.github/workflows/probe-ois-ror.yml``) and splice it
into RoR's build, dropping the conan package.

Usage
-----
    apply_ios_patches.py <ror_dir> \
        --ois-include <dir containing OIS.h> \
        --ois-lib <path to libOIS.a>
"""
import argparse
import os
import sys


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def must_replace(text, old, new, what):
    if old not in text:
        sys.exit("ERROR: expected source text not found: %s" % what)
    return text.replace(old, new, 1)


def patch_conanfile(ror, minimal_deps=False):
    path = os.path.join(ror, "conanfile.py")
    text = read(path)
    text = must_replace(
        text,
        '        self.requires("ois/1.4.1@rigsofrods/custom")\n',
        '        # Apple/iOS port: OIS is built from source, not fetched by conan.\n',
        "conanfile.py ois requirement",
    )
    if minimal_deps:
        # Stage-1 probe: keep only what RoR marks REQUIRED (OGRE, MyGUI, fmt,
        # RapidJSON) and drop the optional ones. Their features are behind
        # USE_* macros and simply switch off, which keeps the first full Apple
        # build small enough to actually finish.
        for dep in (
            "angelscript",
            "discord-rpc",
            "libcurl",
            "ogre3d-caelum",
            "ogre3d-pagedgeometry",
            "openal-soft",
            "openssl",
            "socketw",
        ):
            line = '        self.requires("%s' % dep
            lines = text.splitlines(True)
            kept = [l for l in lines if not l.startswith(line)]
            if len(kept) == len(lines):
                sys.exit("ERROR: minimal-deps: no requires line starting with %r" % line)
            text = "".join(kept)
    write(path, text)


def patch_dependencies(ror):
    path = os.path.join(ror, "cmake", "DependenciesConfig.cmake")
    text = read(path)
    text = must_replace(
        text,
        "# --- Object Oriented Input System ---\n"
        "find_package(OIS REQUIRED)\n",
        "# --- Object Oriented Input System ---\n"
        "# Apple/iOS port: use an OIS built from source when asked to.\n"
        "if (ROR_OIS_INCLUDE_DIR)\n"
        "    message(STATUS \"OIS: using from-source build (${ROR_OIS_INCLUDE_DIR})\")\n"
        "else ()\n"
        "    find_package(OIS REQUIRED)\n"
        "endif ()\n",
        "DependenciesConfig.cmake find_package(OIS)",
    )
    write(path, text)


def patch_main_cmake(ror):
    path = os.path.join(ror, "source", "main", "CMakeLists.txt")
    text = read(path)

    # OS_LIBS: Windows wants ws2_32, Linux wants X11 + libdl + librt.  iOS has
    # none of those: libdl/librt/lpthread do not exist as separate libraries and
    # X11 is obviously absent.  Sockets, dlopen and threads all live in
    # libSystem, which every app links anyway.
    #
    # What iOS *does* need here is the frameworks.  The build runs a plain
    # `make`, so nothing else on the command line adds them, and the first
    # link attempt came back with exactly 38 undefined symbols, every one of
    # them accounted for:
    #
    #   AudioToolbox/CoreAudio (11) AudioComponent*, AudioUnit*,
    #                               AudioOutputUnit* - openal's CoreAudio backend
    #   CoreFoundation (8)          macUtils.o (OGRE) + openal's alconfig.cpp
    #   Foundation (5)              OgreFileSystemLayer/macUtils (OGRE)
    #   UIKit (6)                   the OIS iPhone backend
    #   libobjc (7)                 objc_msgSend, objc_release_x8, ...
    #   CallSystemFunctionNative    libangelscript.a - not a framework problem,
    #                               see the ARCHFLAGS patch in build-ios-deps.yml
    #
    # -lobjc matters because the ObjC runtime is only linked automatically when
    # the driver is compiling a .mm file; RoR's own translation units are all
    # plain C++, so it has to be asked for explicitly.  OpenGLES/QuartzCore are
    # not among the 38 but are what the GLES2 render system and the frame loop
    # will need as soon as the static OGRE plugins are linked in.
    text = must_replace(
        text,
        "if (WIN32)\n"
        "    set(OS_LIBS \"Ws2_32\")\n"
        "else ()\n",
        "if (WIN32)\n"
        "    set(OS_LIBS \"Ws2_32\")\n"
        "elseif (APPLE AND CMAKE_SYSTEM_NAME STREQUAL \"iOS\")\n"
        "    set(OS_LIBS \"-framework UIKit -framework Foundation -framework CoreFoundation\"\n"
        "                 \" -framework CoreGraphics -framework QuartzCore -framework OpenGLES\"\n"
        "                 \" -framework AudioToolbox -framework CoreAudio -lobjc\")\n"
        "else ()\n",
        "main/CMakeLists.txt OS_LIBS (iOS branch)",
    )

    text = must_replace(
        text,
        "        ${OGRE_LIBRARIES}\n        ois::ois\n        fmt::fmt\n",
        "        ${OGRE_LIBRARIES}\n        fmt::fmt\n",
        "main/CMakeLists.txt ois::ois link",
    )
    text = must_replace(
        text,
        "target_include_directories(${BINNAME} PRIVATE ${OGRE_INCLUDE_DIRS} ${RapidJSON_INCLUDE_DIRS})\n",
        "target_include_directories(${BINNAME} PRIVATE ${OGRE_INCLUDE_DIRS} ${RapidJSON_INCLUDE_DIRS})\n"
        "if (ROR_OIS_INCLUDE_DIR)\n"
        "    target_include_directories(${BINNAME} PRIVATE ${ROR_OIS_INCLUDE_DIR})\n"
        "endif ()\n"
        "if (ROR_OIS_LIBRARY)\n"
        "    target_link_libraries(${BINNAME} PRIVATE ${ROR_OIS_LIBRARY})\n"
        "endif ()\n"
        "\n"
        "# Apple/iOS port: the platform layer written by patch_ios_platform().\n"
        "# ios_main.mm owns the process entry point (UIApplicationMain never\n"
        "# returns, so RoR's main cannot be it) and calls RoRMain() from the app\n"
        "# delegate; ios_platform.mm services the UIKit run loop once per frame.\n"
        "# ROR_PLATFORM_IOS is the switch the patched sources use, because\n"
        "# OGRE_PLATFORM_APPLE_IOS is not reliably defined before OgrePlatform.h\n"
        "# has been included (an undefined OGRE_PLATFORM makes the comparison\n"
        "# accidentally true).\n"
        "if (APPLE AND CMAKE_SYSTEM_NAME STREQUAL \"iOS\")\n"
        "    target_compile_definitions(${BINNAME} PRIVATE ROR_PLATFORM_IOS)\n"
        "    target_include_directories(${BINNAME} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/ios)\n"
        "    target_sources(${BINNAME} PRIVATE ios/ios_main.mm ios/ios_platform.mm)\n"
        "endif ()\n",
        "main/CMakeLists.txt OIS include/link + iOS platform layer",
    )
    write(path, text)


def patch_errorutils(ror):
    """ErrorUtils.cpp: `_L` must exist on iOS too.

    The file only defines `_L` for WIN32 (as an empty macro) and APPLE, and
    includes Language.h for LINUX.  OGRE_PLATFORM on iOS is
    OGRE_PLATFORM_APPLE_IOS, which is NOT OGRE_PLATFORM_APPLE, so on iOS none
    of the branches is taken and `_L` stays undefined - while ShowError() on
    lines 49-50 calls it.  Result: a plain compile error.

    iOS gets the APPLE treatment (empty macro, no translation lookup), which
    is what ErrorUtils wants anyway: it must be able to show an error even
    when the language file failed to load.
    """
    path = os.path.join(ror, "source", "main", "utils", "ErrorUtils.cpp")
    text = read(path)
    text = must_replace(
        text,
        '#include "ErrorUtils.h"\n'
        '#include "Utils.h"\n',
        '#include "ErrorUtils.h"\n'
        '#include "Utils.h"\n'
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "ios_platform.h"\n'
        "#endif\n",
        "ErrorUtils.cpp iOS include",
    )
    # On iOS ShowMsgBox() has no dialog at all and only printf()s, which lands
    # in the device log where we cannot see it.  Everything that reports an
    # error therefore also goes into the trace file.
    text = must_replace(
        text,
        "int ErrorUtils::ShowMsgBox(const std::string& title, const std::string& err, int type)\n"
        "{\n",
        "int ErrorUtils::ShowMsgBox(const std::string& title, const std::string& err, int type)\n"
        "{\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: see below - there is no dialog on iOS.\n"
        '    RoR_IosTrace("[error] %s: %s", title.c_str(), err.c_str());\n'
        "#endif\n",
        "ErrorUtils.cpp iOS error trace",
    )
    text = must_replace(
        text,
        "#elif OGRE_PLATFORM == OGRE_PLATFORM_APPLE\n#define _L\n",
        "#elif OGRE_PLATFORM == OGRE_PLATFORM_APPLE || OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS\n"
        "#define _L\n",
        "ErrorUtils.cpp _L for iOS",
    )
    # ShowMsgBox() additionally has a per-platform body; on iOS it would be
    # empty, so route it through the APPLE branch to at least get printf(),
    # which lands in the device log.
    text = must_replace(
        text,
        "#elif OGRE_PLATFORM == OGRE_PLATFORM_APPLE\n"
        '\tprintf("\\n\\n%s: %s\\n\\n", title.c_str(), err.c_str());\n',
        "#elif OGRE_PLATFORM == OGRE_PLATFORM_APPLE || OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS\n"
        '\tprintf("\\n\\n%s: %s\\n\\n", title.c_str(), err.c_str());\n',
        "ErrorUtils.cpp ShowMsgBox for iOS",
    )
    write(path, text)


def patch_platformutils(ror):
    """PlatformUtils.cpp: the POSIX branch uses /proc/self/exe, which iOS has not.

    Without this, GetExecutablePath() returns an empty string, and
    AppContext::SetUpProgramPaths() answers with "Error while retrieving
    program directory path" and returns false - i.e. the engine refuses to
    start at all.

    Darwin has _NSGetExecutablePath() (mach-o/dyld.h) for exactly this; on
    iOS it yields <bundle>.app/<executable>, so the existing
    GetParentDirectory() call then gives the app bundle directory, which is
    where RoR expects `resources/` and `content/` to sit.
    """
    path = os.path.join(ror, "source", "main", "utils", "PlatformUtils.cpp")
    text = read(path)
    # OGRE_PLATFORM is not usable in the include block (OgrePlatform.h is
    # included further down, and an undefined OGRE_PLATFORM would make
    # `#if OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS` accidentally true).
    # __APPLE__ is defined by the compiler itself, so it is safe there.
    text = must_replace(
        text,
        "#else\n"
        "    #include <sys/types.h>\n"
        "    #include <sys/stat.h>\n"
        "    #include <unistd.h> // readlink()\n"
        "#endif\n",
        "#else\n"
        "    #include <sys/types.h>\n"
        "    #include <sys/stat.h>\n"
        "    #include <unistd.h> // readlink()\n"
        "    #if defined(__APPLE__)\n"
        "        // Apple/iOS port: _NSGetExecutablePath(), see GetExecutablePath()\n"
        "        #include <mach-o/dyld.h>\n"
        "    #endif\n"
        "#endif\n",
        "PlatformUtils.cpp darwin include",
    )
    # Here OgrePlatform.h has already been included, so OGRE_PLATFORM is safe.
    text = must_replace(
        text,
        "std::string GetExecutablePath()\n"
        "{\n"
        "    const int BUF_SIZE = 500;\n"
        "    std::string buf_str(BUF_SIZE, 0);\n"
        "    // Linux or POSIX assumed; http://stackoverflow.com/a/625523\n"
        '    if (readlink("/proc/self/exe", &buf_str[0], BUF_SIZE-1) == -1)\n',
        "std::string GetExecutablePath()\n"
        "{\n"
        "#if OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS\n"
        "    // iOS has no /proc.  _NSGetExecutablePath() needs a two-pass call:\n"
        "    // once to learn the size, once to fill the buffer.\n"
        "    uint32_t size = 0;\n"
        "    _NSGetExecutablePath(nullptr, &size);\n"
        "    std::string buf_str(size, 0);\n"
        "    if (_NSGetExecutablePath(&buf_str[0], &size) != 0)\n"
        "    {\n"
        '        RoR::Log("[RoR] Internal error: GetExecutablePath() failed; _NSGetExecutablePath() reported failure");\n'
        "        return std::string();\n"
        "    }\n"
        "    buf_str.resize(strlen(buf_str.c_str())); // may be padded with NULs\n"
        "    return buf_str;\n"
        "#else\n"
        "    const int BUF_SIZE = 500;\n"
        "    std::string buf_str(BUF_SIZE, 0);\n"
        "    // Linux or POSIX assumed; http://stackoverflow.com/a/625523\n"
        '    if (readlink("/proc/self/exe", &buf_str[0], BUF_SIZE-1) == -1)\n',
        "PlatformUtils.cpp GetExecutablePath() for iOS",
    )
    text = must_replace(
        text,
        "        RoR::LogFormat(\"[RoR] Internal error: GetExecutablePath() failed; readlink() sets errno to %d\", static_cast<int>(errno));\n"
        "        return std::string();\n"
        "    }\n"
        "\n"
        "    return std::move(buf_str);\n"
        "}\n",
        "        RoR::LogFormat(\"[RoR] Internal error: GetExecutablePath() failed; readlink() sets errno to %d\", static_cast<int>(errno));\n"
        "        return std::string();\n"
        "    }\n"
        "\n"
        "    return std::move(buf_str);\n"
        "#endif // OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS\n"
        "}\n",
        "PlatformUtils.cpp GetExecutablePath() iOS #endif",
    )
    # system() is not merely a no-op on iOS, it is marked unavailable, so
    # `::system(...)` does not even compile there.  There is no xdg-open
    # either.  RoR only calls this for "open our website/discord" buttons, so
    # on iOS we log and carry on (doing it properly needs UIApplication, which
    # belongs to the platform layer, not to this POSIX helper).
    text = must_replace(
        text,
        "void OpenUrlInDefaultBrowser(std::string const& url)\n"
        "{\n"
        '    std::string buf = "xdg-open " + url;\n'
        "    ::system(buf.c_str());\n"
        "}\n",
        "void OpenUrlInDefaultBrowser(std::string const& url)\n"
        "{\n"
        "#if OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS\n"
        "    // Apple/iOS port: system() is marked unavailable on iOS and there\n"
        "    // is no xdg-open to call anyway.\n"
        "    RoR::LogFormat(\"[RoR] OpenUrlInDefaultBrowser() is not implemented on iOS; ignored URL '%s'\", url.c_str());\n"
        "#else\n"
        '    std::string buf = "xdg-open " + url;\n'
        "    ::system(buf.c_str());\n"
        "#endif\n"
        "}\n",
        "PlatformUtils.cpp OpenUrlInDefaultBrowser() for iOS",
    )
    write(path, text)


def patch_appcontext(ror):
    """AppContext.cpp: the per-platform user directory has no iOS branch.

    SetUpProgramPaths() switches on WIN32 / LINUX / APPLE; on iOS none of
    them matches, so `ror_homedir` stays empty, and sys_user_dir (config,
    logs, mods, cache) becomes the empty string - every user path breaks.

    iOS uses $HOME/Documents/RigsOfRods: $HOME is the app's data container
    (getenv("HOME") works there) and Documents is the folder exposed to the
    Files app by UIFileSharingEnabled in the Xcode project, so mods can be
    dropped in without a Mac.
    """
    path = os.path.join(ror, "source", "main", "AppContext.cpp")
    text = read(path)
    text = must_replace(
        text,
        "        RoR::Str<500> ror_homedir;\n"
        "#if OGRE_PLATFORM == OGRE_PLATFORM_WIN32\n",
        "        RoR::Str<500> ror_homedir;\n"
        "#if OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS\n"
        "        ror_homedir << user_home << PATH_SLASH << \"Documents\" << PATH_SLASH << \"RigsOfRods\";\n"
        "#elif OGRE_PLATFORM == OGRE_PLATFORM_WIN32\n",
        "AppContext.cpp user dir for iOS",
    )

    # ---------------------------------------------------------------------
    # Rendering on iOS
    # ---------------------------------------------------------------------
    text = must_replace(
        text,
        '#include "InputEngine.h"\n'
        '#include "Language.h"\n',
        '#include "InputEngine.h"\n'
        '#include "Language.h"\n'
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "ios_platform.h"\n'
        "#   include <Bites/OgreStaticPluginLoader.h>\n"
        "#endif\n",
        "AppContext.cpp iOS includes",
    )

    # The OGRE we build for iOS is static, so there are no plugin .dylib files
    # for loadPlugin() to dlopen: the whole plugin set is linked into the binary
    # and has to be registered through OgreBites::StaticPluginLoader, whose
    # plugin list was compiled in from OgreBuildSettings.h.  plugins.cfg is not
    # generated into the app bundle either (CMake writes it next to bin/RoR,
    # whereas sys_process_dir is bin/RoR.app), so reading it could only fail.
    text = must_replace(
        text,
        "    try\n"
        "    {\n"
        "        Ogre::ConfigFile cfg;\n"
        "        cfg.load(plugins_path);\n",
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: register the statically linked OGRE plugins.\n"
        "    {\n"
        '        LOG("[RoR|Startup|Rendering] iOS: registering statically linked OGRE plugins");\n'
        "        static OgreBites::StaticPluginLoader s_static_plugins;\n"
        "        s_static_plugins.load();\n"
        "    }\n"
        "#else\n"
        "    try\n"
        "    {\n"
        "        Ogre::ConfigFile cfg;\n"
        "        cfg.load(plugins_path);\n",
        "AppContext.cpp static OGRE plugins for iOS",
    )
    text = must_replace(
        text,
        "        return false;\n"
        "    }\n"
        "\n"
        "    // Load renderer configuration\n",
        "        return false;\n"
        "    }\n"
        "#endif // ROR_PLATFORM_IOS\n"
        "\n"
        "    // Load renderer configuration\n",
        "AppContext.cpp static OGRE plugins #endif",
    )

    # On iOS the GLES2 render system has no usable "Video Mode" config option,
    # so the parse above yields 0x0, the 800x600 clamp below rewrites it, and
    # the "auto-detected" loop that follows tries setConfigOption("Video Mode")
    # outside any try/catch.  Skip the loop and take the real screen size in
    # points instead: OGRE's EAGL2Window builds its UIWindow at exactly the size
    # passed to createRenderWindow().
    text = must_replace(
        text,
        "    if (autodetect_resolution)\n"
        "    {\n",
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: the mode list is empty or unrelated to the device\n"
        "    // on iOS, and setConfigOption() is not wrapped in a try/catch here.\n"
        "    if (false)\n"
        "#else\n"
        "    if (autodetect_resolution)\n"
        "#endif\n"
        "    {\n",
        "AppContext.cpp skip the iOS video mode autodetect",
    )
    text = must_replace(
        text,
        '        LOG(fmt::format("[RoR|Startup|Rendering] WARNING - invalid \'ogre.cfg\', auto-detected resolution {}x{}", width, height));\n'
        "        m_ogre_root->saveConfig();\n"
        "    }\n"
        "\n"
        "    // Review render window settings\n",
        '        LOG(fmt::format("[RoR|Startup|Rendering] WARNING - invalid \'ogre.cfg\', auto-detected resolution {}x{}", width, height));\n'
        "        m_ogre_root->saveConfig();\n"
        "    }\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: use the real screen size in points.  UIScreen\n"
        "    // reports it in the portrait frame, and RoR is landscape-only, so the\n"
        "    // long side is the width (see RoR_IosGetScreenSize()).\n"
        "    RoR_IosGetScreenSize((int*)&width, (int*)&height);\n"
        '    LOG(fmt::format("[RoR|Startup|Rendering] iOS: using screen size {}x{}", width, height));\n'
        "#endif\n"
        "\n"
        "    // Review render window settings\n",
        "AppContext.cpp iOS screen size instead of video modes",
    )

    # ---------------------------------------------------------------------
    # The resource directory is called rorres, not resources
    # ---------------------------------------------------------------------
    # An iOS .app whose bundle contains a top-level directory *named*
    # `resources` - or `Resources`, the check is case-insensitive - is refused
    # by the installer with the generic "Unable to Install / Please try again
    # later", while the very same directory under any other name (`zzz`,
    # `Base.lproj`, `rorres`) installs and runs.  Measured on device, see
    # PORT_FINDINGS.md section 7.22.
    #
    # What flips is codesign's resource rules: with such a directory present it
    # switches to the macOS "resource envelope" set (rules=^Resources/, and a
    # `^[^/]+$ -> nested` rule in rules2) which iOS will not install; with any
    # other name it keeps the normal `^.*` set that iOS accepts.
    #
    # RoR hard-codes the name in exactly four places; everything else resolves
    # through App::sys_resources_dir, so renaming here and in ContentManager.cpp
    # is enough.  The packaging step puts the archives in RoR.app/rorres.
    text = must_replace(
        text,
        '    std::string process_dir = PathCombine(App::sys_process_dir->getStr(), "resources");\n',
        "    // Apple/iOS port: `resources` is a directory name the iOS installer\n"
        "    // rejects outright, so the bundle ships its data in `rorres` instead.\n"
        '    std::string process_dir = PathCombine(App::sys_process_dir->getStr(), "rorres");\n',
        "AppContext.cpp resource directory name",
    )

    # ---------------------------------------------------------------------
    # Mirror OGRE's log into the crash-proof trace file
    # ---------------------------------------------------------------------
    # SetUpLogging() already hands the new log to the console.  On iOS the same
    # messages also go to Documents/ror-trace.log, which ios_platform.mm writes
    # with write(2) one line at a time.  OGRE's own log file is buffered, so
    # when the app dies during startup its last lines - the interesting ones -
    # never reach the disk.  The trace file is what the crash handlers append
    # their reason to as well, so everything lands in one place.
    text = must_replace(
        text,
        "#include <ctime>\n"
        "\n"
        "using namespace RoR;\n",
        "#include <ctime>\n"
        "\n"
        "using namespace RoR;\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "// Apple/iOS port: mirrors every OGRE log message into the crash-proof\n"
        "// trace file written by ios_platform.mm.\n"
        "class IosMirrorLogListener : public Ogre::LogListener\n"
        "{\n"
        "public:\n"
        "    void messageLogged(const Ogre::String& message, Ogre::LogMessageLevel lml,\n"
        "        bool maskDebug, const Ogre::String& logName, bool& skipThisMessage) override\n"
        "    {\n"
        "        (void)lml;\n"
        "        (void)maskDebug;\n"
        "        (void)logName;\n"
        "        (void)skipThisMessage;\n"
        '        RoR_IosTrace("%s", message.c_str());\n'
        "    }\n"
        "};\n"
        "#endif // ROR_PLATFORM_IOS\n",
        "AppContext.cpp iOS log mirror listener",
    )
    text = must_replace(
        text,
        "    rorlog->addListener(App::GetConsole());  // Allow console to intercept log messages\n"
        "}\n",
        "    rorlog->addListener(App::GetConsole());  // Allow console to intercept log messages\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: also mirror into Documents/ror-trace.log, which is\n"
        "    // flushed line by line and therefore survives a crash.\n"
        "    rorlog->addListener(new IosMirrorLogListener());\n"
        "#endif\n"
        "}\n",
        "AppContext.cpp iOS log mirror registration",
    )
    # ---------------------------------------------------------------------
    # Breadcrumbs inside SetUpConfigSkeleton()
    # ---------------------------------------------------------------------
    # This function is where RoR first uses a Zip archive: it adds
    # <bundle>/rorres/skeleton.zip to resource group "SrcRG", copies every file
    # out of it into the user directory and then drops both groups again.  A
    # failure anywhere in here leaves the app dead before any other output, so
    # each step gets its own line in the trace file.
    text = must_replace(
        text,
        "    Ogre::String src_path = PathCombine(App::sys_resources_dir->getStr(), \"skeleton.zip\");\n",
        "#ifdef ROR_PLATFORM_IOS\n"
        '    RoR_IosStartupTick("SetUpConfigSkeleton: entering");\n'
        "#endif\n"
        "    Ogre::String src_path = PathCombine(App::sys_resources_dir->getStr(), \"skeleton.zip\");\n",
        "AppContext.cpp SetUpConfigSkeleton entry breadcrumb",
    )
    text = must_replace(
        text,
        "    Ogre::ResourceGroupManager::getSingleton().addResourceLocation(src_path, \"Zip\", \"SrcRG\");\n",
        "    Ogre::ResourceGroupManager::getSingleton().addResourceLocation(src_path, \"Zip\", \"SrcRG\");\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '    RoR_IosStartupTick("SetUpConfigSkeleton: skeleton.zip opened as a Zip archive");\n'
        "#endif\n",
        "AppContext.cpp SetUpConfigSkeleton zip breadcrumb",
    )
    text = must_replace(
        text,
        "    Ogre::ResourceGroupManager::getSingleton().addResourceLocation(dst_path, \"FileSystem\", \"DstRG\", false, false);\n",
        "    Ogre::ResourceGroupManager::getSingleton().addResourceLocation(dst_path, \"FileSystem\", \"DstRG\", false, false);\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '    RoR_IosStartupTick("SetUpConfigSkeleton: user directory mounted as DstRG");\n'
        "#endif\n",
        "AppContext.cpp SetUpConfigSkeleton file system breadcrumb",
    )
    text = must_replace(
        text,
        "    Ogre::ResourceGroupManager::getSingleton().destroyResourceGroup(\"SrcRG\");\n"
        "    Ogre::ResourceGroupManager::getSingleton().destroyResourceGroup(\"DstRG\");\n"
        "\n"
        "    return true;\n",
        "    Ogre::ResourceGroupManager::getSingleton().destroyResourceGroup(\"SrcRG\");\n"
        "    Ogre::ResourceGroupManager::getSingleton().destroyResourceGroup(\"DstRG\");\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '    RoR_IosStartupTick("SetUpConfigSkeleton: finished");\n'
        "#endif\n"
        "\n"
        "    return true;\n",
        "AppContext.cpp SetUpConfigSkeleton exit breadcrumb",
    )

    write(path, text)


def patch_contentmanager(ror):
    """ContentManager.cpp: three resource paths spell out `resources` themselves.

    Every other resource path is built from App::sys_resources_dir, which
    patch_appcontext() already points at `rorres`.  These three combine
    sys_process_dir with their own literal, so they need the same rename - see
    the note on the iOS installer's rejection of a `resources` directory in
    patch_appcontext().
    """
    path = os.path.join(ror, "source", "main", "resources", "ContentManager.cpp")
    text = read(path)
    for var, name in (("objects", "beamobjects.zip"),
                      ("dashboards", "dashboards.zip"),
                      ("gadgets", "gadgets.zip")):
        text = must_replace(
            text,
            '    std::string %s = PathCombine("resources", "%s");\n' % (var, name),
            '    // Apple/iOS port: see the resource directory note in AppContext.cpp.\n'
            '    std::string %s = PathCombine("rorres", "%s");\n' % (var, name),
            "ContentManager.cpp resource directory name (%s)" % name,
        )
    write(path, text)


def patch_soundmanager(ror):
    """SoundManager.h: the Apple OpenAL layout does not exist on iOS.

    The header does `#ifdef __APPLE__` and then includes <OpenAL/al.h> & co.
    That is right for macOS (the OpenAL.framework headers) but wrong for iOS:
    __APPLE__ is defined there as well, while openal-soft - the only OpenAL
    available on iOS and the one our SDK ships - installs the standard
    <AL/*.h> layout.  Result: "'OpenAL/alext.h' file not found" in every one
    of the ~106 translation units that pull in the sound manager.

    pch.h already includes <AL/al.h>/<AL/alc.h> unconditionally, so switching
    iOS to the AL/ layout also makes the two consistent.
    """
    path = os.path.join(ror, "source", "main", "audio", "SoundManager.h")
    text = read(path)
    text = must_replace(
        text,
        "#ifdef __APPLE__\n"
        "  #include <OpenAL/al.h>\n"
        "  #include <OpenAL/alc.h>\n"
        "  #include <OpenAL/alext.h>\n"
        "  #include <OpenAL/efx-presets.h>\n"
        "#else\n"
        "  #include <AL/al.h>\n"
        "  #include <AL/alc.h>\n"
        "  #include <AL/alext.h>\n"
        "  #include <AL/efx-presets.h>\n"
        "#endif // __APPLE__\n",
        "#ifdef __APPLE__\n"
        "  // Apple/iOS port: on iOS this is not the macOS OpenAL.framework but\n"
        "  // openal-soft, which uses the standard <AL/*.h> layout (just like\n"
        "  // pch.h already does unconditionally).\n"
        "  #include <TargetConditionals.h>\n"
        "#endif\n"
        "#if defined(__APPLE__) && !TARGET_OS_IPHONE\n"
        "  #include <OpenAL/al.h>\n"
        "  #include <OpenAL/alc.h>\n"
        "  #include <OpenAL/alext.h>\n"
        "  #include <OpenAL/efx-presets.h>\n"
        "#else\n"
        "  #include <AL/al.h>\n"
        "  #include <AL/alc.h>\n"
        "  #include <AL/alext.h>\n"
        "  #include <AL/efx-presets.h>\n"
        "#endif // __APPLE__ && !TARGET_OS_IPHONE\n",
        "SoundManager.h OpenAL include layout for iOS",
    )
    write(path, text)


def patch_network_header(ror):
    """Network.h: CurlFailInfo is declared under USE_SOCKETW but typed with CURLcode.

    So a build with SocketW but *without* libcurl - exactly our iOS setup -
    fails with "unknown type name 'CURLcode'" in every TU that includes
    Network.h.  In the first census that was 152 errors in one go.  The three
    functions that dereference the member guard their cURL bits with USE_CURL
    as well.
    """
    path = os.path.join(ror, "source", "main", "network", "Network.h")
    text = read(path)
    text = must_replace(
        text,
        "struct CurlFailInfo\n"
        "{\n"
        "    std::string title;\n"
        "    CURLcode curl_result = CURLE_OK;\n"
        "    long http_response = 0;\n"
        "};\n",
        "struct CurlFailInfo\n"
        "{\n"
        "    std::string title;\n"
        "#ifdef USE_CURL\n"
        "    // Apple/iOS port: typed with libcurl's CURLcode, but the struct\n"
        "    // itself is only guarded by USE_SOCKETW, so a SocketW-without-cURL\n"
        "    // build could not even name the type.\n"
        "    CURLcode curl_result = CURLE_OK;\n"
        "#endif\n"
        "    long http_response = 0;\n"
        "};\n",
        "Network.h CurlFailInfo cURL member",
    )
    write(path, text)


def patch_repo_selector(ror):
    """GUI_RepositorySelector.cpp: two spots use libcurl outside a USE_CURL guard.

    Both are only reachable from the "mod repository" HTTP code, which cannot
    work without libcurl anyway.
    """
    path = os.path.join(ror, "source", "main", "gui", "panels", "GUI_RepositorySelector.cpp")
    text = read(path)
    # 1) ShowError(): the message itself is fine without cURL, only the
    #    curl_result/curl_easy_strerror bits are not.
    text = must_replace(
        text,
        "    if (failinfo->curl_result != CURLE_OK)\n"
        "        m_repolist_curlmsg = curl_easy_strerror(failinfo->curl_result);\n",
        "#ifdef USE_CURL // Apple/iOS port: no libcurl in this build\n"
        "    if (failinfo->curl_result != CURLE_OK)\n"
        "        m_repolist_curlmsg = curl_easy_strerror(failinfo->curl_result);\n"
        "#endif\n",
        "GUI_RepositorySelector.cpp ShowError() curl bits",
    )
    # 2) The attachment-image downloader.  Without libcurl there is simply no
    #    way to fetch the image, so skipping the whole block drops straight
    #    into the existing "download failed" path just below the try/catch.
    text = must_replace(
        text,
        "            CURL *curl_th = curl_easy_init();\n"
        "            curl_easy_setopt(curl_th, CURLOPT_URL, url.c_str());\n"
        "            curl_easy_setopt(curl_th, CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4);\n"
        "            curl_easy_setopt(curl_th, CURLOPT_FOLLOWLOCATION, 1L); // Necessary for attachment images\n"
        "#ifdef _WIN32\n"
        "            curl_easy_setopt(curl_th, CURLOPT_SSL_OPTIONS, CURLSSLOPT_NATIVE_CA);\n"
        "#endif // _WIN32\n"
        "            curl_easy_setopt(curl_th, CURLOPT_WRITEFUNCTION, CurlOgreDataStreamWriteFunc);\n"
        "            curl_easy_setopt(curl_th, CURLOPT_WRITEDATA, datastream.get());\n"
        "            CURLcode curl_result = curl_easy_perform(curl_th);\n"
        "            curl_easy_cleanup(curl_th);\n"
        "\n"
        "            // If CURL follows a redirect then it returns 0 for HTTP response code.\n"
        "            if (curl_result != CURLE_OK || (response_code != 0 && response_code != 200))\n"
        "            {\n"
        "                Ogre::LogManager::getSingleton().stream()\n"
        "                    << \"[RoR|Repository] Failed to download image;\"\n"
        "                    << \" URL: '\" << url << \"',\"\n"
        "                    << \" Error: '\" << curl_easy_strerror(curl_result) << \"',\"\n"
        "                    << \" HTTP status code: \" << response_code;\n"
        "            }\n"
        "            else\n"
        "            {\n"
        "                App::GetGameContext()->PushMessage(Message(MSG_NET_DOWNLOAD_REPOIMAGE_SUCCESS, request));\n"
        "                return;\n"
        "            }\n",
        "#ifdef USE_CURL // Apple/iOS port: no libcurl in this build\n"
        "            CURL *curl_th = curl_easy_init();\n"
        "            curl_easy_setopt(curl_th, CURLOPT_URL, url.c_str());\n"
        "            curl_easy_setopt(curl_th, CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4);\n"
        "            curl_easy_setopt(curl_th, CURLOPT_FOLLOWLOCATION, 1L); // Necessary for attachment images\n"
        "#ifdef _WIN32\n"
        "            curl_easy_setopt(curl_th, CURLOPT_SSL_OPTIONS, CURLSSLOPT_NATIVE_CA);\n"
        "#endif // _WIN32\n"
        "            curl_easy_setopt(curl_th, CURLOPT_WRITEFUNCTION, CurlOgreDataStreamWriteFunc);\n"
        "            curl_easy_setopt(curl_th, CURLOPT_WRITEDATA, datastream.get());\n"
        "            CURLcode curl_result = curl_easy_perform(curl_th);\n"
        "            curl_easy_cleanup(curl_th);\n"
        "\n"
        "            // If CURL follows a redirect then it returns 0 for HTTP response code.\n"
        "            if (curl_result != CURLE_OK || (response_code != 0 && response_code != 200))\n"
        "            {\n"
        "                Ogre::LogManager::getSingleton().stream()\n"
        "                    << \"[RoR|Repository] Failed to download image;\"\n"
        "                    << \" URL: '\" << url << \"',\"\n"
        "                    << \" Error: '\" << curl_easy_strerror(curl_result) << \"',\"\n"
        "                    << \" HTTP status code: \" << response_code;\n"
        "            }\n"
        "            else\n"
        "            {\n"
        "                App::GetGameContext()->PushMessage(Message(MSG_NET_DOWNLOAD_REPOIMAGE_SUCCESS, request));\n"
        "                return;\n"
        "            }\n"
        "#endif // USE_CURL\n",
        "GUI_RepositorySelector.cpp image download curl block",
    )
    write(path, text)


def patch_multiplayer_selector(ror):
    """GUI_MultiplayerSelector.cpp: DisplayRefreshFailed() uses libcurl unguarded.

    It is called unconditionally from the MSG_NET_REFRESH_SERVERLIST_FAILURE
    handler in main.cpp, so only the curl bits are guarded off.
    """
    path = os.path.join(ror, "source", "main", "gui", "panels", "GUI_MultiplayerSelector.cpp")
    text = read(path)
    text = must_replace(
        text,
        "    if (failinfo->curl_result != CURLE_OK)\n"
        "        m_serverlist_curlmsg = curl_easy_strerror(failinfo->curl_result);\n",
        "#ifdef USE_CURL // Apple/iOS port: no libcurl in this build\n"
        "    if (failinfo->curl_result != CURLE_OK)\n"
        "        m_serverlist_curlmsg = curl_easy_strerror(failinfo->curl_result);\n"
        "#endif\n",
        "GUI_MultiplayerSelector.cpp DisplayRefreshFailed() curl bits",
    )
    write(path, text)


def patch_server_script_curl_helpers(ror):
    """ServerScriptCurlHelpers.h: the status enum must not sit behind USE_CURL.

    ServerScriptCurlStatusType is part of the *scripting* API (the `curlStatus`
    callback) and is named by ServerScriptEngine.h/.cpp, which are compiled
    with or without libcurl.  In the first census its absence alone produced 13
    errors.  Only the cURL-typed bits below it actually need libcurl, so the
    enum moves out of the guard and the namespace stays open around both.
    """
    path = os.path.join(ror, "source", "main", "scripting", "server", "ServerScriptCurlHelpers.h")
    text = read(path)
    text = must_replace(
        text,
        "#ifdef USE_CURL\n"
        "\n"
        '#include "CurlHelpers.h" // RIGSOFRODS: This is actually the client\'s header with same name as original server header. Both implement the same functionality but with cosmetic differences.\n'
        "\n"
        "namespace RoR { // RIGSOFRODS\n"
        "\n"
        "enum ServerScriptCurlStatusType\n"
        "{\n"
        "    CURL_STATUS_INVALID,  //!< Should never be reported.\n"
        "    CURL_STATUS_START,    //!< New CURL request started, n1/n2 both 0.\n"
        "    CURL_STATUS_PROGRESS, //!< Download in progress, n1 = bytes downloaded, n2 = total bytes.\n"
        "    CURL_STATUS_SUCCESS,  //!< CURL request finished, n1 = CURL return code, n2 = HTTP result code, message = received payload.\n"
        "    CURL_STATUS_FAILURE,  //!< CURL request finished, n1 = CURL return code, n2 = HTTP result code, message = CURL error string.\n"
        "};\n"
        "\n"
        "void ServerScriptCurlRequestThreadFunc(CurlTaskContext context);\n"
        "\n"
        "} // namespace RoR\n"
        "\n"
        "#endif // USE_CURL\n",
        "namespace RoR { // RIGSOFRODS\n"
        "\n"
        "// Apple/iOS port: this enum is part of the scripting API (the\n"
        "// `curlStatus` callback) and is named by ServerScriptEngine.h/.cpp,\n"
        "// which are compiled whether or not libcurl is available - so it must\n"
        "// live outside the USE_CURL guard.  Only the cURL-typed declaration\n"
        "// below actually needs libcurl.\n"
        "enum ServerScriptCurlStatusType\n"
        "{\n"
        "    CURL_STATUS_INVALID,  //!< Should never be reported.\n"
        "    CURL_STATUS_START,    //!< New CURL request started, n1/n2 both 0.\n"
        "    CURL_STATUS_PROGRESS, //!< Download in progress, n1 = bytes downloaded, n2 = total bytes.\n"
        "    CURL_STATUS_SUCCESS,  //!< CURL request finished, n1 = CURL return code, n2 = HTTP result code, message = received payload.\n"
        "    CURL_STATUS_FAILURE,  //!< CURL request finished, n1 = CURL return code, n2 = HTTP result code, message = CURL error string.\n"
        "};\n"
        "\n"
        "#ifdef USE_CURL\n"
        "\n"
        '#include "CurlHelpers.h" // RIGSOFRODS: This is actually the client\'s header with same name as original server header. Both implement the same functionality but with cosmetic differences.\n'
        "\n"
        "void ServerScriptCurlRequestThreadFunc(CurlTaskContext context);\n"
        "\n"
        "#endif // USE_CURL\n"
        "\n"
        "} // namespace RoR\n",
        "ServerScriptCurlHelpers.h curl status enum outside the guard",
    )
    write(path, text)


def patch_game_settings_alc(ror):
    """GUI_GameSettings.cpp: a flat <alc.h> include only works with macOS frameworks.

    pch.h and SoundManager.h both use the standard openal-soft layout
    (<AL/al.h>, <AL/alc.h>), which is what our iOS SDK ships, so this lone
    `#include <alc.h>` was the very last compile error left in the census.

    TargetConditionals.h is already in scope here because SoundManager.h
    (included just above) pulls it in on Apple platforms.
    """
    path = os.path.join(ror, "source", "main", "gui", "panels", "GUI_GameSettings.cpp")
    text = read(path)
    text = must_replace(
        text,
        "#ifdef USE_OPENAL\n"
        "#   include <alc.h>\n"
        "#endif\n",
        "#ifdef USE_OPENAL\n"
        "#   if defined(__APPLE__) && TARGET_OS_IPHONE\n"
        "#       include <AL/alc.h> // Apple/iOS port: openal-soft layout, not the macOS framework\n"
        "#   else\n"
        "#       include <alc.h>\n"
        "#   endif\n"
        "#endif\n",
        "GUI_GameSettings.cpp <alc.h> include for iOS",
    )
    write(path, text)


IOS_PLATFORM_H = r'''// Rigs of Rods - Apple/iOS platform layer.
//
// Why this exists
// ---------------
// Two things cannot be expressed in plain C++ on iOS:
//
//  * The process entry point.  UIKit needs UIApplicationMain() to build the
//    UIApplication singleton and start the application run loop, and
//    UIApplicationMain() never returns - so RoR's own blocking main() can no
//    longer be the entry point.  On iOS it is renamed to RoRMain() and called
//    from the app delegate instead (see ios_main.mm).
//
//  * Servicing the run loop.  RoR's frame loop calls
//    OgreBites::WindowEventUtilities::messagePump(), which is a no-op on iOS
//    (OgreWindowEventUtilities.cpp), so unless somebody pumps the run loop
//    UIKit never gets to deliver touch events and iOS eventually kills the app
//    for not responding.
//
// ROR_PLATFORM_IOS (defined by source/main/CMakeLists.txt on iOS) is the
// switch the patched sources use, not OGRE_PLATFORM_APPLE_IOS: before
// OgrePlatform.h has been included OGRE_PLATFORM is undefined and evaluates to
// 0, which makes that comparison accidentally true.
#ifndef ROR_IOS_PLATFORM_H
#define ROR_IOS_PLATFORM_H

#ifdef __cplusplus
extern "C" {
#endif

// RoR's own main(), renamed on iOS.  Called by the app delegate; never returns.
int RoRMain(int argc, char* argv[]);

// Runs the UIKit side of the main run loop exactly once and returns
// immediately.  Call once per frame.
void RoR_IosPumpRunLoop(void);

// Size of the main screen in points, which is what OGRE's EAGL2Window builds
// its UIWindow from.  RoR is landscape-only, so width is the long side.
void RoR_IosGetScreenSize(int* width, int* height);

// Appends one line to <the app container>/Documents/ror-trace.log and hands it
// to write(2) straight away, so the file is complete even if the process dies
// on the very next instruction.  OGRE's own log file is buffered, and a crash
// during startup therefore loses its tail - exactly the lines that say what
// went wrong.  The app container's Documents folder is visible in the Files
// app, because Info.plist sets UIFileSharingEnabled.
void RoR_IosTrace(const char* format, ...);

// Installs the SIGABRT/SIGSEGV/SIGBUS/SIGILL/SIGFPE and std::terminate handlers
// that append the reason to that same file.  Call it as early as possible.
void RoR_IosInstallCrashHandlers(void);

// Same as RoR_IosTrace(), and then runs the main run loop once.  Call this at
// each startup milestone: it records how far startup got, and keeps iOS's "app
// not responding" watchdog happy while the slow parts run, because RoR does not
// return to the run loop until its own frame loop starts.
void RoR_IosStartupTick(const char* format, ...);

#ifdef __cplusplus
}
#endif

#endif // ROR_IOS_PLATFORM_H
'''

IOS_PLATFORM_MM = r'''// Rigs of Rods - Apple/iOS platform layer implementation.  See ios_platform.h.
#include "ios_platform.h"

#import <UIKit/UIKit.h>
#import <CoreFoundation/CFRunLoop.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <exception>
#include <string>
#include <dlfcn.h>
#include <signal.h>
#include <sys/ucontext.h>
#include <fcntl.h>
#include <unistd.h>

// ---------------------------------------------------------------------------
// Crash-proof tracing
// ---------------------------------------------------------------------------
// Everything here writes to a raw descriptor with write(2), which is the only
// way to get a line onto the disk from inside a signal handler - and it is also
// what makes the file complete when the process dies before stdio would have
// flushed.
static int g_trace_fd = -1;

static void ror_trace_open(void)
{
    if (g_trace_fd >= 0)
    {
        return;
    }
    const char* home = getenv("HOME");
    if (home == nullptr)
    {
        return;
    }
    const std::string path = std::string(home) + "/Documents/ror-trace.log";
    g_trace_fd = open(path.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
}

static void ror_trace_write(const char* text)
{
    if (g_trace_fd < 0)
    {
        return;
    }
    const ssize_t len = (ssize_t)strlen(text);
    ssize_t done = 0;
    while (done < len)
    {
        const ssize_t written = write(g_trace_fd, text + done, (size_t)(len - done));
        if (written <= 0)
        {
            break;
        }
        done += written;
    }
}

static void ror_trace_v(const char* format, va_list args)
{
    ror_trace_open();
    if (g_trace_fd < 0)
    {
        return;
    }

    va_list args_copy;
    va_copy(args_copy, args);
    const int needed = vsnprintf(nullptr, 0, format, args);
    if (needed > 0)
    {
        std::string buffer((size_t)needed + 1, '\0');
        vsnprintf(&buffer[0], buffer.size(), format, args_copy);
        ror_trace_write("[trace] ");
        ror_trace_write(buffer.c_str());
        ror_trace_write("\n");
    }
    va_end(args_copy);
}

void RoR_IosTrace(const char* format, ...)
{
    va_list args;
    va_start(args, format);
    ror_trace_v(format, args);
    va_end(args);
}

void RoR_IosStartupTick(const char* format, ...)
{
    va_list args;
    va_start(args, format);
    ror_trace_v(format, args);
    va_end(args);

    // Startup can take a while - OGRE brings up the GLES2 device, compiles the
    // shaders and the resource groups are indexed - and RoR never returns to
    // the run loop until the frame loop below.  iOS kills a process whose main
    // thread stays out of the run loop for too long ("app not responding",
    // 0x8badf00d).  Servicing it once per startup phase costs nothing and
    // removes that whole class of failure.
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.0, /*returnAfterSourceHandled=*/false);
}

static const char* ror_signal_name(int sig)
{
    switch (sig)
    {
        case SIGABRT: return "SIGABRT - abort(), typically an uncaught C++ exception";
        case SIGSEGV: return "SIGSEGV - invalid memory access";
        case SIGBUS:  return "SIGBUS - bad or misaligned address";
        case SIGILL:  return "SIGILL - illegal instruction";
        case SIGFPE:  return "SIGFPE - arithmetic fault";
        default:      return "unrecognised signal";
    }
}

// Address the Mach-O header of the main executable is mapped at, recorded once
// in RoR_IosInstallCrashHandlers().  The faulting PC minus this value is an
// address in the same space as the symbols of the .app binary, so the crash
// line can be resolved without the device: `nm -n RoR.app/RoR` or
// `atos -o RoR.app/RoR -l 0`.  A PC outside the executable (a frame inside
// libsystem, for example) is obvious from the offset, and the caller is then
// named by the LR, which is always inside our own code.
static uintptr_t g_exe_base = 0;

// snprintf into a caller-supplied buffer, appended with write(2): %llx does not
// allocate, which keeps this usable from a signal handler.
static void ror_trace_hex(const char* prefix, unsigned long long value,
                          const char* suffix)
{
    char buf[160];
    const int n = snprintf(buf, sizeof(buf), "%s0x%llx%s", prefix, value, suffix);
    if (n > 0)
    {
        ror_trace_write(buf);
    }
}

static void ror_signal_handler(int sig, siginfo_t* info, void* uap)
{
    ror_trace_open();
    ror_trace_write("\n[crash] ");
    ror_trace_write(ror_signal_name(sig));

    // si_addr is the address that could not be accessed: 0x0 means a null
    // pointer dereference (something was never initialised), anything else is a
    // wild pointer (something was freed, corrupted or cast wrongly).
    if (info != nullptr)
    {
        ror_trace_hex(" addr=", (unsigned long long)(uintptr_t)info->si_addr, "");
    }

#if defined(__arm64__) || defined(__aarch64__)
    if (uap != nullptr)
    {
        const ucontext_t* uc = (const ucontext_t*)uap;
        const unsigned long long pc = (unsigned long long)(uintptr_t)uc->uc_mcontext->__ss.__pc;
        const unsigned long long lr = (unsigned long long)(uintptr_t)uc->uc_mcontext->__ss.__lr;
        const unsigned long long fp = (unsigned long long)(uintptr_t)uc->uc_mcontext->__ss.__fp;
        if (g_exe_base != 0)
        {
            ror_trace_hex(" pc=", pc, "");
            ror_trace_hex("(+", pc - (unsigned long long)g_exe_base, ")");
            ror_trace_hex(" lr=", lr, "");
            ror_trace_hex("(+", lr - (unsigned long long)g_exe_base, ")");
            ror_trace_hex(" fp=", fp, "");
        }
        else
        {
            ror_trace_hex(" pc=", pc, "");
            ror_trace_hex(" lr=", lr, "");
        }
    }
#endif
    ror_trace_write("\n");

    // Put the default action back and re-raise, so the process still dies with
    // the real signal and the system writes its own crash report.
    signal(sig, SIG_DFL);
    raise(sig);
}

static void ror_terminate_handler(void)
{
    ror_trace_open();
    ror_trace_write("\n[crash] std::terminate() called");
    // The exception is normally still in flight here, so a bare throw can be
    // caught and its message recorded.
    try
    {
        throw;
    }
    catch (const std::exception& e)
    {
        ror_trace_write(" - std::exception: ");
        ror_trace_write(e.what());
    }
    catch (const std::string& s)
    {
        ror_trace_write(" - std::string: ");
        ror_trace_write(s.c_str());
    }
    catch (const char* s)
    {
        ror_trace_write(" - const char*: ");
        ror_trace_write(s);
    }
    catch (...)
    {
        ror_trace_write(" - exception of an unknown type");
    }
    ror_trace_write("\n");
    abort();
}

void RoR_IosInstallCrashHandlers(void)
{
    ror_trace_open();

    // Remember where the executable was loaded.  iOS always uses ASLR, so the
    // absolute addresses change on every launch and mean nothing on their own;
    // everything below is therefore also printed relative to this base.
    // &g_exe_base is a data address in the main executable, which sidesteps the
    // function-pointer cast dladdr would otherwise need.
    Dl_info dl_info;
    if (dladdr((const void*)&g_exe_base, &dl_info) != 0)
    {
        g_exe_base = (uintptr_t)dl_info.dli_fbase;
    }
    {
        char buf[160];
        const int n = snprintf(buf, sizeof(buf),
            "\n[trace] ----- crash handlers installed (exe base 0x%llx) -----\n",
            (unsigned long long)g_exe_base);
        if (n > 0)
        {
            ror_trace_write(buf);
        }
    }

    // Give the handlers a stack of their own: if the crash is a stack overflow
    // the normal stack is unusable, and without an alternate stack the handler
    // itself faults and the reason is lost.
    static char s_altstack[64 * 1024];
    stack_t ss;
    memset(&ss, 0, sizeof(ss));
    ss.ss_sp = s_altstack;
    ss.ss_size = sizeof(s_altstack);
    sigaltstack(&ss, nullptr);

    // sigaction rather than signal(), because only the former hands si_addr and
    // the register context to the handler.
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = ror_signal_handler;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGABRT, &sa, nullptr);
    sigaction(SIGSEGV, &sa, nullptr);
    sigaction(SIGBUS,  &sa, nullptr);
    sigaction(SIGILL,  &sa, nullptr);
    sigaction(SIGFPE,  &sa, nullptr);

    std::set_terminate(ror_terminate_handler);
}

void RoR_IosPumpRunLoop(void)
{
    // Run the main run loop once and return immediately: we are called from
    // inside RoR's own frame loop, so blocking here would stall rendering.
    // This still drains everything UIKit has queued for the app, because the
    // touch/event machinery (_UIApplicationHandleEventQueue) is a run loop
    // source, which is what makes the touch layer work at all.
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.0, /*returnAfterSourceHandled=*/false);
}

void RoR_IosGetScreenSize(int* width, int* height)
{
    // UIScreen.mainScreen.bounds is always reported in the portrait frame,
    // whatever the current orientation happens to be, and RoR is
    // landscape-only - so the long side is the width.
    const CGRect bounds = [[UIScreen mainScreen] bounds];
    const int a = (int)(bounds.size.width + 0.5);
    const int b = (int)(bounds.size.height + 0.5);
    const int long_side  = a > b ? a : b;
    const int short_side = a > b ? b : a;
    if (width)  *width  = long_side;
    if (height) *height = short_side;
}
'''

IOS_MAIN_MM = r'''// Rigs of Rods - Apple/iOS process entry point.  See ios_platform.h.
//
// UIApplicationMain() never returns, so it has to be the outermost call, and
// RoR's main() (renamed to RoRMain) is invoked from the app delegate instead.
//
// There is deliberately no UIWindow/UIViewController setup here: OGRE's
// EAGL2Window builds its own UIWindow, EAGL2View and EAGL2ViewController and
// makes them visible when no externalWindowHandle misc parameter is supplied
// (RenderSystems/GLES2/src/EAGL/OgreEAGL2Window.mm), which is exactly RoR's
// case - it calls createRenderWindow() with an empty params list on iOS.
#include "ios_platform.h"

#import <UIKit/UIKit.h>
#import <dispatch/dispatch.h>

static int    g_argc = 0;
static char** g_argv = nullptr;

@interface RoRAppDelegate : UIResponder <UIApplicationDelegate>
@end

@implementation RoRAppDelegate

- (BOOL)application:(UIApplication*)application
        didFinishLaunchingWithOptions:(NSDictionary*)launchOptions
{
    (void)application;
    (void)launchOptions;

    // Return from -didFinishLaunchingWithOptions: immediately and start RoR on
    // the next main run loop turn.  iOS kills an app whose launch phase does
    // not finish in time (watchdog 0x8badf00d), and RoRMain never returns - it
    // blocks for the whole lifetime of the process.  Doing the blocking work
    // here would keep -didFinishLaunching from ever returning, which is exactly
    // the pathological case the watchdog exists for.  RoR still owns the main
    // thread afterwards and services UIKit from inside its frame loop via
    // RoR_IosPumpRunLoop().
    dispatch_async(dispatch_get_main_queue(), ^{
        RoRMain(g_argc, g_argv);
    });
    return YES;
}

@end

int main(int argc, char* argv[])
{
    @autoreleasepool
    {
        // First thing in the process: from here on every line of RoR's startup
        // is mirrored into Documents/ror-trace.log and any crash is recorded
        // with its reason (see ios_platform.mm).
        RoR_IosInstallCrashHandlers();

        g_argc = argc;
        g_argv = argv;
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([RoRAppDelegate class]));
    }
}
'''


def patch_ios_platform(ror):
    """Create the iOS platform layer at source/main/ios/.

    These three files do not exist upstream, so this patch writes them rather
    than editing anything.  They are added to the RoR target by
    patch_main_cmake().
    """
    directory = os.path.join(ror, "source", "main", "ios")
    os.makedirs(directory, exist_ok=True)
    write(os.path.join(directory, "ios_platform.h"), IOS_PLATFORM_H)
    write(os.path.join(directory, "ios_platform.mm"), IOS_PLATFORM_MM)
    write(os.path.join(directory, "ios_main.mm"), IOS_MAIN_MM)


def patch_root_cmake(ror):
    """Root CMakeLists.txt: enable Objective-C++ for the iOS platform layer.

    project() at line 22 has no LANGUAGES clause, so only C and CXX are enabled
    - and source/main/ios/*.mm cannot be compiled until OBJCXX is on.  Enabled
    from the top-level scope and before add_subdirectory(source/main), because
    CMake has to know the language before it resolves a target's sources.
    """
    path = os.path.join(ror, "CMakeLists.txt")
    text = read(path)
    text = must_replace(
        text,
        "project(${PROJECT_NAME_UNDERSCORE} VERSION 2026.01)\n",
        "project(${PROJECT_NAME_UNDERSCORE} VERSION 2026.01)\n"
        "\n"
        "# Apple/iOS port: RoR's project() above enables only C and CXX, but the\n"
        "# iOS platform layer (source/main/ios/*.mm) needs Objective-C++.\n"
        "if (APPLE AND CMAKE_SYSTEM_NAME STREQUAL \"iOS\")\n"
        "    enable_language(OBJCXX)\n"
        "endif ()\n",
        "CMakeLists.txt enable_language(OBJCXX)",
    )
    write(path, text)


def patch_main_cpp(ror):
    """main.cpp: hand the entry point over to UIKit on iOS.

    UIApplicationMain() never returns, so RoR's main() cannot be the process
    entry point any more - it becomes RoRMain() and ios_main.mm calls it from
    the app delegate.  The frame loop additionally has to service the run loop,
    because OgreBites::WindowEventUtilities::messagePump() is a no-op on iOS
    (OgreWindowEventUtilities.cpp:174-212): without it UIKit never delivers
    touch events and iOS eventually kills the app for not responding.
    """
    path = os.path.join(ror, "source", "main", "main.cpp")
    text = read(path)
    text = must_replace(
        text,
        '#include "Utils.h"\n'
        "#include <Overlay/OgreOverlaySystem.h>\n",
        '#include "Utils.h"\n'
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "ios_platform.h"\n'
        "#endif\n"
        "#include <Overlay/OgreOverlaySystem.h>\n",
        "main.cpp ios_platform.h include",
    )
    # `main` already sits inside an `extern "C" {` block here, so RoRMain keeps
    # C linkage and is directly callable from ios_main.mm.
    text = must_replace(
        text,
        "int main(int argc, char *argv[])\n"
        "{\n"
        "    using namespace RoR;\n",
        "#ifdef ROR_PLATFORM_IOS\n"
        "// Apple/iOS port: not the process entry point on iOS - see ios_main.mm.\n"
        "int RoRMain(int argc, char *argv[])\n"
        "#else\n"
        "int main(int argc, char *argv[])\n"
        "#endif\n"
        "{\n"
        "    using namespace RoR;\n",
        "main.cpp rename main to RoRMain on iOS",
    )
    text = must_replace(
        text,
        "            App::GetAppContext()->PrepareProfiler();\n"
        "            OgreBites::WindowEventUtilities::messagePump();\n",
        "            App::GetAppContext()->PrepareProfiler();\n"
        "            OgreBites::WindowEventUtilities::messagePump();\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "            // Apple/iOS port: messagePump() does nothing on iOS, so\n"
        "            // service UIKit here or there are no touches at all.\n"
        "            RoR_IosPumpRunLoop();\n"
        "            // Breadcrumb, so the trace file shows the app is alive and\n"
        "            // rendering rather than stuck somewhere with no output.\n"
        "            static unsigned long s_ios_frames = 0;\n"
        "            ++s_ios_frames;\n"
        "            if (s_ios_frames == 1 || (s_ios_frames % 600) == 0)\n"
        "            {\n"
        '                RoR_IosTrace("frame %lu completed", s_ios_frames);\n'
        "            }\n"
        "#endif\n",
        "main.cpp pump the iOS run loop each frame",
    )

    # ---------------------------------------------------------------------
    # Crash-proof startup breadcrumbs
    # ---------------------------------------------------------------------
    # One line per phase, written straight to Documents/ror-trace.log by
    # ios_platform.mm.  OGRE's own log is buffered and loses its tail on a hard
    # crash, which is the only part that matters when startup dies - the last
    # breadcrumb in the trace file names the phase that killed it.
    breadcrumbs = (
        ("        App::GetAppContext()->SetUpThreads();\n",
         "RoRMain entered; main thread recorded"),
        ("        App::GetAppContext()->SetUpLogging();\n",
         "program paths resolved; OGRE log created"),
        ("        App::GetConsole()->processCommandLine(argc, argv);\n",
         "RoR.cfg and command line processed"),
        ("        CreateFolder(App::sys_config_dir->getStr());\n",
         "resource directory found and accepted"),
        ("        Ogre::TextureManager::getSingleton().setDefaultNumMipmaps(5);\n",
         "OGRE is up and the render window was created"),
        # These two used to share a single breadcrumb, which could not say which
        # of them died.  SetUpConfigSkeleton() is the first place RoR touches a
        # Zip archive (skeleton.zip) and the OverlaySystem constructor is the
        # first place the Overlay component comes up; a failure in either looked
        # identical in the trace file.
        ("        if (!App::GetAppContext()->SetUpConfigSkeleton())\n"
         "        {\n"
         "            return -1; // Error already displayed\n"
         "        }\n",
         "config skeleton deployed from skeleton.zip"),
        ("        Ogre::OverlaySystem* overlay_system = new Ogre::OverlaySystem(); //Overlay init\n",
         "overlay system constructed (OverlayManager + FontManager up)"),
        ("        App::GetContentManager()->AddResourcePack(ContentManager::ResourcePack::SCRIPTS);\n",
         "environment textures created; resource packs registered"),
        ("        App::GetConsole()->regBuiltinCommands(); // Call after localization had been set up\n",
         "localisation set up"),
        ("        App::GetContentManager()->InitContentManager();\n",
         "content manager initialised"),
        ("        App::GetGfxScene()->GetEnvMap().SetupEnvMap(); // Needs camera\n",
         "scene, camera and environment map ready"),
        ("        App::CreateGuiManager(); // Needs scene manager\n",
         "GUI manager created"),
        ("        App::GetDiscordRpc()->Init();\n"
         "\n"
         "        App::GetAppContext()->SetUpInput();\n",
         "Discord RPC done; input engine ready"),
        ("        App::GetGuiManager()->SetUpMenuWallpaper();\n",
         "AngelScript ready; menu wallpaper set"),
        ("        App::app_state->setVal((int)AppState::MAIN_MENU);\n"
         "        App::GetGuiManager()->MenuWallpaper->show();\n",
         "entering the main menu state"),
        ("        auto start_time = std::chrono::high_resolution_clock::now();\n",
         "complete; entering the frame loop"),
    )
    for anchor, label in breadcrumbs:
        found = text.count(anchor)
        if found != 1:
            raise SystemExit(
                "ERROR: main.cpp breadcrumb anchor found %d times, expected 1: %r"
                % (found, anchor))
        text = text.replace(
            anchor,
            anchor
            + "#ifdef ROR_PLATFORM_IOS\n"
            + '        RoR_IosStartupTick("startup: %s");\n' % label
            + "#endif\n",
        )
    write(path, text)


def patch_inputengine_header(ror):
    """InputEngine.h: teach the engine to be an OIS multitouch listener on iOS.

    OIS's iPhone backend (src/iphone/*.mm, which is what we build and link) has
    no keyboard and no mouse at all: iPhoneInputManager::freeDeviceList() offers
    only OISJoyStick (the accelerometer) and OISMultiTouch.  So
    createInputObject(OISKeyboard) and createInputObject(OISMouse) both throw
    "No devices match requested type", RoR's own try/catch leaves mKeyboard and
    mMouse null - and RoR dereferences them unguarded in Capture() and
    windowResized().  Rather than special-case every dereference, InputEngine
    becomes the multitouch listener and translates touches into the mouse
    events RoR already understands.
    """
    path = os.path.join(ror, "source", "main", "utils", "InputEngine.h")
    text = read(path)

    text = must_replace(
        text,
        '#include "OISMouse.h"\n',
        '#include "OISMouse.h"\n'
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "OISMultiTouch.h"\n'
        "#endif\n",
        "InputEngine.h include OISMultiTouch.h",
    )

    # Deriving from the listener is cheaper than a second class: OIS allows only
    # one callback object per device anyway.
    text = must_replace(
        text,
        "/// Manages controller configuration, evaluates input events\n"
        "class InputEngine\n"
        "{\n"
        "public:\n",
        "/// Manages controller configuration, evaluates input events\n"
        "class InputEngine\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: OIS's iPhone backend reports touches through this\n"
        "    // interface and offers no keyboard or mouse at all.\n"
        "    : public OIS::MultiTouchListener\n"
        "#endif\n"
        "{\n"
        "public:\n",
        "InputEngine.h derive from OIS::MultiTouchListener",
    )

    text = must_replace(
        text,
        "    void windowResized(Ogre::RenderWindow* rw);\n"
        "    OIS::ForceFeedback* getForceFeedbackDevice() { return mForceFeedback; };\n",
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port -----------------------------------------------\n"
        "    // OIS::MultiTouchListener\n"
        "    bool                touchMoved(const OIS::MultiTouchEvent& arg) override;\n"
        "    bool                touchPressed(const OIS::MultiTouchEvent& arg) override;\n"
        "    bool                touchReleased(const OIS::MultiTouchEvent& arg) override;\n"
        "    bool                touchCancelled(const OIS::MultiTouchEvent& arg) override;\n"
        "    // Translates one touch into RoR's ordinary mouse path.\n"
        "    void                IosHandleTouch(int action, float x, float y);\n"
        "    // Creates the accelerometer-as-joystick and the multitouch device.\n"
        "    void                IosSetupInput(size_t hWnd);\n"
        "#endif\n"
        "    void windowResized(Ogre::RenderWindow* rw);\n"
        "    OIS::ForceFeedback* getForceFeedbackDevice() { return mForceFeedback; };\n",
        "InputEngine.h multitouch + IosHandleTouch declarations",
    )

    text = must_replace(
        text,
        "    OIS::InputManager* mInputManager;\n"
        "    OIS::Mouse* mMouse;\n"
        "    OIS::Keyboard* mKeyboard;\n"
        "    OIS::JoyStick* mJoy[MAX_JOYSTICKS];\n"
        "    int free_joysticks; //!< Number of detected game controllers\n"
        "    OIS::ForceFeedback* mForceFeedback;\n",
        "    OIS::InputManager* mInputManager;\n"
        "    OIS::Mouse* mMouse;\n"
        "    OIS::Keyboard* mKeyboard;\n"
        "    OIS::JoyStick* mJoy[MAX_JOYSTICKS];\n"
        "    int free_joysticks; //!< Number of detected game controllers\n"
        "    OIS::ForceFeedback* mForceFeedback;\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: the multitouch device replaces mouse and keyboard,\n"
        "    // and the listeners have to be remembered here because there is no\n"
        "    // OIS::Mouse/OIS::Keyboard to hand them to.\n"
        "    OIS::MultiTouch*   mMultiTouch;\n"
        "    OIS::MouseListener* mMouseListener;\n"
        "    OIS::KeyListener*  mKeyboardListener;\n"
        "#endif\n",
        "InputEngine.h iOS members",
    )
    write(path, text)


def patch_inputengine(ror):
    """InputEngine.cpp: make the engine survive - and work - on iOS.

    Two separate problems:

      * Crashes.  Capture() and windowResized() dereference mKeyboard/mMouse
        unguarded, and both are null on iOS (see patch_inputengine_header).
        windowResized() is even called from the constructor, so this is not a
        hypothetical: the engine cannot be constructed at all.
      * No input.  Touches arrive on OIS's multitouch device, whose listener is
        the only thing that calls RoR's mouse callbacks - OIS does that from
        inside Mouse::capture(), which never runs here.
    """
    path = os.path.join(ror, "source", "main", "utils", "InputEngine.cpp")
    text = read(path)

    # Cleanup: the declarators are fine, but the _L macro and friends are not
    # the issue here - only the OIS dereferences are.
    text = must_replace(
        text,
        "InputEngine::InputEngine() :\n"
        "     free_joysticks(0)\n"
        "    , mForceFeedback(0)\n"
        "    , mInputManager(0)\n"
        "    , mKeyboard(0)\n"
        "    , mMouse(0)\n"
        "    , uniqueCounter(0)\n",
        "InputEngine::InputEngine() :\n"
        "     free_joysticks(0)\n"
        "    , mForceFeedback(0)\n"
        "    , mInputManager(0)\n"
        "    , mKeyboard(0)\n"
        "    , mMouse(0)\n"
        "    , uniqueCounter(0)\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    , mMultiTouch(0)\n"
        "    , mMouseListener(0)\n"
        "    , mKeyboardListener(0)\n"
        "#endif\n",
        "InputEngine.cpp ctor initialiser list",
    )

    # setup(): the whole desktop path assumes a windowed input system that can
    # hand out keyboards and mice.  On iOS take the multitouch path instead and
    # then fall through to the binding table, which the desktop path also needs.
    text = must_replace(
        text,
        "void InputEngine::setup()\n"
        "{\n"
        "    size_t hWnd = 0;\n"
        '    App::GetAppContext()->GetRenderWindow()->getCustomAttribute("WINDOW", &hWnd);\n'
        "\n"
        '    LOG("*** Initializing OIS ***");\n',
        "void InputEngine::setup()\n"
        "{\n"
        "    size_t hWnd = 0;\n"
        '    App::GetAppContext()->GetRenderWindow()->getCustomAttribute("WINDOW", &hWnd);\n'
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: OIS's iPhone backend creates its own touch view on\n"
        "    // top of the UIWindow OGRE made for us, and offers an accelerometer\n"
        "    // (as a joystick) plus a multitouch device.  There is no keyboard and\n"
        "    // no mouse, so take that path and then do the binding table setup\n"
        "    // that the desktop path performs at the end of this function.\n"
        '    LOG("[RoR|Startup|Input] iOS: using OIS\'s iPhone multitouch backend");\n'
        "    this->IosSetupInput(hWnd);\n"
        "    this->loadConfigFile(-1);\n"
        "    completeMissingEvents();\n"
        "    return;\n"
        "#else\n"
        '    LOG("*** Initializing OIS ***");\n',
        "InputEngine.cpp setup() iOS branch",
    )
    text = must_replace(
        text,
        "    completeMissingEvents();\n"
        "}\n"
        "\n"
        "OIS::MouseState InputEngine::getMouseState()\n",
        "    completeMissingEvents();\n"
        "#endif // ROR_PLATFORM_IOS\n"
        "}\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "void InputEngine::IosSetupInput(size_t hWnd)\n"
        "{\n"
        "    OIS::ParamList pl;\n"
        '    pl.insert(OIS::ParamList::value_type("WINDOW", TOSTRING(hWnd)));\n'
        "\n"
        "    // OIS's iPhone backend throws if it cannot find a UIWindow, so this\n"
        "    // must not take the process down - without input the app is still\n"
        "    // usable enough to see what is wrong.\n"
        "    try\n"
        "    {\n"
        "        mInputManager = OIS::InputManager::createInputSystem(pl);\n"
        "    }\n"
        "    catch (OIS::Exception& ex)\n"
        "    {\n"
        "        mInputManager = nullptr;\n"
        '        LOG(String("Exception raised on OIS input system creation: ") + String(ex.eText));\n'
        "    }\n"
        "\n"
        "    if (mInputManager == nullptr)\n"
        "    {\n"
        '        LOG("[RoR|Startup|Input] WARNING - no OIS input system, no input will be available");\n'
        "        return;\n"
        "    }\n"
        "\n"
        "    mKeyboard = nullptr;\n"
        "    mMouse = nullptr;\n"
        "\n"
        "    // The accelerometer is exposed as a joystick; keep it so the existing\n"
        "    // joystick plumbing (and force feedback lookups) stay happy.\n"
        "    try\n"
        "    {\n"
        "        int numSticks = std::min(mInputManager->getNumberOfDevices(OISJoyStick), (int)MAX_JOYSTICKS);\n"
        "        free_joysticks = 0;\n"
        "        for (int i = 0; i < numSticks; ++i)\n"
        "        {\n"
        "            mJoy[i] = (OIS::JoyStick*)mInputManager->createInputObject(OISJoyStick, true);\n"
        "            free_joysticks++;\n"
        "        }\n"
        "    }\n"
        "    catch (OIS::Exception& ex)\n"
        "    {\n"
        '        LOG(String("Exception raised on joystick creation: ") + String(ex.eText));\n'
        "    }\n"
        "\n"
        "    try\n"
        "    {\n"
        "        mMultiTouch = static_cast<OIS::MultiTouch*>(mInputManager->createInputObject(OISMultiTouch, true));\n"
        "        mMultiTouch->setEventCallback(this);\n"
        '        LOG("[RoR|Startup|Input] iOS: multitouch device ready");\n'
        "    }\n"
        "    catch (OIS::Exception& ex)\n"
        "    {\n"
        "        mMultiTouch = nullptr;\n"
        '        LOG(String("Exception raised on multitouch creation: ") + String(ex.eText));\n'
        "    }\n"
        "}\n"
        "#endif // ROR_PLATFORM_IOS\n"
        "\n"
        "OIS::MouseState InputEngine::getMouseState()\n",
        "InputEngine.cpp IosSetupInput",
    )

    text = must_replace(
        text,
        "void InputEngine::Capture()\n"
        "{\n"
        "    mKeyboard->capture();\n"
        "    mMouse->capture();\n",
        "void InputEngine::Capture()\n"
        "{\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: mKeyboard and mMouse do not exist here, and touches\n"
        "    // arrive through the multitouch listener rather than by polling.\n"
        "    if (mMultiTouch)\n"
        "    {\n"
        "        mMultiTouch->capture();\n"
        "    }\n"
        "#else\n"
        "    mKeyboard->capture();\n"
        "    mMouse->capture();\n"
        "#endif\n",
        "InputEngine.cpp Capture()",
    )

    text = must_replace(
        text,
        "    rw->getMetrics(width, height, depth, left, top);\n"
        "    const OIS::MouseState& ms = mMouse->getMouseState();\n"
        "    ms.width = width;\n"
        "    ms.height = height;\n",
        "    rw->getMetrics(width, height, depth, left, top);\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: no OIS::Mouse owns the state, we keep our own copy.\n"
        "    // This runs from the constructor, so a null dereference here would mean\n"
        "    // the input engine could never be constructed at all.\n"
        "    mouseState.width = width;\n"
        "    mouseState.height = height;\n"
        "#else\n"
        "    const OIS::MouseState& ms = mMouse->getMouseState();\n"
        "    ms.width = width;\n"
        "    ms.height = height;\n"
        "#endif\n",
        "InputEngine.cpp windowResized()",
    )

    text = must_replace(
        text,
        "void InputEngine::SetKeyboardListener(OIS::KeyListener* keyboard_listener)\n"
        "{\n"
        "    ROR_ASSERT(mKeyboard != nullptr);\n"
        "    mKeyboard->setEventCallback(keyboard_listener);\n"
        "}\n"
        "\n"
        "void InputEngine::SetMouseListener(OIS::MouseListener* mouse_listener)\n"
        "{\n"
        "    ROR_ASSERT(mMouse != nullptr);\n"
        "    mMouse->setEventCallback(mouse_listener);\n"
        "}\n",
        "void InputEngine::SetKeyboardListener(OIS::KeyListener* keyboard_listener)\n"
        "{\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: nothing to hand the callback to, remember it instead.\n"
        "    // (The assert() would be compiled out in Release, but the following call\n"
        "    // would then dereference null - ROR_ASSERT is a bare assert().)\n"
        "    mKeyboardListener = keyboard_listener;\n"
        "#else\n"
        "    ROR_ASSERT(mKeyboard != nullptr);\n"
        "    mKeyboard->setEventCallback(keyboard_listener);\n"
        "#endif\n"
        "}\n"
        "\n"
        "void InputEngine::SetMouseListener(OIS::MouseListener* mouse_listener)\n"
        "{\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    mMouseListener = mouse_listener;\n"
        "#else\n"
        "    ROR_ASSERT(mMouse != nullptr);\n"
        "    mMouse->setEventCallback(mouse_listener);\n"
        "#endif\n"
        "}\n",
        "InputEngine.cpp Set*Listener",
    )

    text = must_replace(
        text,
        "Ogre::Vector2 InputEngine::getMouseNormalizedScreenPos()\n"
        "{\n"
        "    OIS::MouseState const& mstate = mMouse->getMouseState();\n",
        "Ogre::Vector2 InputEngine::getMouseNormalizedScreenPos()\n"
        "{\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    OIS::MouseState const& mstate = mouseState;\n"
        "#else\n"
        "    OIS::MouseState const& mstate = mMouse->getMouseState();\n"
        "#endif\n",
        "InputEngine.cpp getMouseNormalizedScreenPos()",
    )

    # The touch -> mouse translation itself.  Appended at the end of the file so
    # it cannot disturb any other anchor.
    text += (
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "// Apple/iOS port -------------------------------------------------------\n"
        "// OIS reports touches as OISMultiTouch events on a device that has no\n"
        "// buttons and no pointer, so translate them into the mouse events RoR\n"
        "// already handles: MyGUI listens for those, and so does the camera.\n"
        "bool InputEngine::touchMoved(const OIS::MultiTouchEvent& arg)\n"
        "{\n"
        "    this->IosHandleTouch(OIS::MT_Moved, (float)arg.state.X.abs, (float)arg.state.Y.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "bool InputEngine::touchPressed(const OIS::MultiTouchEvent& arg)\n"
        "{\n"
        "    this->IosHandleTouch(OIS::MT_Pressed, (float)arg.state.X.abs, (float)arg.state.Y.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "bool InputEngine::touchReleased(const OIS::MultiTouchEvent& arg)\n"
        "{\n"
        "    this->IosHandleTouch(OIS::MT_Released, (float)arg.state.X.abs, (float)arg.state.Y.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "bool InputEngine::touchCancelled(const OIS::MultiTouchEvent& arg)\n"
        "{\n"
        "    this->IosHandleTouch(OIS::MT_Cancelled, (float)arg.state.X.abs, (float)arg.state.Y.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "void InputEngine::IosHandleTouch(int action, float x, float y)\n"
        "{\n"
        "    // Coordinates arrive in points, and so do the render window metrics,\n"
        "    // so no conversion is needed:\n"
        "    //   * OIS multiplies locationInView() by the contentScaleFactor of its\n"
        "    //     own delegate UIView, and iPhoneInputManager sets that from its\n"
        "    //     mContentScalingFactor, which stays 1.0 because we pass \"WINDOW\"\n"
        "    //     but not \"VIEW\" (iPhoneInputManager.mm:126,153-158).\n"
        "    //   * OGRE's EAGL2Window also starts at contentScalingFactor 1.0 and is\n"
        "    //     only changed by a \"contentScalingFactor\" misc parameter\n"
        "    //     (OgreEAGL2Window.mm:63,320), which RoR does not pass - so\n"
        "    //     mWidth/mHeight are points as well (OgreEAGL2Window.mm:276).\n"
        "    // If contentScalingFactor is ever passed to createRenderWindow(), this\n"
        "    // stops holding and the \"VIEW\" parameter must be given to OIS too.\n"
        "    const int new_x = (int)x;\n"
        "    const int new_y = (int)y;\n"
        "    mouseState.X.rel = new_x - mouseState.X.abs;\n"
        "    mouseState.Y.rel = new_y - mouseState.Y.abs;\n"
        "    mouseState.X.abs = new_x;\n"
        "    mouseState.Y.abs = new_y;\n"
        "\n"
        "    const BitMask_t btnmask = 1 << OIS::MB_Left;\n"
        "    const OIS::MouseEvent evt(nullptr, mouseState);\n"
        "\n"
        "    if (action == OIS::MT_Pressed)\n"
        "    {\n"
        "        BITMASK_SET_1(mouseState.buttons, btnmask);\n"
        "        if (mMouseListener) { mMouseListener->mousePressed(evt, OIS::MB_Left); }\n"
        "    }\n"
        "    else if (action == OIS::MT_Released || action == OIS::MT_Cancelled)\n"
        "    {\n"
        "        BITMASK_SET_0(mouseState.buttons, btnmask);\n"
        "        if (mMouseListener) { mMouseListener->mouseReleased(evt, OIS::MB_Left); }\n"
        "    }\n"
        "    else\n"
        "    {\n"
        "        if (mMouseListener) { mMouseListener->mouseMoved(evt); }\n"
        "    }\n"
        "}\n"
        "#endif // ROR_PLATFORM_IOS\n"
    )
    write(path, text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ror_dir")
    ap.add_argument("--ois-include", default="")
    ap.add_argument("--ois-lib", default="")
    ap.add_argument("--minimal-deps", action="store_true",
                    help="drop optional conan deps (stage-1 build)")
    args = ap.parse_args()

    if not os.path.isdir(args.ror_dir):
        sys.exit("ERROR: not a directory: %s" % args.ror_dir)

    patch_conanfile(args.ror_dir, minimal_deps=args.minimal_deps)
    patch_dependencies(args.ror_dir)
    patch_root_cmake(args.ror_dir)
    patch_main_cmake(args.ror_dir)
    # Platform layer: these fix things that are broken on iOS specifically,
    # independent of how the build is driven.
    patch_errorutils(args.ror_dir)
    patch_platformutils(args.ror_dir)
    patch_appcontext(args.ror_dir)
    # The resource directory is renamed to `rorres` (iOS refuses a bundle
    # carrying a top-level `resources` directory) - see patch_appcontext().
    patch_contentmanager(args.ror_dir)
    # Platform-specific headers/libs that simply do not exist with that
    # meaning on iOS.
    patch_soundmanager(args.ror_dir)
    patch_server_script_curl_helpers(args.ror_dir)
    # Missing guard holes for the supported "SocketW without libcurl" build,
    # which is the configuration we run on iOS.
    patch_network_header(args.ror_dir)
    patch_repo_selector(args.ror_dir)
    patch_multiplayer_selector(args.ror_dir)
    patch_game_settings_alc(args.ror_dir)
    patch_inputengine_header(args.ror_dir)
    patch_inputengine(args.ror_dir)
    # The runtime platform layer: the new iOS files themselves plus the two
    # entry-point/frame-loop changes in main.cpp that hand the process over to
    # UIKit.
    patch_ios_platform(args.ror_dir)
    patch_main_cpp(args.ror_dir)

    print("applied Apple/iOS patches to %s" % args.ror_dir)
    if args.ois_include:
        print("  OIS include: %s" % args.ois_include)
    if args.ois_lib:
        print("  OIS library: %s" % args.ois_lib)


if __name__ == "__main__":
    main()
