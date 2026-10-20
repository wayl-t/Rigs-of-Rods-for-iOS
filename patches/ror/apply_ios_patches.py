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
import shutil
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

// Appends a one-line-per-view dump of the key UIWindow's view hierarchy to
// ror-trace.log: each view's class, frame, hidden flag and
// userInteractionEnabled.  Purely diagnostic.  It answers the question that
// "taps do nothing" always raises - whether OIS's touch delegate view exists
// at all, how much of the screen it actually covers, and whether some other
// view sits on top of it and swallows the touches.
void RoR_IosDumpViewHierarchy(void);

// Makes OIS's iPhone touch delegate cover the whole window.
//
// OIS sizes that delegate from [[UIScreen mainScreen] applicationFrame], which
// is the *portrait* frame - 390x844 on an iPhone - and then adds it as a
// subview of OGRE's *landscape* UIWindow (844x390).  The delegate therefore
// only covers the left 390 points of the screen, and a tap on the right half
// is never delivered to RoR at all.  It also leaves the delegate under
// whatever else the window has added since (OGRE's view controller view), in
// which case nothing is delivered anywhere.
//
// OIS reads touch coordinates with [touch locationInView:thatView], i.e. in
// the delegate's own coordinate space; its frame origin is (0,0), so resizing
// it to the window bounds does not move a single reported coordinate - it
// only widens the area that reports them.  Returns true if an iOS
// "InputDelegate" view was found and resized.
int RoR_IosMakeTouchViewCoverWindow(void);

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

// Records a fatal-but-swallowed failure (an exception RoRMain caught and turned
// into a plain `return 0`) both into ror-trace.log, as a `[fatal]` line, and onto
// the screen - the latter because RoRMain returning leaves the app alive on a
// black screen, where there is nothing else to see.  RoR's own LOG() for that
// exception goes through OGRE's log manager, whose detail threshold can drop it,
// so this file is the only guaranteed record.
void RoR_IosShowFatal(const char* format, ...);

#ifdef __cplusplus
}
#endif

#endif // ROR_IOS_PLATFORM_H
'''

IOS_PLATFORM_MM = r'''// Rigs of Rods - Apple/iOS platform layer implementation.  See ios_platform.h.
#include "ios_platform.h"

#import <UIKit/UIKit.h>
#import <CoreFoundation/CFRunLoop.h>
#import <objc/runtime.h>

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

void RoR_IosShowFatal(const char* format, ...)
{
    ror_trace_open();

    char buffer[4096];
    va_list args;
    va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);

    // The record that survives anything: unbuffered, straight to the file.
    ror_trace_write("\n[fatal] ");
    ror_trace_write(buffer);
    ror_trace_write("\n");

    // And on screen.  RoRMain has returned at this point, so the process only
    // idles on a black window from here on and there is nothing else to read
    // on the device.  UIKit is main-thread only; the caller is RoRMain's own
    // catch block, which runs there.
    if (![NSThread isMainThread])
    {
        return;
    }

    @autoreleasepool
    {
        UIWindow* window = nil;
        for (UIWindow* candidate in [UIApplication sharedApplication].windows)
        {
            if (window == nil || candidate.isKeyWindow)
            {
                window = candidate;
            }
        }
        if (window == nil)
        {
            return;
        }

        UITextView* view = [[UITextView alloc] initWithFrame:window.bounds];
        view.backgroundColor = [UIColor colorWithWhite:0.05f alpha:1.0f];
        view.textColor = [UIColor colorWithRed:1.0f green:0.4f blue:0.4f alpha:1.0f];
        UIFont* font = [UIFont fontWithName:@"Menlo" size:11.0f];
        if (font == nil)
        {
            font = [UIFont systemFontOfSize:11.0f];
        }
        view.font = font;
        view.editable = NO;
        view.selectable = YES; // so the text can be selected and copied off the phone
        view.text = [NSString stringWithUTF8String:buffer];
        view.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        [window addSubview:view];
        [window bringSubviewToFront:view];
    }
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

void RoR_IosDumpViewHierarchy(void)
{
    UIApplication* app = [UIApplication sharedApplication];
    UIWindow* win = nil;
    for (UIWindow* w in [app windows])
    {
        if ([w isKeyWindow]) { win = w; break; }
    }
    if (win == nil && [[app windows] count] > 0)
    {
        win = [[app windows] objectAtIndex:0];
    }
    if (win == nil)
    {
        RoR_IosTrace("[RoR|Input] view hierarchy: no UIWindow yet");
        return;
    }

    RoR_IosTrace("[RoR|Input] window frame=(%.0f,%.0f,%.0f,%.0f) subviews=%d",
                 (double)win.frame.origin.x, (double)win.frame.origin.y,
                 (double)win.frame.size.width, (double)win.frame.size.height,
                 (int)[[win subviews] count]);

    for (UIView* v in [win subviews])
    {
        // class_getName() needs <objc/runtime.h> and gives the plain class
        // name, which is what tells OGRE's EAGL2View and OIS's InputDelegate
        // apart in the dump.
        RoR_IosTrace("[RoR|Input]   subview %s frame=(%.0f,%.0f,%.0f,%.0f) hidden=%d interact=%d firstResponder=%d",
                     class_getName([v class]),
                     (double)v.frame.origin.x, (double)v.frame.origin.y,
                     (double)v.frame.size.width, (double)v.frame.size.height,
                     (int)v.hidden, (int)v.userInteractionEnabled,
                     (int)[v isFirstResponder]);
    }
}

int RoR_IosMakeTouchViewCoverWindow(void)
{
    UIApplication* app = [UIApplication sharedApplication];
    UIWindow* win = nil;
    for (UIWindow* w in [app windows])
    {
        if ([w isKeyWindow]) { win = w; break; }
    }
    if (win == nil && [[app windows] count] > 0)
    {
        win = [[app windows] objectAtIndex:0];
    }
    if (win == nil)
    {
        RoR_IosTrace("[RoR|Input] touch view: no UIWindow yet, left as is");
        return 0;
    }

    UIView* touch_view = nil;
    for (UIView* v in [win subviews])
    {
        const char* name = class_getName([v class]);
        if (name != nullptr && strcmp(name, "InputDelegate") == 0)
        {
            touch_view = v;
            break;
        }
    }
    if (touch_view == nil)
    {
        RoR_IosTrace("[RoR|Input] touch view: no InputDelegate subview found, left as is");
        return 0;
    }

    const CGRect old_frame = touch_view.frame;
    touch_view.frame = win.bounds;
    touch_view.autoresizingMask =
        UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    touch_view.userInteractionEnabled = YES;
    touch_view.multipleTouchEnabled = YES;
    // OIS's delegate draws nothing, but UIView defaults to opaque = YES, and an
    // opaque view that never draws is allowed to hand UIKit an undefined
    // backdrop.  It has always been the *last* subview before now (so the GL
    // view was drawn over it); moving it to the front would expose that.  Make
    // it explicitly transparent first, then bring it forward.
    touch_view.opaque = NO;
    touch_view.backgroundColor = [UIColor clearColor];
    // The delegate is transparent and reads nothing but touch locations, so
    // putting it in front of OGRE's view changes only who receives touches -
    // and that is the whole point.
    [win bringSubviewToFront:touch_view];

    RoR_IosTrace("[RoR|Input] touch view: InputDelegate (%.0f,%.0f,%.0f,%.0f) -> (%.0f,%.0f,%.0f,%.0f), brought to front",
                 (double)old_frame.origin.x, (double)old_frame.origin.y,
                 (double)old_frame.size.width, (double)old_frame.size.height,
                 (double)touch_view.frame.origin.x, (double)touch_view.frame.origin.y,
                 (double)touch_view.frame.size.width, (double)touch_view.frame.size.height);
    return 1;
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

    # ---------------------------------------------------------------------
    # GUI message breadcrumbs
    # ---------------------------------------------------------------------
    # The main menu is *drawn* by ImGui but *enabled* by a message on RoR's own
    # queue.  If those messages are never handled - or are handled and then
    # undone - no ImGui window exists at all and the screen shows only the
    # wallpaper, however healthy the renderer is.  One line per transition
    # makes that readable in ror-trace.log instead of inferred.
    #
    # The close-handler anchors include their `case` label because
    # `MainSelector.Close()` and `GameMainMenu.SetVisible(false)` each appear
    # more than once in main.cpp.
    gui_handlers = (
        ("App::GetGuiManager()->GameMainMenu.SetVisible(true);\n",
         "#ifdef ROR_PLATFORM_IOS\n"
         '                        RoR_IosTrace("[RoR|GUI] handler OPEN_MENU: GameMainMenu.SetVisible(true), state=%d",\n'
         "                            (int)App::app_state->getEnum<AppState>());\n"
         "#endif\n",
         "MSG_GUI_OPEN_MENU_REQUESTED"),
        ("                case MSG_GUI_CLOSE_MENU_REQUESTED:\n"
         "                {\n"
         "                    try\n"
         "                    {\n"
         "                        App::GetGuiManager()->GameMainMenu.SetVisible(false);\n",
         "#ifdef ROR_PLATFORM_IOS\n"
         '                        RoR_IosTrace("[RoR|GUI] handler CLOSE_MENU: GameMainMenu.SetVisible(false), state=%d",\n'
         "                            (int)App::app_state->getEnum<AppState>());\n"
         "#endif\n",
         "MSG_GUI_CLOSE_MENU_REQUESTED"),
        ("App::GetGuiManager()->MainSelector.Show(*type, m.description);\n",
         "#ifdef ROR_PLATFORM_IOS\n"
         '                        RoR_IosTrace("[RoR|GUI] handler OPEN_SELECTOR: MainSelector.Show(type=%d), state=%d",\n'
         "                            (int)*type, (int)App::app_state->getEnum<AppState>());\n"
         "#endif\n",
         "MSG_GUI_OPEN_SELECTOR_REQUESTED"),
        ("                case MSG_GUI_CLOSE_SELECTOR_REQUESTED:\n"
         "                {\n"
         "                    try\n"
         "                    {\n"
         "                        App::GetGuiManager()->MainSelector.Close();\n",
         "#ifdef ROR_PLATFORM_IOS\n"
         '                        RoR_IosTrace("[RoR|GUI] handler CLOSE_SELECTOR: MainSelector.Close(), state=%d",\n'
         "                            (int)App::app_state->getEnum<AppState>());\n"
         "#endif\n",
         "MSG_GUI_CLOSE_SELECTOR_REQUESTED"),
    )
    for anchor, insertion, label in gui_handlers:
        found = text.count(anchor)
        if found != 1:
            raise SystemExit(
                "ERROR: main.cpp GUI handler anchor for %s found %d times, expected 1: %r"
                % (label, found, anchor))
        text = text.replace(anchor, anchor + insertion)

    # ---------------------------------------------------------------------
    # Do not let a caught exception turn into a silent black screen
    # ---------------------------------------------------------------------
    # The whole frame loop is wrapped in try/catch (Release builds only).  An
    # exception escaping it is swallowed here, RoRMain falls through to
    # `return 0`, and on iOS that does NOT end the process: -[RoRAppDelegate
    # didFinishLaunchingWithOptions:] already returned, so the main run loop
    # just keeps ticking with an empty queue.  The observed result is a black
    # screen that never changes - no crash, the app stays in the app switcher -
    # with the only clue buried in OGRE's own buffered log at a level the
    # detail threshold may drop.  Make it unmissable instead: one unbuffered
    # [fatal] line, plus the text on screen.
    text = must_replace(
        text,
        "    catch (Ogre::Exception& e)\n"
        "    {\n"
        "        LOG(e.getFullDescription());\n",
        "    catch (Ogre::Exception& e)\n"
        "    {\n"
        "        LOG(e.getFullDescription());\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '        RoR_IosShowFatal("RoRMain caught an Ogre::Exception and is returning:\\n%s",\n'
        "                         e.getFullDescription().c_str());\n"
        "#endif\n",
        "main.cpp report the caught Ogre::Exception on iOS",
    )

    text = must_replace(
        text,
        "    catch (std::runtime_error& e)\n"
        "    {\n"
        "        LOG(e.what());\n",
        "    catch (std::runtime_error& e)\n"
        "    {\n"
        "        LOG(e.what());\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '        RoR_IosShowFatal("RoRMain caught a std::runtime_error and is returning:\\n%s", e.what());\n'
        "#endif\n",
        "main.cpp report the caught std::runtime_error on iOS",
    )

    # The `return 0` below is reached both when the frame loop ends and when an
    # exception was caught.  A breadcrumb here says which of the two happened
    # even when no exception was involved.
    text = must_replace(
        text,
        "    return 0;\n"
        "}\n"
        "\n"
        "#if OGRE_PLATFORM == OGRE_PLATFORM_WIN32\n",
        "#ifdef ROR_PLATFORM_IOS\n"
        '    RoR_IosShowFatal("RoRMain is returning: the frame loop has ended.");\n'
        "#endif\n"
        "    return 0;\n"
        "}\n"
        "\n"
        "#if OGRE_PLATFORM == OGRE_PLATFORM_WIN32\n",
        "main.cpp breadcrumb on leaving RoRMain",
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
        "    void                IosHandleTouch(int action, float x, float y, int id = 0);\n"
        "    // Apple/iOS port: draws the on-screen touch controls and applies their state.\n"
        "    void                IosDrawTouchControls(float dt);\n"
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


# Apple/iOS port: on-screen touch controls ("virtual gamepad").
#
# The device has no keyboard, so RoR's default input map - hold UP to drive,
# WASD to walk - can never fire and the game sits waiting for keys.  This layer
# draws large touch regions and translates touches over them into the very
# events the keyboard would have produced, via InputEngine's simulated-value
# channel (setEventSimulatedValue), which getEventValue() returns in place of
# any key state - the same mechanism AngelScript and the AI already use to
# drive vehicles, so the simulation itself needs no change.
#
# Two constraints shaped it:
#   * OIS's MultiTouchEvent carries only X/Y - no finger id - so fingers cannot
#     be named.  We keep our own list of touches that began on a control and
#     match later move/release events to it by nearest position, which is
#     reliable because the controls are large, well-separated regions.
#   * The hit-test runs when a touch arrives; the drawing runs inside the ImGui
#     frame.  Both call IosLayout() - one pure function of the display size -
#     so what you touch is always exactly what you see.
#
# It is appended before the touch->mouse translation because IosHandleTouch()
# calls straight into it and C++ needs every helper declared before first use.
IOS_TOUCH_CONTROLS_HELPERS = r"""#ifdef ROR_PLATFORM_IOS
#include <imgui.h> // drawn with ImGui's foreground draw list
// App::GetGameContext()->GetPlayerActor() needs the complete GameContext type;
// nothing else in this translation unit pulls this header in.
#include "GameContext.h"

namespace {

// A touch that began on a control and keeps driving it until it lifts.
struct IosTouchPt { float x, y; int id; };
std::vector<IosTouchPt> s_iosCtlTouches;

// A one-shot ("tap") control: the event is held at 1 briefly so the game's
// bounce logic (getEventBoolValueBounce) sees exactly one press, then dropped.
struct IosPulse { RoR::events ev; float t; };
std::vector<IosPulse> s_iosPulses;

// Last applied analog values, mirrored for the on-screen markers.
float s_iosSteer = 0.f;   // -1 = full left .. +1 = full right
float s_iosJoyX  = 0.f;
float s_iosJoyY  = 0.f;   // -1 = up (forward) .. +1 = down (back)

// The vehicle-systems panel opened by the "VEH" button - open or closed.
bool s_iosPanel = false;

// IOS_PANEL: the VEH button itself - a pure UI toggle, drives no event.
// IOS_GUP / IOS_GDN: gear buttons that pulse TWO events each - both the
// sequential pair (EV_TRUCK_SHIFT_UP/DOWN) and the automatic pair
// (EV_TRUCK_AUTOSHIFT_UP/DOWN) - because Engine.cpp reads only the pair
// matching the current gearbox mode, and either mode may be active.
enum IosCtlKind { IOS_STEER, IOS_JOY, IOS_HELD, IOS_TOGGLE, IOS_PANEL, IOS_GUP, IOS_GDN };

struct IosCtl {
    RoR::events ev;      // the event this control drives (STEER/JOY drive several)
    IosCtlKind  kind;
    float x, y, w, h;    // fraction of the display, 0..1
    const char* label;
};

// Controls are only up while actually driving, and step aside whenever a menu
// or dialog owns the screen - those keep getting the touches themselves.
bool IosControlsActive()
{
    if (App::app_state->getEnum<AppState>() != AppState::SIMULATION)
        return false;
    if (App::GetGuiManager()->GameMainMenu.IsVisible())     return false;
    if (App::GetGuiManager()->MainSelector.IsVisible())     return false;
    if (App::GetGuiManager()->MessageBoxDialog.IsVisible()) return false;
    if (App::GetGuiManager()->LoadingWindow.IsVisible())    return false;
    return true;
}

bool IosInTruck()
{
    return App::GetGameContext()->GetPlayerActor() != nullptr;
}

// The display size ImGui is drawing into - the very space the touch coordinates
// arrive in (see IosHandleTouch's coordinate note), so the layout fractions
// line up with both sides.
void IosDisplaySize(float& W, float& H)
{
    const ImVec2 d = ImGui::GetIO().DisplaySize;
    W = (d.x > 1.f) ? d.x : 1.f;
    H = (d.y > 1.f) ? d.y : 1.f;
}

// The one true layout.  Pure function of the display size, so the touch
// hit-test and the ImGui draw can never disagree about where a button is.
void IosLayout(float W, float H, std::vector<IosCtl>& out)
{
    out.clear();
    if (IosInTruck())
    {
        // Steering: an absolute-position bar.  Left of centre = steer left,
        // right of centre = steer right, amount = distance from centre.
        out.push_back({RoR::EV_TRUCK_STEER_LEFT, IOS_STEER, 0.030f, 0.720f, 0.400f, 0.250f, "<  STEER  >"});
        // Pedals under the right thumb.
        out.push_back({RoR::EV_TRUCK_ACCELERATE, IOS_HELD,   0.845f, 0.360f, 0.150f, 0.300f, "GAS"});
        out.push_back({RoR::EV_TRUCK_BRAKE,      IOS_HELD,   0.845f, 0.680f, 0.150f, 0.300f, "BRK"});
        // A small utility row along the bottom, between steering and pedals.
        const float ux = 0.452f, uw = 0.070f, uy = 0.855f, uh = 0.125f;
        out.push_back({RoR::EV_TRUCK_PARKING_BRAKE,        IOS_TOGGLE, ux + 0.f * uw, uy, uw, uh, "P"});
        out.push_back({RoR::EV_TRUCK_TOGGLE_CONTACT,       IOS_TOGGLE, ux + 1.f * uw, uy, uw, uh, "IGN"});
        out.push_back({RoR::EV_COMMON_RESET_TRUCK,         IOS_TOGGLE, ux + 2.f * uw, uy, uw, uh, "R"});
        out.push_back({RoR::EV_COMMON_ENTER_OR_EXIT_TRUCK, IOS_TOGGLE, ux + 3.f * uw, uy, uw, uh, "EXIT"});
        out.push_back({RoR::EV_TRUCK_STARTER,              IOS_HELD,   ux + 4.f * uw, uy, uw, uh, "STRT"});
        // Turn signals and hazard flasher - the left-thumb zone above the bar.
        const float bx = 0.030f, bw = 0.115f, by = 0.560f, bh = 0.130f;
        out.push_back({RoR::EV_TRUCK_BLINK_LEFT,  IOS_TOGGLE, bx,                          by, bw, bh, "BL<"});
        out.push_back({RoR::EV_TRUCK_BLINK_WARN,  IOS_TOGGLE, bx + (bw + 0.010f),          by, bw, bh, "HAZ"});
        out.push_back({RoR::EV_TRUCK_BLINK_RIGHT, IOS_TOGGLE, bx + 2.f * (bw + 0.010f),    by, bw, bh, "BL>"});
        // The vehicle-systems panel switch, top-right above the pedals.
        // ("ev" is ignored for IOS_PANEL - the field only has to be filled.)
        out.push_back({RoR::EV_TRUCK_PARKING_BRAKE, IOS_PANEL, 0.845f, 0.050f, 0.150f, 0.240f, "VEH"});
        // The panel itself: a 5x4 grid over the empty middle of the screen
        // holding every remaining unit of the truck - lights, sound, gearbox,
        // differentials, transfer case, clutch, trailer brake, cruise control
        // and the truck's own command functions - shown while "VEH" is on.
        if (s_iosPanel)
        {
            struct IosPanelCell { RoR::events ev; IosCtlKind kind; const char* label; };
            static const IosPanelCell cells[] =
            {   // lights / sound / camera
                {RoR::EV_TRUCK_LIGHTTOGGLE01,             IOS_TOGGLE, "L1"},
                {RoR::EV_TRUCK_LIGHTTOGGLE02,             IOS_TOGGLE, "L2"},
                {RoR::EV_COMMON_TOGGLE_TRUCK_BEACONS,     IOS_TOGGLE, "BCN"},
                {RoR::EV_TRUCK_HORN,                      IOS_HELD,   "HRN"},
                {RoR::EV_CAMERA_CHANGE,                   IOS_TOGGLE, "CAM"},
                // gearbox: mode, up, down, neutral, reverse
                {RoR::EV_TRUCK_SWITCH_SHIFT_MODES,        IOS_TOGGLE, "A/M"},
                {RoR::EV_TRUCK_SHIFT_UP,                  IOS_GUP,    "G+"},
                {RoR::EV_TRUCK_SHIFT_DOWN,                IOS_GDN,    "G-"},
                {RoR::EV_TRUCK_SHIFT_NEUTRAL,             IOS_TOGGLE, "N"},
                {RoR::EV_TRUCK_SHIFT_GEAR_REVERSE,        IOS_TOGGLE, "RVS"},
                // drivetrain: differentials, transfer case, clutch, trailer brake
                {RoR::EV_TRUCK_TOGGLE_INTER_AXLE_DIFF,    IOS_TOGGLE, "AXD"},
                {RoR::EV_TRUCK_TOGGLE_INTER_WHEEL_DIFF,   IOS_TOGGLE, "WHD"},
                {RoR::EV_TRUCK_TOGGLE_TCASE_4WD_MODE,     IOS_TOGGLE, "T4W"},
                {RoR::EV_TRUCK_MANUAL_CLUTCH,             IOS_HELD,   "CLT"},
                {RoR::EV_TRUCK_TRAILER_PARKING_BRAKE,     IOS_TOGGLE, "TPK"},
                // cruise control and the truck's own command functions
                {RoR::EV_TRUCK_CRUISE_CONTROL,            IOS_TOGGLE, "CRZ"},
                {RoR::EV_TRUCK_CRUISE_CONTROL_ACCL,       IOS_HELD,   "CR+"},
                {RoR::EV_TRUCK_CRUISE_CONTROL_DECL,       IOS_HELD,   "CR-"},
                {RoR::EV_COMMANDS_01,                     IOS_HELD,   "C1"},
                {RoR::EV_COMMANDS_02,                     IOS_HELD,   "C2"},
            };
            const float px0 = 0.442f, pw = 0.070f, pgap  = 0.008f;
            const float py0 = 0.075f, ph = 0.140f, pgapy = 0.012f;
            for (size_t i = 0; i < sizeof(cells) / sizeof(cells[0]); ++i)
            {
                const float col = (float)(i % 5);
                const float row = (float)(i / 5);
                out.push_back({cells[i].ev, cells[i].kind,
                               px0 + col * (pw + pgap),
                               py0 + row * (ph + pgapy),
                               pw, ph, cells[i].label});
            }
        }
    }
    else
    {
        // On foot: a walking/turning joystick under the left thumb...
        out.push_back({RoR::EV_CHARACTER_FORWARD, IOS_JOY, 0.040f, 0.470f, 0.340f, 0.500f, "MOVE"});
        // ...and the actions under the right thumb.
        out.push_back({RoR::EV_CHARACTER_JUMP,             IOS_HELD,   0.800f, 0.420f, 0.190f, 0.170f, "JUMP"});
        out.push_back({RoR::EV_CHARACTER_RUN,              IOS_HELD,   0.800f, 0.600f, 0.190f, 0.170f, "RUN"});
        out.push_back({RoR::EV_COMMON_ENTER_OR_EXIT_TRUCK, IOS_TOGGLE, 0.800f, 0.780f, 0.190f, 0.200f, "ENTER"});
    }
}

bool IosPtIn(const IosCtl& c, float W, float H, float px, float py)
{
    return px >= c.x * W && px <= (c.x + c.w) * W
        && py >= c.y * H && py <= (c.y + c.h) * H;
}

// The whole panel area (its backdrop), in the same display fractions.
// Shared by the draw (background + title) and the touch path (stray taps
// inside are swallowed so they can't swing the camera behind the panel).
void IosPanelRect(float& x0, float& y0, float& x1, float& y1)
{
    x0 = 0.432f; y0 = 0.025f; x1 = 0.834f; y1 = 0.690f;
}

// The already-tracked control-touch nearest to (px,py), or -1.  The match
// radius is tied to the display, not to any one control, so a finger only
// ever pairs with a touch genuinely close by - and the controls never overlap.
int IosFindTouch(float W, float H, float px, float py, int id)
{
    // Exact finger identity first - if OIS carries the UITouch id, use it.
    // This is the authoritative answer when two fingers are down: the camera
    // finger must never be handed to a control just because it is the only
    // finger that control knows about.
    if (id != 0)
    {
        for (size_t i = 0; i < s_iosCtlTouches.size(); ++i)
        {
            if (s_iosCtlTouches[i].id == id)
                return (int)i;
        }
        // Known id, and it is not one of ours: a camera/ImGui finger.  Do not
        // fall back to geometry here - the fallback used to steal this finger
        // from the camera while the joystick (or a pedal) was still held, which
        // is what made the camera drag dead on a two-thumb hold.
        return -1;
    }
    const float rad  = 0.08f * ((W < H) ? W : H);
    const float rad2 = rad * rad;
    int   best = -1;
    float best_d = rad2;
    for (size_t i = 0; i < s_iosCtlTouches.size(); ++i)
    {
        const float dx = s_iosCtlTouches[i].x - px;
        const float dy = s_iosCtlTouches[i].y - py;
        const float d2 = dx * dx + dy * dy;
        if (d2 <= best_d) { best_d = d2; best = (int)i; }
    }
    // If no match within the radius but there's exactly one tracked control
    // touch, claim it - this prevents sticking after fast drags.  Only used
    // when ids are unavailable: with real ids a second finger must not be
    // allowed to join a control that is already driven by another finger.
    if (best < 0 && s_iosCtlTouches.size() == 1 && s_iosCtlTouches[0].id == 0)
        return 0;
    return best;
}

// Hold a toggle event at 1 for just long enough for one bounce to register.
void IosPulseEvent(RoR::events ev)
{
    App::GetInputEngine()->setEventSimulatedValue(ev, 1.0f);
    for (size_t i = 0; i < s_iosPulses.size(); ++i)
    {
        if (s_iosPulses[i].ev == ev) { s_iosPulses[i].t = 0.18f; return; }
    }
    s_iosPulses.push_back({ev, 0.18f});
}

// Track one touch event against the control regions.  Returns true when the
// touch belongs to a control and must NOT go on to the mouse/ImGui/camera path.
bool IosControlsHandleTouch(int action, float x, float y, int id)
{
    if (!IosControlsActive())
    {
        // The controls went away (a menu opened); drop any fingers they held so
        // nothing can stay pressed.
        s_iosCtlTouches.clear();
        return false;
    }

    float W, H; IosDisplaySize(W, H);
    std::vector<IosCtl> ctls;
    IosLayout(W, H, ctls);

    if (action == OIS::MT_Pressed)
    {
        // Only a touch that begins on a control becomes a control-finger; every
        // other touch keeps its old meaning (camera / ImGui widgets).
        for (size_t i = 0; i < ctls.size(); ++i)
        {
            if (!IosPtIn(ctls[i], W, H, x, y)) continue;
            if (ctls[i].kind == IOS_PANEL)
            {
                s_iosPanel = !s_iosPanel;      // the VEH switch: pure UI state
                return true;
            }
            if (ctls[i].kind == IOS_TOGGLE)
                IosPulseEvent(ctls[i].ev);
            else if (ctls[i].kind == IOS_GUP)
            {
                // Pulse both shift pairs: Engine.cpp only reads the one that
                // matches the current gearbox mode, the other is inert.
                IosPulseEvent(RoR::EV_TRUCK_SHIFT_UP);
                IosPulseEvent(RoR::EV_TRUCK_AUTOSHIFT_UP);
            }
            else if (ctls[i].kind == IOS_GDN)
            {
                IosPulseEvent(RoR::EV_TRUCK_SHIFT_DOWN);
                IosPulseEvent(RoR::EV_TRUCK_AUTOSHIFT_DOWN);
            }
            else
                s_iosCtlTouches.push_back({x, y, id});
            return true;
        }
        // With the panel open, a tap that missed every one of its buttons
        // belongs to the panel - swallow it instead of swinging the camera.
        if (s_iosPanel && IosInTruck())
        {
            float bx0, by0, bx1, by1;
            IosPanelRect(bx0, by0, bx1, by1);
            if (x >= bx0 * W && x <= bx1 * W && y >= by0 * H && y <= by1 * H)
                return true;
        }
        return false;
    }

    const int idx = IosFindTouch(W, H, x, y, id);
    if (action == OIS::MT_Moved)
    {
        if (idx < 0) return false;     // a camera/ImGui finger, not ours
        s_iosCtlTouches[idx].x = x;    // stays a control-finger for its whole life
        s_iosCtlTouches[idx].y = y;
        if (s_iosCtlTouches[idx].id == 0 && id != 0)
            s_iosCtlTouches[idx].id = id; // upgrade to exact identity on first move
        return true;
    }
    // Release or cancel.
    if (idx < 0)
    {
        // If the finger id is unknown but we failed to match by id/position, it
        // still likely belongs to a control finger (stuck tracking). Release the
        // only tracked touch when there's ambiguity to avoid permanent sticking.
        //
        // Only when ids are unavailable: with a real UITouch id a released
        // camera finger must not be allowed to tear down a control that a
        // *different* finger is still holding - that used to kill the joystick
        // (or the pedal) mid-press.
        if (id == 0 && s_iosCtlTouches.size() == 1 && s_iosCtlTouches[0].id == 0)
        {
            s_iosCtlTouches.clear();
            return true;
        }
        return false;
    }
    s_iosCtlTouches.erase(s_iosCtlTouches.begin() + idx);
    return true;
}

// Re-derive every continuously-held event from the tracked fingers.  Runs each
// frame, so it both applies pedal positions and - by zeroing the whole set
// first - clears anything left over when the player gets in or out of a truck.
void IosApplyHeldEvents(float W, float H, const std::vector<IosCtl>& ctls)
{
    InputEngine* ie = App::GetInputEngine();

    ie->setEventSimulatedValue(RoR::EV_TRUCK_ACCELERATE,    0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_BRAKE,         0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_STEER_LEFT,    0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_STEER_RIGHT,   0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_STARTER,       0.f);
    ie->setEventSimulatedValue(RoR::EV_CHARACTER_FORWARD,   0.f);
    ie->setEventSimulatedValue(RoR::EV_CHARACTER_BACKWARDS, 0.f);
    ie->setEventSimulatedValue(RoR::EV_CHARACTER_LEFT,      0.f);
    ie->setEventSimulatedValue(RoR::EV_CHARACTER_RIGHT,     0.f);
    ie->setEventSimulatedValue(RoR::EV_CHARACTER_RUN,       0.f);
    ie->setEventSimulatedValue(RoR::EV_CHARACTER_JUMP,      0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_HORN,                 0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_MANUAL_CLUTCH,        0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_CRUISE_CONTROL_ACCL,  0.f);
    ie->setEventSimulatedValue(RoR::EV_TRUCK_CRUISE_CONTROL_DECL,  0.f);
    ie->setEventSimulatedValue(RoR::EV_COMMANDS_01,                0.f);
    ie->setEventSimulatedValue(RoR::EV_COMMANDS_02,                0.f);
    s_iosSteer = 0.f;
    s_iosJoyX  = 0.f;
    s_iosJoyY  = 0.f;

    for (size_t i = 0; i < ctls.size(); ++i)
    {
        const IosCtl& c = ctls[i];
        // One-shots are handled by pulses; the panel and gear buttons never
        // carry an analog position or a level value.
        if (c.kind == IOS_TOGGLE || c.kind == IOS_PANEL ||
            c.kind == IOS_GUP    || c.kind == IOS_GDN) continue;

        // A control is live while some control-finger rests inside its region.
        bool  active = false;
        float tx = 0.f, ty = 0.f;
        for (size_t t = 0; t < s_iosCtlTouches.size(); ++t)
        {
            if (IosPtIn(c, W, H, s_iosCtlTouches[t].x, s_iosCtlTouches[t].y))
            {
                active = true;
                tx = s_iosCtlTouches[t].x;
                ty = s_iosCtlTouches[t].y;
                break;
            }
        }

        if (c.kind == IOS_STEER)
        {
            const float cx = (c.x + 0.5f * c.w) * W;
            const float hw = 0.5f * c.w * W;
            float s = active ? (tx - cx) / hw : 0.f;          // -1 .. +1
            const float dz = 0.12f;
            if (s > -dz && s < dz) s = 0.f;
            else s = (s > 0.f) ? (s - dz) / (1.f - dz) : (s + dz) / (1.f - dz);
            if (s < -1.f) s = -1.f;
            if (s > 1.f)  s = 1.f;
            s_iosSteer = s;
            if (s < 0.f) ie->setEventSimulatedValue(RoR::EV_TRUCK_STEER_LEFT, -s);
            else         ie->setEventSimulatedValue(RoR::EV_TRUCK_STEER_RIGHT, s);
        }
        else if (c.kind == IOS_JOY)
        {
            const float cx = (c.x + 0.5f * c.w) * W;
            const float cy = (c.y + 0.5f * c.h) * H;
            const float rx = 0.5f * c.w * W;
            const float ry = 0.5f * c.h * H;
            float jx = active ? (tx - cx) / rx : 0.f;
            float jy = active ? (ty - cy) / ry : 0.f;
            const float dz = 0.22f;
            if (jx > -dz && jx < dz) jx = 0.f;
            else jx = (jx > 0.f) ? (jx - dz) / (1.f - dz) : (jx + dz) / (1.f - dz);
            if (jy > -dz && jy < dz) jy = 0.f;
            else jy = (jy > 0.f) ? (jy - dz) / (1.f - dz) : (jy + dz) / (1.f - dz);
            if (jx < -1.f) jx = -1.f;
            if (jx > 1.f)  jx = 1.f;
            if (jy < -1.f) jy = -1.f;
            if (jy > 1.f)  jy = 1.f;
            s_iosJoyX = jx;
            s_iosJoyY = jy;
            if (jy < 0.f) ie->setEventSimulatedValue(RoR::EV_CHARACTER_FORWARD, -jy);
            if (jy > 0.f) ie->setEventSimulatedValue(RoR::EV_CHARACTER_BACKWARDS, jy);
            if (jx < 0.f) ie->setEventSimulatedValue(RoR::EV_CHARACTER_LEFT, -jx);
            if (jx > 0.f) ie->setEventSimulatedValue(RoR::EV_CHARACTER_RIGHT, jx);
        }
        else // IOS_HELD
        {
            ie->setEventSimulatedValue(c.ev, active ? 1.f : 0.f);
        }
    }
}

// The on-screen controls themselves, drawn on the foreground draw list so they
// stay readable over the 3D scene and the dashboard.
void IosDrawControls(float W, float H, const std::vector<IosCtl>& ctls)
{
    ImDrawList*  dl   = ImGui::GetForegroundDrawList();
    ImFont*      font = ImGui::GetFont();
    const float  fs   = ImGui::GetFontSize();

    // Labels ride at a fixed fraction of the (iOS: 2x) UI font - the cells
    // are laid out in screen fractions, so text must not outgrow its buttons
    // as the font grows.
    const float lfs = fs * 0.75f;

    // The vehicle-systems panel backdrop, under its buttons.
    if (s_iosPanel && IosInTruck())
    {
        float bx0, by0, bx1, by1;
        IosPanelRect(bx0, by0, bx1, by1);
        const ImVec2 q0(bx0 * W, by0 * H);
        const ImVec2 q1(bx1 * W, by1 * H);
        dl->AddRectFilled(q0, q1, IM_COL32(8, 10, 14, 190), 14.f, ImDrawCornerFlags_All);
        dl->AddRect(q0, q1, IM_COL32(255, 255, 255, 70), 14.f, ImDrawCornerFlags_All, 2.f);
        const char*  title = "VEHICLE";
        const float  tfs   = fs * 0.5f;
        const ImVec2 ts    = font->CalcTextSizeA(tfs, 1e6f, 0.f, title);
        dl->AddText(font, tfs, ImVec2((q0.x + q1.x - ts.x) * 0.5f, q0.y + 2.f),
                    IM_COL32(200, 215, 235, 235), title);
    }

    for (size_t i = 0; i < ctls.size(); ++i)
    {
        const IosCtl& c = ctls[i];
        const ImVec2 p0(c.x * W, c.y * H);
        const ImVec2 p1((c.x + c.w) * W, (c.y + c.h) * H);

        bool pressed = false;
        if (c.kind == IOS_PANEL)
        {
            pressed = s_iosPanel;            // the VEH switch lights while open
        }
        else if (c.kind == IOS_TOGGLE || c.kind == IOS_GUP || c.kind == IOS_GDN)
        {
            for (size_t p = 0; p < s_iosPulses.size(); ++p)
                if (s_iosPulses[p].ev == c.ev) { pressed = true; break; }
        }
        else
        {
            for (size_t t = 0; t < s_iosCtlTouches.size(); ++t)
                if (IosPtIn(c, W, H, s_iosCtlTouches[t].x, s_iosCtlTouches[t].y)) { pressed = true; break; }
        }

        const ImU32 fill   = pressed ? IM_COL32(30, 130, 255, 135) : IM_COL32(15, 15, 15, 95);
        const ImU32 border = pressed ? IM_COL32(130, 205, 255, 235) : IM_COL32(255, 255, 255, 125);
        dl->AddRectFilled(p0, p1, fill, 10.f, ImDrawCornerFlags_All);
        dl->AddRect(p0, p1, border, 10.f, ImDrawCornerFlags_All, 2.f);

        // Centred label.
        const ImVec2 ts = font->CalcTextSizeA(lfs, 1e6f, 0.f, c.label);
        const ImVec2 tp((p0.x + p1.x - ts.x) * 0.5f, (p0.y + p1.y - ts.y) * 0.5f);
        dl->AddText(font, lfs, tp, IM_COL32(255, 255, 255, 240), c.label);

        // Live marker for the two analog controls.
        if (c.kind == IOS_STEER)
        {
            const float mid  = (p0.x + p1.x) * 0.5f;
            const float mark = mid + s_iosSteer * 0.5f * (p1.x - p0.x);
            dl->AddLine(ImVec2(mid, p0.y + 5.f), ImVec2(mid, p1.y - 5.f),
                        IM_COL32(255, 255, 255, 80), 1.f);
            dl->AddCircleFilled(ImVec2(mark, (p0.y + p1.y) * 0.5f), 13.f,
                                IM_COL32(130, 205, 255, 235), 20);
        }
        else if (c.kind == IOS_JOY)
        {
            const float kx = (p0.x + p1.x) * 0.5f + s_iosJoyX * 0.5f * (p1.x - p0.x);
            const float ky = (p0.y + p1.y) * 0.5f + s_iosJoyY * 0.5f * (p1.y - p0.y);
            dl->AddCircleFilled(ImVec2((p0.x + p1.x) * 0.5f, (p0.y + p1.y) * 0.5f),
                                0.16f * (p1.y - p0.y), IM_COL32(255, 255, 255, 45), 24);
            dl->AddCircleFilled(ImVec2(kx, ky),
                                0.12f * (p1.y - p0.y), IM_COL32(130, 205, 255, 210), 24);
        }
    }
}

} // namespace
#endif // ROR_PLATFORM_IOS
"""

# The per-frame entry point GUIManager::DrawSimulationGui() calls: expires
# one-shot presses, folds the tracked fingers into this frame's input events,
# then draws the controls.  Declared in InputEngine.h under ROR_PLATFORM_IOS.
IOS_TOUCH_CONTROLS_DRAW = r"""#ifdef ROR_PLATFORM_IOS
void InputEngine::IosDrawTouchControls(float dt)
{
    // Let any one-shot ("tap") press expire first, whatever else is going on.
    for (size_t i = 0; i < s_iosPulses.size(); )
    {
        s_iosPulses[i].t -= dt;
        if (s_iosPulses[i].t <= 0.f)
        {
            this->setEventSimulatedValue(s_iosPulses[i].ev, 0.f);
            s_iosPulses.erase(s_iosPulses.begin() + i);
        }
        else
        {
            ++i;
        }
    }

    if (!IosControlsActive())
    {
        // Nothing is on screen: make sure no held event is left behind.
        const std::vector<IosCtl> empty;
        IosApplyHeldEvents(1.f, 1.f, empty);
        s_iosCtlTouches.clear();
        return;
    }

    // Leaving the truck takes the vehicle panel with it.
    if (!IosInTruck())
        s_iosPanel = false;

    float W, H; IosDisplaySize(W, H);
    std::vector<IosCtl> ctls;
    IosLayout(W, H, ctls);
    IosApplyHeldEvents(W, H, ctls);
    IosDrawControls(W, H, ctls);
}
#endif // ROR_PLATFORM_IOS
"""


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

    # The iOS block calls RoR_IosDumpViewHierarchy(), which lives in the
    # platform layer.  main.cpp includes the same header the same way.
    text = must_replace(
        text,
        '#include "InputEngine.h"\n'
        "\n"
        '#include "Actor.h"\n',
        '#include "InputEngine.h"\n'
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "ios_platform.h"\n'
        "#endif\n"
        "\n"
        '#include "Actor.h"\n',
        "InputEngine.cpp ios_platform.h include",
    )

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
        "        // One-line-per-view dump of the window OIS just put its touch\n"
        "        // delegate into, so a later \"taps do nothing\" report can be\n"
        "        // answered from the log instead of guessed at.  The dump runs\n"
        "        // BEFORE the fix below, so the log always shows the real frame\n"
        "        // OIS chose, not the corrected one.\n"
        "        RoR_IosDumpViewHierarchy();\n"
        "        // OIS sizes its delegate from the *portrait* screen frame and\n"
        "        // puts it in OGRE's *landscape* window, so the delegate only\n"
        "        // covers part of the screen - and sits under OGRE's own view.\n"
        "        // Stretch it over the window and bring it to the front; see\n"
        "        // RoR_IosMakeTouchViewCoverWindow() for why this cannot move a\n"
        "        // reported touch coordinate.\n"
        "        RoR_IosMakeTouchViewCoverWindow();\n"
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

    # The on-screen touch controls.  Appended before the touch -> mouse
    # translation because IosHandleTouch() calls straight into them and C++
    # needs every helper declared before its first use.
    text += IOS_TOUCH_CONTROLS_HELPERS

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
        "    this->IosHandleTouch(OIS::MT_Moved, (float)arg.state.X.abs, (float)arg.state.Y.abs, (int)arg.state.Z.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "bool InputEngine::touchPressed(const OIS::MultiTouchEvent& arg)\n"
        "{\n"
        "    this->IosHandleTouch(OIS::MT_Pressed, (float)arg.state.X.abs, (float)arg.state.Y.abs, (int)arg.state.Z.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "bool InputEngine::touchReleased(const OIS::MultiTouchEvent& arg)\n"
        "{\n"
        "    this->IosHandleTouch(OIS::MT_Released, (float)arg.state.X.abs, (float)arg.state.Y.abs, (int)arg.state.Z.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "bool InputEngine::touchCancelled(const OIS::MultiTouchEvent& arg)\n"
        "{\n"
        "    this->IosHandleTouch(OIS::MT_Cancelled, (float)arg.state.X.abs, (float)arg.state.Y.abs, (int)arg.state.Z.abs);\n"
        "    return true;\n"
        "}\n"
        "\n"
        "void InputEngine::IosHandleTouch(int action, float x, float y, int id)\n"
        "{\n"
        "    // The very first touch is the single most useful datum when someone\n"
        "    // reports that the UI does not react at all: it says whether touches\n"
        "    // reach RoR, or whether they die in the OIS/UIKit layer.  The string\n"
        "    // also doubles as the marker the packaging gate greps the binary for\n"
        "    // to prove this translation shipped.\n"
        "    static bool s_first_touch_traced = false;\n"
        "    static unsigned long s_touch_count = 0;\n"
        "    ++s_touch_count;\n"
        "    if (!s_first_touch_traced)\n"
        "    {\n"
        "        s_first_touch_traced = true;\n"
        "        LOG(\"[RoR|Input] first touch arrived - publishing the pointer position to ImGui\");\n"
        "    }\n"
        "    if ((s_touch_count % 5) == 1)\n"
        "    {\n"
        "        LOG(String(\"[RoR|Input] touch #\") + TOSTRING(s_touch_count) + \": action=\" + TOSTRING(action) + \", pos=(\" + TOSTRING((int)x) + \",\" + TOSTRING((int)y) + \")\");\n"
        "    }\n"
        "\n"
        "    // Apple/iOS port: touches that land on an on-screen control are\n"
        "    // consumed there (they become input events, not mouse motion); every\n"
        "    // other touch keeps its old meaning below - the camera and ImGui.\n"
        "    if (IosControlsHandleTouch(action, x, y, id))\n"
        "    {\n"
        "        return;\n"
        "    }\n"
        "\n"
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
        "    if (action != OIS::MT_Moved)\n"
        "    {\n"
        "        // A press or a release is not a move.  iOS reports where the finger\n"
        "        // landed but no delta, and measuring one from wherever the pointer\n"
        "        // happened to be before - another finger, or nothing at all - would\n"
        "        // hand the camera a flick it never asked for.\n"
        "        mouseState.X.rel = 0;\n"
        "        mouseState.Y.rel = 0;\n"
        "    }\n"
        "\n"
        "    const BitMask_t btnmask = 1 << OIS::MB_Left;\n"
        "    // The orbit camera only ever looks at the RIGHT mouse button:\n"
        "    //   CameraManager::CameraBehaviorOrbitMouseMoved() starts with\n"
        "    //   `if (ms.buttonDown(OIS::MB_Right))`, and it is what both the\n"
        "    //   truck camera and the third-person on-foot camera use.  A\n"
        "    //   touchscreen has no buttons and the touch -> mouse bridge can\n"
        "    //   only offer a left-button drag, so the camera never moved at all\n"
        "    //   on the phone.  A drag that is not on an on-screen control is\n"
        "    //   therefore reported as a right-button drag as well.  The camera is\n"
        "    //   the only consumer of MB_Right here, and ImGui still only ever\n"
        "    //   sees the left button (the listener calls below say MB_Left).\n"
        "    const BitMask_t rbtnmask = 1 << OIS::MB_Right;\n"
        "    const OIS::MouseEvent evt(nullptr, mouseState);\n"
        "\n"
        "    // Publish the position before the button, always.\n"
        "    //\n"
        "    // ImGui learns where the pointer is only from a move:\n"
        "    //   AppContext::mouseMoved -> OgreImGui::InjectMouseMoved -> io.MousePos\n"
        "    // (AppContext.cpp:80 is the one and only caller).  OIS's iPhone backend\n"
        "    // sends touchesBegan *without* a preceding touchesMoved - its\n"
        "    // _touchBegan emits MT_Pressed alone (iPhoneMultiTouch.mm) - so if this\n"
        "    // path is only taken for MT_Moved, io.MousePos keeps its initial\n"
        "    // (-FLT_MAX,-FLT_MAX), ImGui never hovers a widget, and a plain tap\n"
        "    // does nothing at all: the tap is then offered to the camera instead.\n"
        "    // Desktop OIS cannot show this because the OS pointer has a real\n"
        "    // position from the start; on a touchscreen the first event *is* the\n"
        "    // press.  Only the relative motion is suppressed above, so a press or a\n"
        "    // release moves the ImGui cursor and nothing else.\n"
        "    if (mMouseListener) { mMouseListener->mouseMoved(evt); }\n"
        "\n"
        "    if (action == OIS::MT_Pressed)\n"
        "    {\n"
        "        BITMASK_SET_1(mouseState.buttons, btnmask);\n"
        "        BITMASK_SET_1(mouseState.buttons, rbtnmask);\n"
        "        if (mMouseListener) { mMouseListener->mousePressed(evt, OIS::MB_Left); }\n"
        "    }\n"
        "    else if (action == OIS::MT_Released || action == OIS::MT_Cancelled)\n"
        "    {\n"
        "        BITMASK_SET_0(mouseState.buttons, btnmask);\n"
        "        BITMASK_SET_0(mouseState.buttons, rbtnmask);\n"
        "        if (mMouseListener) { mMouseListener->mouseReleased(evt, OIS::MB_Left); }\n"
        "    }\n"
        "}\n"
        "#endif // ROR_PLATFORM_IOS\n"
    )

    # Draw + apply the on-screen controls once per frame (GUIManager calls this).
    text += IOS_TOUCH_CONTROLS_DRAW

    write(path, text)


# MyGUI's own GLES2 shaders, under the names MyGUI 3.4.0 looks for.
#
# MyGUI::OgreRenderManager::initialise() (3.4.0) creates the GPU programs
# "MyGUI_VP.<lang>" and "MyGUI_FP.<lang>" and then loads each one from a source
# file of that same name, searching every OGRE resource group (RoR hands it
# AUTODETECT_RESOURCE_GROUP_NAME).  RoR's resources/mygui ships the pre-3.4
# names MyGUI_Ogre_VP.<lang> / MyGUI_Ogre_FP.<lang> instead, so on a render
# system without fixed function - which is every render system iOS has - MyGUI
# dies inside ResourceGroupManager::findGroupContainingResource with
#
#     ItemIdentityException: Unable to derive resource group for MyGUI_VP.glsles
#     automatically since the resource was not found.
#
# and main()'s catch swallows that into `return 0`, i.e. the black screen.
# Desktop never trips over it: there OGRE reports RSC_FIXED_FUNCTION and MyGUI
# skips the shader path completely (see the `if (!...RSC_FIXED_FUNCTION)` guard
# in MyGUI_OgreRenderManager.cpp), which is why the stale names survive
# upstream.
#
# These are MyGUI 3.4.0's own Media/MyGUI_Media shaders, not renamed copies of
# RoR's.  The two vertex programs are byte-identical, but RoR's fragment
# program is `#version 300 es` with `in` / `texture()` while its vertex program
# is `#version 100`, and a linked program may not mix GLSL ES versions.  MyGUI's
# pair is `#version 100` on both sides, which links on an ES2 context and is
# still accepted by an ES3 one.
MYGUI_VP_GLSLES = """#version 100

precision highp int;
precision highp float;

attribute vec4 position;
attribute vec4 uv0;
attribute vec4 colour;
uniform float YFlipScale;

varying vec4 outUV0;
varying vec4 outColor;

// Texturing vertex program for GLSL ES
void main()
{
\tvec4 vpos = position;
\tvpos.y *= YFlipScale;
\tgl_Position = vpos;
\toutUV0 = uv0;
\toutColor = colour;
}
"""

MYGUI_FP_GLSLES = """#version 100

precision highp int;
precision highp float;

uniform sampler2D sampler;

varying vec4 outUV0;
varying vec4 outColor;

// Texturing fragment program for GLSL ES
void main()
{
\tgl_FragColor = outColor * texture2D(sampler, outUV0.xy);
}
"""


def patch_rtshader(ror):
    """Let OGRE's RTShader System generate the shaders GLES2 insists on.

    GLES2 has no fixed-function pipeline and OGRE does not paper over that:
    ``SceneManager::_setPass`` throws

        InvalidStateException: RenderSystem does not support FixedFunction, but
        technique of 'rigsofrods/WallpaperMat' has no Vertex Shader. Use the
        RTSS or write custom shaders.

    the moment anything renders a material written without shaders.  This is
    not a corner case: **240 of RoR's 273 materials** are fixed-function,
    including all of ``OgreCore.material`` (fonts, overlay panels, the
    profiler), most of ``ror.material``, the paged grass and trees and every
    particle system.  Hand written shaders are out of the question at that
    scale, and user content brings materials we can never patch.

    The RTSS is OGRE's own answer to this: it reads a material's
    fixed-function description (texture units, blending, alpha rejection,
    fog, scene blending) and builds real GLSL ES programs from it at run time.
    Most of what is needed is already in place - OGRE 1.11 ships a GLSL ES
    writer (``OgreShaderGLSLESProgramWriter``), it parses the FFP library out
    of ``.glsl`` files, and RoR ships that library in ``resources/rtshader``
    (pack ``RtShaderRG``) and links ``OgreRTShaderSystem``
    (``find_package(OGRE COMPONENTS ... RTShaderSystem)``).  Two things were
    missing and are added here: the pack is never mounted (upstream declares
    it and never calls ``AddResourcePack`` for it - on desktop the library
    happens to come from OGRE's own installed media), and the RTSS bootstrap
    itself (see the AppContext section for its strict placement rules).

    Only fixed-function materials are converted.  ``createShaderBasedTechnique``
    refuses a material whose source technique is programmable, we then return
    NULL from the scheme listener, and OGRE falls back to the material's own
    technique - so switching the active scheme cannot regress anything that
    already renders (MyGUI, ImGui, the vehicle/terrain shaders).
    """
    # ---------------- AppContext.cpp: the listener, and turning the RTSS on -----
    path = os.path.join(ror, "source", "main", "AppContext.cpp")
    text = read(path)

    text = must_replace(
        text,
        "#include <iomanip>\n"
        "#include <sstream>\n"
        "#include <string>\n"
        "#include <ctime>\n",
        "#include <iomanip>\n"
        "#include <sstream>\n"
        "#include <string>\n"
        "#include <ctime>\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "#   include <RTShaderSystem/OgreRTShaderSystem.h>\n"
        "#endif\n",
        "AppContext.cpp: include the RTShader System (iOS only)",
    )

    text = must_replace(
        text,
        "        RoR_IosTrace(\"%s\", message.c_str());\n"
        "    }\n"
        "};\n"
        "#endif // ROR_PLATFORM_IOS\n",
        "        RoR_IosTrace(\"%s\", message.c_str());\n"
        "    }\n"
        "};\n"
        "#endif // ROR_PLATFORM_IOS\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "// Apple/iOS port: hand RTSS the materials that have no shaders, on demand.\n"
        "//\n"
        "// OGRE asks this listener once per draw when the material has no technique\n"
        "// in the active scheme ('NGOS').  A fixed-function material gets a\n"
        "// generated shader technique; anything else is left to OGRE's own\n"
        "// fallback, which is the material's default technique.\n"
        "class IosFixedFunctionMaterialListener : public Ogre::MaterialManager::Listener\n"
        "{\n"
        "public:\n"
        "    Ogre::Technique* handleSchemeNotFound(unsigned short schemeIndex,\n"
        "            const Ogre::String& schemeName, Ogre::Material* material,\n"
        "            unsigned short lodIndex, const Ogre::Renderable* rend) override\n"
        "    {\n"
        "        (void)schemeIndex;\n"
        "        (void)lodIndex;\n"
        "        (void)rend;\n"
        "\n"
        "        if (mBusy) // generation must not re-enter this listener\n"
        "            return nullptr;\n"
        "\n"
        "        Ogre::RTShader::ShaderGenerator& gen = Ogre::RTShader::ShaderGenerator::getSingleton();\n"
        "\n"
        "        if (!gen.createShaderBasedTechnique(*material,\n"
        "                    Ogre::MaterialManager::DEFAULT_SCHEME_NAME, schemeName))\n"
        "        {\n"
        "            return nullptr; // Not fixed-function - use the material as it is.\n"
        "        }\n"
        "\n"
        "        RoR_IosTrace(\"[rtss] generating shaders for fixed-function material '%s'\",\n"
        "            material->getName().c_str());\n"
        "        // Apple/iOS: log the pass state RTSS is about to read.  The\n"
        "        // generated shader for 'ImGui/material' declares no 'colour'\n"
        "        // attribute, so ImGui's per-vertex colour (window/button alpha and\n"
        "        // text tint) never reaches the GPU; FFPColour only wires up\n"
        "        // SF_VS_INPUT_DIFFUSE when srcPass->getVertexColourTracking() != 0\n"
        "        // (OgreShaderFFPColour.cpp, preAddToRenderState).  Sampling both the\n"
        "        // source and the freshly generated technique says whether the flag\n"
        "        // survives into the pass RTSS reads; without this the trace only\n"
        "        // shows the attribute warning and the reason stays guesswork.\n"
        "        for (unsigned short ios_ti = 0; ios_ti < material->getNumTechniques(); ++ios_ti)\n"
        "        {\n"
        "            Ogre::Technique* ios_tech = material->getTechnique(ios_ti);\n"
        "            if (ios_tech == nullptr || ios_tech->getNumPasses() == 0)\n"
        "                continue;\n"
        "            Ogre::Pass* ios_pass = ios_tech->getPass(0);\n"
        "            RoR_IosTrace(\"[rtss]   '%s' tech#%d scheme='%s' vcol=%d lighting=%d texunits=%d \"\n"
        "                \"depthcheck=%d depthwrite=%d cull=%d\",\n"
        "                material->getName().c_str(), (int)ios_ti,\n"
        "                ios_tech->getSchemeName().c_str(),\n"
        "                (int)ios_pass->getVertexColourTracking(),\n"
        "                (int)ios_pass->getLightingEnabled(),\n"
        "                (int)ios_pass->getNumTextureUnitStates(),\n"
        "                (int)ios_pass->getDepthCheckEnabled(),\n"
        "                (int)ios_pass->getDepthWriteEnabled(),\n"
        "                (int)ios_pass->getCullingMode());\n"
        "        }\n"
        "\n"
        "        // The generated technique is normally built on the next\n"
        "        // SGScheme::validate(), which for this frame has already run, so\n"
        "        // build it now rather than draw nothing for one frame.\n"
        "        //\n"
        "        // Use validateMaterial() and NOT invalidateMaterial()+\n"
        "        // validateScheme().  invalidateMaterial() sets the entry's\n"
        "        // 'build destination technique' flag unconditionally, so it re-runs\n"
        "        // SGTechnique::buildTargetRenderState() even when the destination\n"
        "        // technique already exists - and that path starts by calling\n"
        "        // Material::removeTechnique(), which does\n"
        "        // 'mSupportedTechniques.clear(); mCompilationRequired = true;'.\n"
        "        // Material::getBestTechnique() then returns NULL *before* it ever\n"
        "        // asks the scheme listener again, so nothing can repair it: the\n"
        "        // caller dereferences the NULL right away (seen on device at\n"
        "        // OgreImGuiOverlay.cpp:184 'getBestTechnique()->getPass(0)',\n"
        "        // crashing on the second draw of the material in the same frame).\n"
        "        //\n"
        "        // validateMaterial() only builds while the flag is set, i.e. exactly\n"
        "        // once, which is what OGRE's own SGTechniqueResolverListener does\n"
        "        // (Components/Bites/src/OgreSGTechniqueResolverListener.cpp).\n"
        "        mBusy = true;\n"
        "        gen.validateMaterial(schemeName, material->getName(), material->getGroup());\n"
        "\n"
        "        // Bullet proofing for the same trap: if the material still needs a\n"
        "        // compile (removeTechnique() ran - a light or fog change invalidated\n"
        "        // the scheme, or a previous build replaced the technique), rebuild\n"
        "        // its supported-technique list here.  compile() only re-evaluates\n"
        "        // technique support and refills mSupportedTechniques/\n"
        "        // mBestTechniquesBySchemeList; it creates no GPU programs.\n"
        "        if (material->getCompilationRequired())\n"
        "        {\n"
        "            RoR_IosTrace(\"[rtss] material '%s' needed a recompile after \"\n"
        "                \"the technique build\", material->getName().c_str());\n"
        "            material->compile(true);\n"
        "        }\n"
        "        mBusy = false;\n"
        "\n"
        "        for (Ogre::Technique* tech : material->getTechniques())\n"
        "        {\n"
        "            if (tech->getSchemeName() == schemeName)\n"
        "                return tech;\n"
        "        }\n"
        "        return nullptr;\n"
        "    }\n"
        "\n"
        "private:\n"
        "    bool mBusy = false;\n"
        "};\n"
        "\n"
        "void RoR::Ios::RtssRegisterSceneManager(Ogre::SceneManager* scene_manager)\n"
        "{\n"
        "    // Without this, RTSS never validates its scheme for this scene manager\n"
        "    // and the generated shaders are never built.  Called once per scene\n"
        "    // manager; RoR has a single one (GfxScene) which also renders overlays.\n"
        "    if (Ogre::RTShader::ShaderGenerator::getSingletonPtr() != nullptr)\n"
        "    {\n"
        "        Ogre::RTShader::ShaderGenerator::getSingleton().addSceneManager(scene_manager);\n"
        "    }\n"
        "}\n"
        "#endif // ROR_PLATFORM_IOS\n",
        "AppContext.cpp: fixed-function material listener + scene manager hook",
    )

    text = must_replace(
        text,
        "    this->SetRenderWindowIcon(m_render_window);\n"
        "    m_render_window->setActive(true);\n"
        "\n"
        "    // Create viewport (without camera)\n",
        "    this->SetRenderWindowIcon(m_render_window);\n"
        "    m_render_window->setActive(true);\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS: turn the RTShader System on here, between the render\n"
        "    // window and the first viewport.  Two hard constraints fix this spot:\n"
        "    //\n"
        "    //  * The GLES2 render system registers its GLSL ES high-level program\n"
        "    //    factory only when the FIRST RENDER WINDOW is created\n"
        "    //    (GLES2RenderSystem::createRenderWindow ->\n"
        "    //    initialiseFromRenderSystemCapabilities, OgreGLES2RenderSystem.cpp),\n"
        "    //    and ShaderGenerator picks its shader language from\n"
        "    //    HighLevelGpuProgramManager.  Initialising the RTSS any earlier\n"
        "    //    aborts with InternalErrorException 'None of the profiles is\n"
        "    //    supported' - reproduced on device before this block moved here.\n"
        "    //  * The scheme must be active before the viewport exists and before\n"
        "    //    any material is parsed or rendered.  The RTSS FFP library\n"
        "    //    ('rtshader/FFPLib_*.glsl' in RtShaderRG) is read lazily, when the\n"
        "    //    first shader is generated, so resource-group init order is free.\n"
        "    bool rtss_ok = false;\n"
        "    try\n"
        "    {\n"
        "        rtss_ok = Ogre::RTShader::ShaderGenerator::initialize();\n"
        "    }\n"
        "    catch (const Ogre::Exception& ex)\n"
        "    {\n"
        "        RoR_IosTrace(\"[rtss] ShaderGenerator::initialize() threw: %s\",\n"
        "            ex.getFullDescription().c_str());\n"
        "    }\n"
        "    if (rtss_ok)\n"
        "    {\n"
        "        Ogre::MaterialManager::getSingleton().addListener(\n"
        "            new IosFixedFunctionMaterialListener(),\n"
        "            Ogre::RTShader::ShaderGenerator::DEFAULT_SCHEME_NAME);\n"
        "        Ogre::MaterialManager::getSingleton().setActiveScheme(\n"
        "            Ogre::RTShader::ShaderGenerator::DEFAULT_SCHEME_NAME);\n"
        "        RoR_IosTrace(\"[rtss] RTShader System up, active material scheme '%s'\",\n"
        "            Ogre::RTShader::ShaderGenerator::DEFAULT_SCHEME_NAME.c_str());\n"
        "    }\n"
        "    else\n"
        "    {\n"
        "        RoR_IosTrace(\"[rtss] RTShader System FAILED to initialise - \"\n"
        "            \"materials without shaders will not render\");\n"
        "    }\n"
        "#endif\n"
        "\n"
        "    // Create viewport (without camera)\n",
        "AppContext.cpp: enable the RTShader System on iOS",
    )
    write(path, text)

    # ---------------- ios_platform.h: declare the hook ------------------------
    path = os.path.join(ror, "source", "main", "ios", "ios_platform.h")
    text = read(path)
    text = must_replace(
        text,
        "#ifdef __cplusplus\n"
        "}\n"
        "#endif\n"
        "\n"
        "#endif // ROR_IOS_PLATFORM_H\n",
        "#ifdef __cplusplus\n"
        "}\n"
        "\n"
        "namespace Ogre { class SceneManager; }\n"
        "\n"
        "namespace RoR\n"
        "{\n"
        "namespace Ios\n"
        "{\n"
        "    // Registers a scene manager with OGRE's RTShader System (AppContext.cpp)\n"
        "    // so that the shaders it generates for fixed-function materials are\n"
        "    // built for it too.  No-op when the RTSS is not running.\n"
        "    void RtssRegisterSceneManager(Ogre::SceneManager* scene_manager);\n"
        "}\n"
        "}\n"
        "#endif // __cplusplus\n"
        "\n"
        "#endif // ROR_IOS_PLATFORM_H\n",
        "ios_platform.h: declare RoR::Ios::RtssRegisterSceneManager",
    )
    write(path, text)

    # ---------------- GfxScene.cpp: register RoR's scene manager ---------------
    path = os.path.join(ror, "source", "main", "gfx", "GfxScene.cpp")
    text = read(path)
    text = must_replace(
        text,
        "#include \"AppContext.h\"\n",
        "#include \"AppContext.h\"\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "#   include \"ios_platform.h\"\n"
        "#endif\n",
        "GfxScene.cpp: include ios_platform.h",
    )
    text = must_replace(
        text,
        "    ROR_ASSERT(!m_scene_manager);\n"
        "    m_scene_manager = App::GetAppContext()->GetOgreRoot()->createSceneManager();\n",
        "    ROR_ASSERT(!m_scene_manager);\n"
        "    m_scene_manager = App::GetAppContext()->GetOgreRoot()->createSceneManager();\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS: this scene manager draws the world *and* the overlays,\n"
        "    // so RTSS needs it to build the shaders it generates for them.\n"
        "    RoR::Ios::RtssRegisterSceneManager(m_scene_manager);\n"
        "#endif\n",
        "GfxScene.cpp: register the scene manager with the RTSS",
    )
    write(path, text)

    # ---------------- ContentManager.cpp: mount the FFP library pack ---------
    path = os.path.join(ror, "source", "main", "resources", "ContentManager.cpp")
    text = read(path)
    text = must_replace(
        text,
        "    this->AddResourcePack(ResourcePack::MYGUI);\n"
        "    this->AddResourcePack(ResourcePack::DASHBOARDS);\n",
        "    this->AddResourcePack(ResourcePack::MYGUI);\n"
        "    this->AddResourcePack(ResourcePack::DASHBOARDS);\n"
        "\n"
        "    // Apple/iOS: upstream declares the RTShader System's FFP function\n"
        "    // library pack ('resources/rtshader' -> group RtShaderRG) but never\n"
        "    // mounts it - the cvar 'gfx_enable_rtshaders' is dead code as well.\n"
        "    // GLES2 has no fixed-function pipeline, so the iOS port runs the\n"
        "    // RTSS from AppContext and shader generation needs the pack's\n"
        "    // 'FFPLib_*.glsl'; without them the first generated shader dies with\n"
        "    // 'FileNotFoundException: Cannot locate resource FFPLib_Transform.glsl'\n"
        "    // (seen on device).  AddResourcePack() mounts and initialises the\n"
        "    // group in one step, well before the first frame.\n"
        "    this->AddResourcePack(ResourcePack::RTSHADER);\n",
        "ContentManager.cpp: mount the RTShader System FFP library pack",
    )
    write(path, text)


def patch_ogrecore_stdquad_glsles(ror):
    """Write the four GLSL ES sources `StdQuad_vp.program` asks for but nobody wrote.

    ``resources/OgreCore/StdQuad_vp.program`` defines a ``glsles`` variant for
    five vertex programs and a ``unified`` program delegating to it, but the
    repo only ever contained ``StdQuad_vp.glsles`` - OGRE's own media never had
    the other four, because nothing on desktop ever resolves a GLSL ES
    delegate.  iOS does, and every run since the first one ends with

        FileNotFoundException: StdQuad_Tex2_vp.glsles in resource group OgreCoreRG
        InvalidParametersException: Named constants have not been initialised

    in the trace: the script parser follows the ``unified`` program to its
    GLSLES delegate, the delegate's source file is not there, so it fails to
    load and the ``default_params`` block cannot be applied to it.

    Nothing references these programs, so this is not a bug - but it is two
    scary-looking errors in *every* trace from here on, and false alarms are
    expensive when a file off the device is the only window into the app
    (§7.25 was the same class of problem: the file simply was not there under
    the name the build asked for).

    The files are the ``StdQuad_Tex2_vp`` / ``StdQuad_Tex2a_vp`` /
    ``StdQuad_Tex3_vp`` / ``StdQuad_Tex4_vp`` entry points of ``StdQuad_vp.cg``
    translated to GLSL ES 1.00, in the shape of the ``StdQuad_vp.glsles`` that
    is already shipped.  The quad geometry and the uv derivation are identical
    in all five; they differ only in how many texture coordinates they hand to
    the fragment stage.
    """
    directory = os.path.join(ror, "resources", "OgreCore")
    os.makedirs(directory, exist_ok=True)

    # Shared preamble, identical to the existing StdQuad_vp.glsles.
    head = (
        "#version 100\n"
        "\n"
        "precision mediump int;\n"
        "precision mediump float;\n"
        "\n"
        "uniform mat4 worldViewProj;\n"
        "attribute vec4 vertex;\n"
    )
    # The uv every variant derives from the position, from StdQuad_vp.cg:
    #   inPos = sign(inPos.xy); uv0 = (vec2(inPos.x, -inPos.y) + 1) / 2
    body = (
        "\n"
        "void main()\n"
        "{\n"
        "    gl_Position = worldViewProj * vertex;\n"
        "\n"
        "    vec2 inPos = sign(vertex.xy);\n"
        "\n"
        "    uv0 = (vec2(inPos.x, -inPos.y) + 1.0) / 2.0;\n"
        "%s"
        "}\n"
    )

    variants = [
        # file, varying declarations, extra uv assignments
        ("StdQuad_Tex2_vp.glsles",
         "varying vec2 uv0;\nvarying vec2 uv1;\n",
         "    uv1 = uv0;\n"),
        # Tex2a is the odd one out: uv1 comes straight from the sign-normalised
        # position instead of following uv0.
        ("StdQuad_Tex2a_vp.glsles",
         "varying vec2 uv0;\nvarying vec2 uv1;\n",
         "    uv1 = inPos;\n"),
        ("StdQuad_Tex3_vp.glsles",
         "varying vec2 uv0;\nvarying vec2 uv1;\nvarying vec2 uv2;\n",
         "    uv1 = uv0;\n    uv2 = uv0;\n"),
        ("StdQuad_Tex4_vp.glsles",
         "varying vec2 uv0;\nvarying vec2 uv1;\nvarying vec2 uv2;\n"
         "varying vec2 uv3;\n",
         "    uv1 = uv0;\n    uv2 = uv0;\n    uv3 = uv0;\n"),
    ]

    for name, varyings, extra in variants:
        path = os.path.join(directory, name)
        write(path, head + varyings + body % extra)
        print("    wrote resources/OgreCore/%s (%d bytes)"
              % (name, os.path.getsize(path)))


def patch_gui_manager(ror):
    """GUIManager.cpp: do not read modifiers off a keyboard that is not there.

    ``NewImGuiFrame`` is the one place in the port that reaches straight for
    ``OIS::Keyboard*`` instead of going through ``InputEngine``, and on iOS
    that pointer is deliberately null: OIS's iPhone backend hands out a
    joystick and a multitouch device, nothing else (see
    ``patch_inputengine_header``).  ``isKeyDown`` is pure virtual in
    ``OIS::Keyboard``, so calling it through null is a vtable load from
    address 0.

    This is not a corner case.  main() asks for its very first ImGui frame
    immediately after the ``startup: entering the main menu state``
    breadcrumb, via the LoadingWindow "hack to properly init DearIMGUI"
    call, so the app died with

        [crash] SIGSEGV - invalid memory access addr=0x0
                pc=...(+0xfe65c) lr=...(+0xfe658)
        -> RoR::GUIManager::NewImGuiFrame(float) +0x30

    before the frame loop ever started - twice, at the same offset, on
    every launch.  Everything else in the input path was already guarded in
    patch_inputengine; this one was outside InputEngine and missed.

    Without a keyboard there are simply no modifier keys to report, so the
    three flags stay false.  It is written as an unguarded null check rather
    than an iOS ``#ifdef`` because the check is free either way and keeps the
    function identical across platforms.
    """
    path = os.path.join(ror, "source", "main", "gui", "GUIManager.cpp")
    text = read(path)

    text = must_replace(
        text,
        '#include "GUIManager.h"\n'
        "\n"
        '#include "Actor.h"\n',
        '#include "GUIManager.h"\n'
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "ios_platform.h"\n'
        "#endif\n"
        "\n"
        '#include "Actor.h"\n',
        "GUIManager.cpp ios_platform.h include",
    )

    # NB: upstream indents that comment with five spaces.  Keep it byte-exact.
    text = must_replace(
        text,
        "    ImGuiIO& io = ImGui::GetIO();\n"
        "    OIS::Keyboard* kb = App::GetInputEngine()->GetOisKeyboard();\n"
        "\n"
        "     // Read keyboard modifiers inputs\n"
        "    io.KeyCtrl = kb->isKeyDown(OIS::KC_LCONTROL);\n"
        "    io.KeyShift = kb->isKeyDown(OIS::KC_LSHIFT);\n"
        "    io.KeyAlt = kb->isKeyDown(OIS::KC_LMENU);\n"
        "    io.KeySuper = false;\n",
        "    ImGuiIO& io = ImGui::GetIO();\n"
        "    OIS::Keyboard* kb = App::GetInputEngine()->GetOisKeyboard();\n"
        "\n"
        "    // Read keyboard modifiers inputs\n"
        "    // Apple/iOS port: OIS's iPhone backend has no keyboard at all, so\n"
        "    // kb is null there (patch_inputengine).  isKeyDown is pure virtual\n"
        "    // in OIS::Keyboard, so calling it through null is a vtable load\n"
        "    // from address 0 - and main() asks for the first ImGui frame right\n"
        "    // after entering the main menu, which took the process down before\n"
        "    // the frame loop started.  No keyboard, no modifier keys: false.\n"
        "    io.KeyCtrl = (kb != nullptr) && kb->isKeyDown(OIS::KC_LCONTROL);\n"
        "    io.KeyShift = (kb != nullptr) && kb->isKeyDown(OIS::KC_LSHIFT);\n"
        "    io.KeyAlt = (kb != nullptr) && kb->isKeyDown(OIS::KC_LMENU);\n"
        "    io.KeySuper = false;\n",
        "GUIManager.cpp NewImGuiFrame null keyboard guard",
    )

    # Periodic "is there anything on screen" probe.  A user report of "I only
    # see the wallpaper, tapping does nothing" has three very different causes
    # and this one line separates them:
    #
    #  * the main menu widget is never marked visible -> there is no ImGui
    #    window at all, ImGui::Render() emits nothing, the overlay draws
    #    nothing.  (Also covers the app_skip_main_menu path, which opens
    #    MainSelector instead of GameMainMenu.)
    #  * the widget is visible but ImGui still emits nothing - the window was
    #    culled, got zero size, or ImGui::Begin() early-outed.
    #  * ImGui does emit geometry but the GPU never shows it (the RTSS drops
    #    the vertex colour for 'ImGui/material', the overlay is never queued
    #    because the active viewport is not the app viewport, ...).
    #
    # lastdraw() is the ImDrawData left over from the PREVIOUS frame's
    # ImGui::Render(), read here because this runs just before
    # ImGui::NewFrame() invalidates it (imgui.h: GetDrawData() is valid after
    # Render() and until the next NewFrame()).  cmds==0 means ImGui had no
    # windows to draw; the first draw command's clip rectangle says whether
    # the geometry is aimed at the screen.
    #
    # The previous, one-shot version of this probe was worthless: it fired
    # during the mod-cache load, because LoadingWindow::SetProgress() renders a
    # frame through this very function while main()'s
    # MSG_GUI_OPEN_MENU_REQUESTED is still queued behind
    # MSG_APP_MODCACHE_LOAD_REQUESTED (the queue is FIFO), so the menu was
    # always still closed and the reading always said "no".  Hence: first call,
    # then every 300.
    text = must_replace(
        text,
        "    io.KeySuper = false;\n"
        "\n"
        "    // Call IMGUI\n",
        "    io.KeySuper = false;\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    static int s_ios_gui_diag_n = 0;\n"
        "    ++s_ios_gui_diag_n;\n"
        "    if (s_ios_gui_diag_n == 1 || (s_ios_gui_diag_n % 300) == 0)\n"
        "    {\n"
        "        ImDrawData* dd = ImGui::GetDrawData();\n"
        "        int dd_cmds = -1;\n"
        "        int dd_vtx = -1;\n"
        "        float cl0 = 0.f;\n"
        "        float cl1 = 0.f;\n"
        "        float cl2 = 0.f;\n"
        "        float cl3 = 0.f;\n"
        "        if (dd != nullptr)\n"
        "        {\n"
        "            dd_cmds = dd->CmdListsCount;\n"
        "            dd_vtx = dd->TotalVtxCount;\n"
        "            if (dd->CmdListsCount > 0 && dd->CmdLists[0]->CmdBuffer.Size > 0)\n"
        "            {\n"
        "                const ImVec4& r = dd->CmdLists[0]->CmdBuffer[0].ClipRect;\n"
        "                cl0 = r.x;\n"
        "                cl1 = r.y;\n"
        "                cl2 = r.z;\n"
        "                cl3 = r.w;\n"
        "            }\n"
        "        }\n"
        "        RoR_IosTrace(\"[RoR|GUI] n=%d display=(%d,%d) lastdraw(cmds=%d,vtx=%d,clip=%.0f,%.0f,%.0f,%.0f)\"\n"
        "            \" menu=%d selector=%d loading=%d msgbox=%d menubar=%d wallpaper=%d state=%d\",\n"
        "            s_ios_gui_diag_n, (int)io.DisplaySize.x, (int)io.DisplaySize.y,\n"
        "            dd_cmds, dd_vtx, cl0, cl1, cl2, cl3,\n"
        "            (int)this->GameMainMenu.IsVisible(),\n"
        "            (int)this->MainSelector.IsVisible(),\n"
        "            (int)this->LoadingWindow.IsVisible(),\n"
        "            (int)this->MessageBoxDialog.IsVisible(),\n"
        "            (int)this->TopMenubar.IsVisible(),\n"
        "            (int)(this->MenuWallpaper != nullptr && this->MenuWallpaper->isVisible()),\n"
        "            (int)App::app_state->getEnum<AppState>());\n"
        "    }\n"
        "#endif\n"
        "\n"
        "    // Call IMGUI\n",
        "GUIManager.cpp NewImGuiFrame periodic menu/draw diagnostic",
    )

    # Apple/iOS port: draw + apply the on-screen touch controls once per frame.
    # This lands at the tail of DrawSimulationGui, which main() calls every
    # simulation frame after NewImGuiFrame() and before ImGui::Render() (the
    # latter runs from OgreImGuiOverlay::ImGUIRenderable::preRender), so the
    # additions are picked up by the very frame that requested them.  The
    # function early-outs when no controls are on screen, so this is a no-op on
    # platforms without the controls and whenever a menu owns the screen.
    text = must_replace(
        text,
        "    if (this->FlexbodyDebug.IsVisible())\n"
        "    {\n"
        "        this->FlexbodyDebug.Draw();\n"
        "    }\n"
        "\n"
        "\n"
        "};\n",
        "    if (this->FlexbodyDebug.IsVisible())\n"
        "    {\n"
        "        this->FlexbodyDebug.Draw();\n"
        "    }\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: draw the virtual steering/pedals/joystick and fold\n"
        "    // the current finger state into this frame's input events.\n"
        "    App::GetInputEngine()->IosDrawTouchControls(dt);\n"
        "#endif\n"
        "\n"
        "};\n",
        "GUIManager.cpp DrawSimulationGui iOS touch controls",
    )

    # Widget metrics for the phone, kept in step with the font that
    # patch_ogreimgui_overlay rasterizes (anchor verified against
    # the pinned tree: line after style.WindowBorderSize).
    text = must_replace(
        text,
        "    style.WindowBorderSize      = 0.f;\n"
        "\n"
        "    App::GetGfxScene()->GetSceneManager()->addRenderQueueListener(&m_imgui);\n",
        "    style.WindowBorderSize      = 0.f;\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: draw the UI at the desktop size - 1.0x.  The\n"
        "    // earlier 1.5x/2x scale made every panel too big on the device; this\n"
        "    // is the single knob for the whole ImGui UI and it is kept in step\n"
        "    // with the font scale in patch_ogreimgui_overlay (OgreImGuiOverlay.cpp\n"
        "    // addFont) so panels keep the proportions the upstream design used.\n"
        "    style.ScaleAllSizes(1.0f);\n"
        "    // No system cursor on a touchscreen - hide ImGui's software cursor.\n"
        "    // NOTE: SetupImGui() has no local `io` (that lives in NewImGuiFrame),\n"
        "    // so the IO block has to be fetched here explicitly.\n"
        "    ImGui::GetIO().MouseDrawCursor = false;\n"
        "#endif\n"
        "\n"
        "    App::GetGfxScene()->GetSceneManager()->addRenderQueueListener(&m_imgui);\n",
        "GUIManager.cpp SetupImGui iOS ScaleAllSizes",
    )

    write(path, text)


def patch_character_material(ror):
    """resources/materials/character.material: drop the ColorChange pass on iOS.

    ``tracks/character`` derives from ``RoR/Managed_Mats/Base``, and that
    inheritance is what supplies the technique (``Shadows/managed/base_receiver``)
    and therefore the RTSS shader generation every other material in the scene
    uses.  So the file is edited surgically - only the offending pass is
    removed - instead of being rewritten, which would silently change the
    shading path along with the fix.

    The pass that has to go is ``ColorChange``:

        pass ColorChange
        {
            scene_blend alpha_blend
            texture_unit { texture character-alpha.png }
            texture_unit { colour_op_ex blend_current_alpha src_manual src_current 0 0 0 }
        }

    Its job is to tint the body with the player's chosen colour, and it does it
    with fixed-function texture blending.  GLES2 has no fixed function at all -
    the RTSS translation of ``colour_op_ex ... src_manual 0 0 0`` multiplies the
    whole mesh by (0,0,0), which is exactly the solid black character seen on
    the device (the nearest black object always wins the depth test, so the
    body reads as a black silhouette).  The tint is cosmetic; losing it is the
    fix.
    """
    path = os.path.join(ror, "resources", "materials", "character.material")
    text = read(path)

    color_pass = (
        "\t\tpass ColorChange\n"
        "\t\t{\n"
        "\t\t\tscene_blend alpha_blend\n"
        "\t\t\ttexture_unit\n"
        "\t\t\t{\n"
        "\t\t\t\ttexture character-alpha.png\n"
        "\t\t\t}\n"
        "\t\t\ttexture_unit\n"
        "\t\t\t{\n"
        "\t\t\t\tcolour_op_ex blend_current_alpha src_manual src_current 0 0 0\n"
        "\t\t\t}\n"
        "\t\t}\n"
    )
    text = must_replace(text, color_pass, "", "character.material: ColorChange pass")
    write(path, text)


def patch_gamechatbox(ror):
    """source/main/gui/panels/GUI_GameChatBox.cpp: iOS-friendly spawn hint.

    On the desktop the hint reads "Press Ctrl+G to spawn a vehicle".  There is
    no keyboard on the device, so that line is both useless and misleading -
    the phone spawns from the on-screen buttons.

    Note the anchor is the *whole* fmt::format call including its line break:
    the upstream source wraps `App::GetInputEngine()...` onto the next line with
    19 spaces of indentation, and matching only the first line would leave a
    half-replaced statement behind.
    """
    path = os.path.join(ror, "source", "main", "gui", "panels", "GUI_GameChatBox.cpp")
    text = read(path)

    # The anchor is the whole putMessage() statement, not just the fmt::format
    # call: the third argument of putMessage is the message and the fourth is
    # the icon, and a #ifdef/#endif pair cannot sit in the middle of an
    # argument list.  Replacing only the message expression left the trailing
    # `, "lightbulb.png")` dangling after #endif, which does not compile.
    old = (
        '        App::GetConsole()->putMessage(Console::CONSOLE_MSGTYPE_INFO, Console::CONSOLE_SYSTEM_NOTICE,\n'
        '                                      fmt::format(_LC("ChatBox", "Press {} to spawn a vehicle"),\n'
        '                   App::GetInputEngine()->getEventCommandTrimmed(EV_COMMON_GET_NEW_VEHICLE)), "lightbulb.png");\n'
    )
    new = (
        '        App::GetConsole()->putMessage(Console::CONSOLE_MSGTYPE_INFO, Console::CONSOLE_SYSTEM_NOTICE,\n'
        '#ifdef ROR_PLATFORM_IOS\n'
        '                                      std::string("Use the on-screen buttons"), "lightbulb.png");\n'
        '#else\n'
        '                                      fmt::format(_LC("ChatBox", "Press {} to spawn a vehicle"),\n'
        '                   App::GetInputEngine()->getEventCommandTrimmed(EV_COMMON_GET_NEW_VEHICLE)), "lightbulb.png");\n'
        '#endif\n'
    )
    text = must_replace(text, old, new, "GUI_GameChatBox.cpp: spawn hint")
    write(path, text)


def patch_character_diagnostic(ror):
    """Character.cpp: report what the character is actually drawn with.

    The body is drawn black on the device and the only way to tell why is to
    see the numbers.  This prints, once per spawned character:

    * the mesh's own bounding box, captured *before* SetupGfx() replaces it
      with an infinite one - the real size says whether the 0.02 node scale
      is doing anything, and how big the body is supposed to be;
    * whether the mesh carries a skeleton, how many bones it has and how
      many bone weights a vertex can carry - a skinned mesh whose skinning
      the renderer cannot honour renders as garbage geometry;
    * the material's technique and pass count - the number that proves the
      ColorChange removal in patch_character_material() actually landed.
    """
    path = os.path.join(ror, "source", "main", "gameplay", "Character.cpp")
    text = read(path)

    anchor = (
        "    Entity* entity = App::GetGfxScene()->GetSceneManager()->createEntity("
        "m_instance_name + \"_mesh\", \"character.mesh\");\n"
    )
    probe = (
        "    Entity* entity = App::GetGfxScene()->GetSceneManager()->createEntity("
        "m_instance_name + \"_mesh\", \"character.mesh\");\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    {\n"
        "        Ogre::MaterialPtr mat1 = MaterialManager::getSingleton().getByName(\"tracks/character\");\n"
        "        const Ogre::AxisAlignedBox& bb = entity->getMesh()->getBoundingBox();\n"
        "        const int n_tech = mat1.get() ? (int)mat1->getNumTechniques() : -1;\n"
        "        const int n_pass = (mat1.get() && n_tech > 0) ? (int)mat1->getTechnique(0)->getNumPasses() : -1;\n"
        "        LOGSTREAM << \"[RoR|iOS] character '\" << m_instance_name.c_str()\n"
        "            << \"' bbox_min=(\" << bb.getMinimum().x << \",\" << bb.getMinimum().y << \",\" << bb.getMinimum().z << \")\"\n"
        "            << \" bbox_max=(\" << bb.getMaximum().x << \",\" << bb.getMaximum().y << \",\" << bb.getMaximum().z << \")\"\n"
        "            << \" skeleton=\" << (entity->getMesh()->hasSkeleton() ? 1 : 0)\n"
        "            << \" skeleton_name='\" << entity->getMesh()->getSkeletonName().c_str() << \"'\"\n"
        "            << \" bones=\" << (entity->getMesh()->hasSkeleton() ? entity->getMesh()->getSkeleton()->getNumBones() : 0)\n"
        "            << \" bone_weights=\" << entity->getMesh()->getNumBoneWeights()\n"
        "            << \" vcol=\" << (entity->getMesh()->hasVertexColors() ? 1 : 0)\n"
        "            << \" material='\" << (mat1.get() ? mat1->getName().c_str() : \"none\") << \"'\"\n"
        "            << \" techniques=\" << n_tech << \" passes=\" << n_pass\n"
        '            << "\\n";\n'
        "    }\n"
        "#endif\n"
    )
    text = must_replace(text, anchor, probe, "Character.cpp: character material/skeleton trace")
    write(path, text)


def patch_shadows_off_ambient(ror):
    """resources/managed_materials/shadows/pssm/off/shadows.material: light it.

    On iOS the shadow type is forced to NONE (see patch_cvar_shadow_default()),
    so ContentManager mounts ``shadows/pssm/off`` instead of ``shadows/pssm/on``
    and every material that derives from ``RoR/Managed_Mats/Base`` inherits
    ``Shadows/managed/base_receiver`` from there.

    The "on" variant of that abstract technique says:

        pass BaseRender
        {
                ambient 1 1 1 1
                diffuse 1 1 1 1
                ... shadow textures and programs ...
        }

    while the "off" variant - the one that is actually used on the device -
    has an *empty* pass.  OGRE's default ambient is black, so every material
    built on that technique (the on-foot character, for one) is rendered with
    zero ambient and comes out solid black on the phone.  The textures are
    fine; the lighting constants are missing.

    So the fix is to give the no-shadow variant the same ambient/diffuse the
    shadow variant has, minus everything shadow-specific.  Nothing else about
    the technique changes.
    """
    path = os.path.join(ror, "resources", "managed_materials", "shadows",
                        "pssm", "off", "shadows.material")
    text = read(path)

    old = (
        "abstract technique Shadows/managed/base_receiver\n"
        "{\n"
        "\tpass BaseRender\n"
        "\t{\n"
    )
    new = (
        "abstract technique Shadows/managed/base_receiver\n"
        "{\n"
        "\tpass BaseRender\n"
        "\t{\n"
        "\t\t\tambient 1 1 1 1\n"
        "\t\t\tdiffuse 1 1 1 1\n"
    )
    text = must_replace(text, old, new, "shadows/pssm/off: ambient/diffuse")
    write(path, text)


def patch_ogreimgui(ror):
    """OgreImGui.cpp: trace whether the ImGui overlay ever reaches the queue.

    ``OgreImGui::renderQueueStarted()`` is the *only* thing that puts the ImGui
    overlay into the render queue, and it does so under a pointer-identity test
    against the app viewport:

        if (vp == App::GetAppContext()->GetViewport())
            m_imgui_overlay->_findVisibleObjects(...)

    The wallpaper is drawn by OGRE's own ``OverlaySystem``, which has no such
    test, so if this one ever fails the screen shows the wallpaper and nothing
    else - which is exactly the user report ("I only see the background").
    That failure is invisible in a trace today, so this adds a counter of how
    often the overlay render-queue group is entered and how often the overlay
    is actually queued, plus the state of every input to that decision, sampled
    once and then every 300 overlay-group entries.

    It also marks ``Init()``: if the trace shows no such line at all, ImGui's
    glue never ran, which is a different failure entirely.
    """
    path = os.path.join(ror, "source", "main", "gui", "imgui", "OgreImGui.cpp")
    text = read(path)

    text = must_replace(
        text,
        "#include <imgui.h>\n"
        "#include <Ogre.h>\n",
        "#include <imgui.h>\n"
        "#include <Ogre.h>\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "ios_platform.h"\n'
        "#endif\n",
        "OgreImGui.cpp ios_platform.h include",
    )

    text = must_replace(
        text,
        "    m_imgui_overlay->setZOrder(300);\n"
        "    m_imgui_overlay->initialise(); // Build font texture\n"
        "    m_imgui_overlay->show();\n",
        "    m_imgui_overlay->setZOrder(300);\n"
        "    m_imgui_overlay->initialise(); // Build font texture\n"
        "    m_imgui_overlay->show();\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '    RoR_IosTrace("[RoR|GUI] ImGui glue initialised: overlay zorder=300, shown");\n'
        "#endif\n",
        "OgreImGui.cpp Init() breadcrumb",
    )

    text = must_replace(
        text,
        "    if(queueGroupId == Ogre::RENDER_QUEUE_OVERLAY)\n"
        "    {\n"
        "        Ogre::Viewport* vp = Ogre::Root::getSingletonPtr()->getRenderSystem()->_getViewport();\n",
        "    if(queueGroupId == Ogre::RENDER_QUEUE_OVERLAY)\n"
        "    {\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "        // See the header of patch_ogreimgui: this listener is the only\n"
        "        // thing that queues the ImGui overlay, and it does so under a\n"
        "        // pointer-identity test against the app viewport.\n"
        "        static int s_rq_overlay = 0;\n"
        "        static int s_rq_queued = 0;\n"
        "        ++s_rq_overlay;\n"
        "#endif\n"
        "        Ogre::Viewport* vp = Ogre::Root::getSingletonPtr()->getRenderSystem()->_getViewport();\n",
        "OgreImGui.cpp renderQueueStarted counters",
    )

    text = must_replace(
        text,
        "                if (vp == vp_target)\n"
        "                {\n"
        "                    m_imgui_overlay->_findVisibleObjects(vp->getCamera(), sceneMgr->getRenderQueue(), vp);\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n",
        "                if (vp == vp_target)\n"
        "                {\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "                    ++s_rq_queued;\n"
        "#endif\n"
        "                    m_imgui_overlay->_findVisibleObjects(vp->getCamera(), sceneMgr->getRenderQueue(), vp);\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "        if (s_rq_overlay == 1 || (s_rq_overlay % 300) == 0)\n"
        "        {\n"
        "            Ogre::Viewport* vp_now = Ogre::Root::getSingletonPtr()->getRenderSystem()->_getViewport();\n"
        "            Ogre::Viewport* vp_app = App::GetAppContext()->GetViewport();\n"
        '            RoR_IosTrace("[RoR|GUI] rq: overlay_entries=%d queued=%d vp_now_null=%d vp_app_null=%d vp_same=%d "\n'
        '                "overlays_enabled=%d camera_null=%d",\n'
        "                s_rq_overlay, s_rq_queued,\n"
        "                (int)(vp_now == nullptr), (int)(vp_app == nullptr), (int)(vp_now == vp_app),\n"
        "                (int)(vp_now != nullptr && vp_now->getOverlaysEnabled()),\n"
        "                (int)(vp_now != nullptr && vp_now->getCamera() == nullptr));\n"
        "        }\n"
        "#endif\n"
        "    }\n"
        "}\n",
        "OgreImGui.cpp renderQueueStarted queue counter + sample",
    )

    write(path, text)


def patch_ogreimgui_overlay(ror):
    """OgreImGuiOverlay.cpp: upload the shader parameters before drawing ImGui.

    This is the reason the menu was invisible on the device, and it is worth
    writing down in full, because nothing about it is specific to ImGui.

    ``ImGUIRenderable::preRender()`` draws the ImGui command lists *itself* (a
    renderable has to, because ImGui needs a scissor rect and a texture per
    command) and returns ``false`` so that OGRE will not also draw it.  But
    ``SceneManager::_issueRenderOp()`` looks like this::

        if (rend->preRender(this, mDestRenderSystem))
        {
            if (pass) updateGpuProgramParameters(pass);   // <-- uploads the
            rend->getRenderOperation(ro);                 //     shader params
            mDestRenderSystem->_render(ro);
        }

    so returning ``false`` means ``updateGpuProgramParameters()`` is never
    called for this renderable - and that method is the only thing in OGRE that
    calls ``GpuProgramParameters::_updateAutoParams()`` and
    ``bindGpuProgramParameters()``.  On the desktop that is harmless: RoR's
    ImGui material has no shader there, so the matrices reach the GPU through
    the fixed-function state that ``SceneManager::setWorldTransform()`` sets.

    On GLES2 there is no fixed-function pipeline, the RTSS emulates it, and the
    generated vertex shader gets its ``worldViewProj`` from the auto-parameter
    system - which now never ran for this renderable.  The shader therefore kept
    the parameters of whichever renderable was drawn before it: an overlay
    element of the wallpaper or the loading screen, both of which have identity
    view/projection *and* identity world matrix, because OGRE's overlay geometry
    is already in clip space.

    ImGui's vertices are in pixels (its ``mXform`` is the projection that maps
    them to clip space, and that is what ``getWorldTransforms()`` hands OGRE).
    Multiplied by a stale identity matrix, a pixel coordinate *is* a clip
    coordinate, so the entire menu - x in [0, 844] - collapses into the single
    pixel-wide sliver x in [0, 1] at the far left edge.  Hence: geometry
    generated, command lists queued, hit-testing working, and the screen showing
    nothing but the wallpaper.

    The fix is to do exactly what OGRE would have done for us if ``preRender()``
    returned ``true``.  All three calls below are public (``SceneManager::
    _getAutoParamDataSource()``, ``GpuProgramParameters::_updateAutoParams()``
    and ``RenderSystem::bindGpuProgramParameters()`` are ``_``-prefixed but
    live in public sections, and ``OgreCompositorInstance.cpp`` already uses the
    same pair), so no OGRE patch and no OGRE rebuild is needed.
    ``mCurrentRenderable`` is already this renderable - ``renderSingleObject()``
    sets it before ``_issueRenderOp()`` is reached - so the identity view and
    projection are picked up and ``worldViewProj`` comes out as ``mXform``.

    The same function also carries the phone's font size: ``addFont()`` is
    the single place a glyph rasterization size enters the ImGui atlas, and
    on a 844x390-point screen read at arm's length the desktop size is
    unreadable - while ImGui cannot scale a glyph after it was rasterized.
    ``GUIManager::SetupImGui`` scales the widget metrics by the same factor.
    """
    path = os.path.join(ror, "source", "main", "gui", "imgui",
                        "OgreImGuiOverlay.cpp")
    text = read(path)

    text = must_replace(
        text,
        "#include <OgreRoot.h>\n",
        "#include <OgreRoot.h>\n"
        "#include <OgreSceneManager.h>\n"
        "#include <OgreGpuProgram.h>\n"
        "#include <OgreGpuProgramParams.h>\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        '#   include "ios_platform.h"\n'
        "#endif\n",
        "OgreImGuiOverlay.cpp includes for the shader-parameter refresh",
    )

    text = must_replace(
        text,
        "    TextureUnitState* tu = mMaterial->getBestTechnique()->getPass(0)->getTextureUnitState(0);\n"
        "\n"
        "    for (int i = 0; i < draw_data->CmdListsCount; ++i)\n",
        "    TextureUnitState* tu = mMaterial->getBestTechnique()->getPass(0)->getTextureUnitState(0);\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // See the header of patch_ogreimgui_overlay(): this renderable draws itself\n"
        "    // and returns false, so OGRE never calls updateGpuProgramParameters() for\n"
        "    // it.  With the RTSS emulating the fixed-function pipeline the generated\n"
        "    // vertex shader would then transform our pixel-space vertices with the\n"
        "    // previous renderable's (identity) matrices and put the whole menu outside\n"
        "    // the viewport.  Refresh the auto parameters ourselves, exactly like\n"
        "    // SceneManager does for a renderable whose preRender() returns true.\n"
        "    // mCurrentRenderable is already this renderable, and it has\n"
        "    // getUseIdentityView()/getUseIdentityProjection() set, so the result is\n"
        "    // worldViewProj == mXform - the pixel to clip space projection.\n"
        "    const Pass* iosPass = mMaterial->getBestTechnique()->getPass(0);\n"
        "    const uint16 iosParamMask = (uint16)(GPV_GLOBAL | GPV_PER_OBJECT);\n"
        "    for (int iosTypeI = 0; iosTypeI < GPT_COUNT; ++iosTypeI)\n"
        "    {\n"
        "        GpuProgramType iosType = static_cast<GpuProgramType>(iosTypeI);\n"
        "        if (!iosPass->hasGpuProgram(iosType))\n"
        "            continue;\n"
        "        const GpuProgramParametersSharedPtr& iosParams =\n"
        "            iosPass->getGpuProgramParameters(iosType);\n"
        "        if (!iosParams)\n"
        "            continue;\n"
        "        iosParams->_updateAutoParams(sm->_getAutoParamDataSource(), iosParamMask);\n"
        "        rsys->bindGpuProgramParameters(iosType, iosParams, iosParamMask);\n"
        "    }\n"
        "    static bool s_ios_imgui_params_logged = false;\n"
        "    if (!s_ios_imgui_params_logged)\n"
        "    {\n"
        "        s_ios_imgui_params_logged = true;\n"
        '        RoR_IosTrace("[RoR|GUI] ImGui overlay: refreshed the shader auto params "\n'
        '            "(vs=%d fs=%d mask=%d), mUseIdentityView=%d mUseIdentityProjection=%d",\n'
        "            (int)iosPass->hasGpuProgram(GPT_VERTEX_PROGRAM),\n"
        "            (int)iosPass->hasGpuProgram(GPT_FRAGMENT_PROGRAM),\n"
        "            (int)iosParamMask, (int)getUseIdentityView(),\n"
        "            (int)getUseIdentityProjection());\n"
        "    }\n"
        "#endif\n"
        "\n"
        "    for (int i = 0; i < draw_data->CmdListsCount; ++i)\n",
        "OgreImGuiOverlay.cpp refresh shader auto params before drawing",
    )

    # Font size for the phone: the atlas is rasterized at the final size, so
    # this is where "readable on a 844x390-point screen" has to happen - see
    # the comment inside the replacement.  GUIManager::SetupImGui scales the
    # widget metrics to the same factor.
    text = must_replace(
        text,
        "    ImFontConfig cfg;\n"
        "    strncpy(cfg.Name, name.c_str(), 40);\n"
        "    return io.Fonts->AddFontFromMemoryTTF(ttfchunk.getPtr(), (int)ttfchunk.size(), font->getTrueTypeSize(), &cfg,\n"
        "                                          cprangePtr);\n"
        "}\n",
        "    ImFontConfig cfg;\n"
        "    strncpy(cfg.Name, name.c_str(), 40);\n"
        "    // Apple/iOS port: rasterize the font at the desktop size - 1.0x.  The\n"
        "    // earlier 1.5x/2x scale made the text too big on the device; ImGui\n"
        "    // cannot scale an already-rasterized glyph, so the atlas has to be built\n"
        "    // at the final size.  GUIManager::SetupImGui scales the widget metrics\n"
        "    // with the same factor (style.ScaleAllSizes), so every panel keeps the\n"
        "    // proportions its upstream design used.\n"
        "    float font_size = (float)font->getTrueTypeSize();\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    font_size *= 1.0f;\n"
        "#endif\n"
        "    return io.Fonts->AddFontFromMemoryTTF(ttfchunk.getPtr(), (int)ttfchunk.size(), font_size, &cfg,\n"
        "                                          cprangePtr);\n"
        "}\n",
        "OgreImGuiOverlay.cpp addFont iOS size",
    )

    write(path, text)


def patch_fetchicon(ror):
    """GUIUtils.cpp: fetch an icon from the resource group that actually has it.

    ``FetchIcon()`` is how every ImGui panel gets a small PNG - the vehicle
    dashboard, the survey map, the repository selector, the multiplayer flags
    and the main menu notice all go through it.  Upstream asks for all of them
    in the group ``"FlagsRG"``, but that group only holds ``resources/flags``
    (the country flags).  The UI icons live in ``resources/icons``
    (``IconsRG``), and two names - ``control_pause.png`` / ``control_play.png``,
    drawn by the multiplayer actor list - exist only in ``resources/famicons``
    (``FamiconsRG``).

    The wrong group cannot be recovered from further down either:
    ``Texture::readImage()`` opens the image with::

        ResourceGroupManager::getSingleton().openResource(name, mGroup, this);

    which is the overload that hard-codes ``searchGroupsIfNotFound = false``
    (``OgreResourceGroupManager.h``), so OGRE searches that one group only and
    throws ``FileNotFoundException`` when the file lives elsewhere.  Ask OGRE
    which mounted group owns the file, and fall back to OGRE warning texture
    when no group has it (an empty ``TexturePtr`` is a null dereference in
    every caller - they all do ``tex->getHandle()`` unconditionally).

    Both outcomes - found and missing - are cached per name so a caller that
    redraws every frame (the console posts one icon per message, every frame)
    does not repeat the group search and re-throw thousands of times.  Before
    the cache, the console hint icon "lightbulb.png" (shipped in no mounted
    group) produced 3306 identical throw+catch pairs in a single device
    session.
    """
    path = os.path.join(ror, "source", "main", "gui", "GUIUtils.cpp")
    text = read(path)

    text = must_replace(
        text,
        '#include "imgui_internal.h" // ImTextCharFromUtf8\n'
        "#include <regex>\n"
        "#include <stdio.h> // sscanf\n",
        '#include "imgui_internal.h" // ImTextCharFromUtf8\n'
        "#include <OgreResourceGroupManager.h> // resourceExistsInAnyGroup()\n"
        "#include <map>\n"
        "#include <regex>\n"
        "#include <stdio.h> // sscanf\n",
        "GUIUtils.cpp OgreResourceGroupManager include",
    )

    text = must_replace(
        text,
        'Ogre::TexturePtr RoR::FetchIcon(const char* name)\n'
        '{\n'
        '    try\n'
        '    {\n'
        '        Ogre::TexturePtr tex = Ogre::static_pointer_cast<Ogre::Texture>(\n'
        '            Ogre::TextureManager::getSingleton().createOrRetrieve(name, "FlagsRG").first);\n'
        '        \n'
        '        // Load the texture now to catch FileNotFoundException early. This is more efficient than\n'
        '        // lazy loading because: (1) the texture will be loaded on first use anyway, and\n'
        '        // (2) catching exceptions here is safer than during ImGui rendering.\n'
        '        if (!tex->isLoaded())\n'
        '        {\n'
        '            tex->load();\n'
        '        }\n'
        '        return tex;\n'
        '    }\n'
        '    catch (...) \n'
        '    {\n'
        '        return Ogre::TexturePtr(); // null\n'
        '    }\n'
        '}\n',
        'Ogre::TexturePtr RoR::FetchIcon(const char* name)\n'
        '{\n'
        '    // Apple/iOS port: cache every resolved icon - found or missing - so a\n'
        '    // caller that redraws each frame does not repeat the resource-group\n'
        '    // search and re-throw thousands of times (see the docstring above).\n'
        '    static std::map<std::string, Ogre::TexturePtr> s_cachedIcons;\n'
        '    std::map<std::string, Ogre::TexturePtr>::iterator it = s_cachedIcons.find(name);\n'
        '    if (it != s_cachedIcons.end())\n'
        '    {\n'
        '        return it->second;\n'
        '    }\n'
        '\n'
        '    Ogre::TexturePtr result;\n'
        '    try\n'
        '    {\n'
        '        // Upstream asked for every icon in "FlagsRG", but the UI icons live in\n'
        '        // "IconsRG" (and two in "FamiconsRG").  Texture::readImage() opens with\n'
        '        // openResource(name, mGroup, this) - searchGroupsIfNotFound=false - so a\n'
        '        // wrong group throws instead of searching elsewhere.  Ask OGRE which\n'
        '        // mounted group owns the file.\n'
        '        Ogre::ResourceGroupManager& rgm = Ogre::ResourceGroupManager::getSingleton();\n'
        '        std::string group = "IconsRG";\n'
        '        if (rgm.resourceExistsInAnyGroup(name))\n'
        '        {\n'
        '            group = rgm.findGroupContainingResource(name);\n'
        '        }\n'
        '        else\n'
        '        {\n'
        '            RoR::Log((std::string("[RoR|FetchIcon] no resource group contains ") + name\n'
        '                + ", using the warning texture").c_str());\n'
        '        }\n'
        '\n'
        '        result = Ogre::static_pointer_cast<Ogre::Texture>(\n'
        '            Ogre::TextureManager::getSingleton().createOrRetrieve(name, group).first);\n'
        '        if (!result->isLoaded())\n'
        '        {\n'
        '            result->load();\n'
        '        }\n'
        '    }\n'
        '    catch (...)\n'
        '    {\n'
        '        // An empty TexturePtr is a null dereference in every caller - they all\n'
        '        // do tex->getHandle() unconditionally.  Hand back the OGRE warning\n'
        '        // texture: a real handle, always.\n'
        '        RoR::Log((std::string("[RoR|FetchIcon] could not load icon ") + name\n'
        '            + ", using the warning texture").c_str());\n'
        '        result = Ogre::TextureManager::getSingleton()._getWarningTexture();\n'
        '    }\n'
        '\n'
        '    s_cachedIcons[name] = result;\n'
        '    return result;\n'
        '}\n',
        "GUIUtils.cpp FetchIcon body (any-group lookup + per-name cache)",
    )

    write(path, text)


def patch_console_view(ror):
    """GUI_ConsoleView.cpp: draw console icons through RoR::FetchIcon().

    The console draws a per-message icon and asked for all of them with::

        Ogre::TextureManager::load(name, "IconsRG")

    a hard-coded group.  The fixed names (script.png, information.png,
    error.png, cancel.png, comment.png) do live in resources/icons, but the
    dynamic per-message icon (``cm_icon`` - e.g. the "lightbulb.png" hint the
    chat box posts on entry) is not shipped in any mounted group, so every
    redraw threw ``FileNotFoundException`` that the surrounding try/catch
    swallowed.  With the console open that is one throw+catch per frame: the
    device trace for a single session carries 3306 identical

        Cannot locate resource lightbulb.png in resource group IconsRG.

    lines.  ``RoR::FetchIcon()`` (patched by :func:`patch_fetchicon`) checks
    which group actually owns a file before loading and caches the result, so
    routing the console through it removes the per-frame exception storm and
    the hard-coded group.  GUI_ConsoleView.cpp already includes "GUIUtils.h",
    so FetchIcon() is in scope.
    """
    path = os.path.join(ror, "source", "main", "gui", "panels",
                        "GUI_ConsoleView.cpp")
    text = read(path)

    text = must_replace(
        text,
        '                icon = Ogre::TextureManager::getSingleton().load(m.cm_icon, "IconsRG");\n',
        '                // Apple/iOS port: FetchIcon() picks the group that owns the file and\n'
        '                // caches the result, instead of hard-coding "IconsRG" and throwing\n'
        '                // once per frame for icons shipped nowhere (e.g. lightbulb.png).\n'
        '                icon = RoR::FetchIcon(m.cm_icon.c_str());\n',
        "GUI_ConsoleView.cpp cm_icon load",
    )

    for name in ("script.png", "information.png", "error.png",
                 "cancel.png", "comment.png"):
        text = must_replace(
            text,
            '            icon = Ogre::TextureManager::getSingleton().load("%s", "IconsRG");\n' % name,
            '            icon = RoR::FetchIcon("%s");\n' % name,
            "GUI_ConsoleView.cpp %s load" % name,
        )

    write(path, text)


def patch_mygui_resources(ror):
    """Add the resources the GUI would otherwise fail to find.

    1. MyGUI_VP.glsles / MyGUI_FP.glsles - see the comment above.  CMake's
       zip_folder_resources packs resources/mygui into rorres/mygui.zip, so
       dropping the two files here is all that is needed.
    2. resources/flags/ror.png.  GUI_RepositorySelector asks for "ror.png" from
       FlagsRG (it is the fallback thumbnail for a missing preview), while the
       file only exists as resources/icons/ror.png.  That lookup fails on every
       launch and logs a FileNotFoundException; one copy makes it succeed.
    """
    gui_dir = os.path.join(ror, "resources", "mygui")
    if not os.path.isdir(gui_dir):
        sys.exit("ERROR: resources/mygui is missing from the RoR checkout")

    for name, text in (("MyGUI_VP.glsles", MYGUI_VP_GLSLES),
                       ("MyGUI_FP.glsles", MYGUI_FP_GLSLES)):
        path = os.path.join(gui_dir, name)
        if os.path.exists(path) and read(path) == text:
            print("  resources/mygui/%s already correct" % name)
            continue
        write(path, text)
        print("  wrote resources/mygui/%s (%d bytes)" % (name, len(text)))

    icons_ror = os.path.join(ror, "resources", "icons", "ror.png")
    flags_ror = os.path.join(ror, "resources", "flags", "ror.png")
    if not os.path.exists(flags_ror):
        if os.path.exists(icons_ror):
            shutil.copyfile(icons_ror, flags_ror)
            print("  copied resources/icons/ror.png -> resources/flags/ror.png")
        else:
            print("  WARNING: resources/icons/ror.png is missing, "
                  "FlagsRG will still fail to resolve ror.png")


# The RTSS function library is *data*, and it is version locked to the library
# that generates the shaders: the sub-render-states call FFP_*/SGX_* functions
# with hard coded parameter types, and OgreShaderGLSLESProgramWriter matches
# those calls against the signatures it parses out of these files with
# FunctionInvocation::operator==, which compares the function name, the return
# type, the operand count and every operand's semantic and *type*.
#
# RoR ships an RTSS library from 2009 (see the copyright headers in
# resources/rtshader/*.glsl: "Copyright (c) 2000-2009"), i.e. the OGRE 1.6/1.7
# set, while this port links OGRE 1.11.6's OgreRTShaderSystem.  The signatures
# no longer agree, and the mismatch is not academic:
#
#   RoR 2009                    OGRE 1.11.6 (what the generator calls)
#   SGX_FetchNormal(... out vec3 vOut)        out vec4 vOut
#   SGX_Light_*_*(in vec3 vTSNormal, ...)     in vec4 vTSNormal
#   FFP_Transform(mat4, vec4, out vec4)       + mat3/vec3 and mat4/vec3 overloads
#   FFP_Normalize                             absent from the 2009 file
#   FFP_DerivePointSize                       absent
#   FFP_PixelFog_PositionDepth                absent
#   FFP_SampleTexture                         absent
#
# A failed comparison means the function body is never emitted ("Error: Cached
# function not found SGX_FetchNormal" - seen 9x on device while entering the
# main menu), yet the call and its forward declaration are still written, so the
# generated shader references an undefined function and does not link.
#
# So the 2009 library has to go.  These files are OGRE v1.11.6's own
# Samples/Media/RTShaderLib/GLSL/*, vendored byte for byte (the deps workflow
# clones exactly that tag - build-ios-deps.yml "Get OGRE 1.11.6").  OGRE's media
# is deliberately not installed into the SDK prefix (OGRE_BUILD_SAMPLES=FALSE),
# so copying them from our repo is the only way to keep generation and library
# in step.  SampleLib_ReflectionMap.glsl and RTShaderSystem.material are RoR's
# own and are left alone.
RTSHADER_GLSL_LIBS = (
    "FFPLib_AlphaTest.glsl",
    "FFPLib_Common.glsl",
    "FFPLib_Fog.glsl",
    "FFPLib_Lighting.glsl",
    "FFPLib_Texturing.glsl",
    "FFPLib_Transform.glsl",
    "SGXLib_DualQuaternion.glsl",
    "SGXLib_IntegratedPSSM.glsl",
    "SGXLib_LayeredBlending.glsl",
    "SGXLib_NormalMapLighting.glsl",
    "SGXLib_PerPixelLighting.glsl",
    "SGXLib_TextureAtlas.glsl",
    "SGXLib_TriplanarTexturing.glsl",
)


def patch_rtshader_media(ror):
    """Replace RoR's 2009 RTSS GLSL library with the OGRE 1.11.6 one.

    See RTSHADER_GLSL_LIBS for why this is not optional.  CMake's
    zip_folder_resources packs resources/rtshader into rorres/rtshader.zip, so
    overwriting the files here is enough; verify_ipa.py then re-reads that zip
    inside the finished IPA and checks the signatures really are 1.11.6's.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(here, "rtshader_glsl")
    dst_dir = os.path.join(ror, "resources", "rtshader")

    if not os.path.isdir(src_dir):
        sys.exit("ERROR: the vendored RTSS library is missing: %s" % src_dir)
    if not os.path.isdir(dst_dir):
        sys.exit("ERROR: resources/rtshader is missing from the RoR checkout")

    replaced = 0
    for name in RTSHADER_GLSL_LIBS:
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            sys.exit("ERROR: vendored RTSS library file missing: %s" % src)
        shutil.copyfile(src, os.path.join(dst_dir, name))
        replaced += 1

    # Say what was wrong, not just what was copied: this is the kind of fix that
    # silently regresses if someone drops the vendored directory.
    stale = os.path.join(dst_dir, "SGXLib_NormalMapLighting.glsl")
    text = read(stale)
    if "out vec4 vOut" not in text or "in vec4 vTSNormal" not in text:
        sys.exit("ERROR: resources/rtshader/SGXLib_NormalMapLighting.glsl is "
                 "not the OGRE 1.11.6 version (SGX_FetchNormal must write "
                 "out vec4 and SGX_Light_* must take in vec4 vTSNormal)")

    print("  replaced %d RTSS library files in resources/rtshader with the "
          "OGRE 1.11.6 (v1.11.6) set" % replaced)


def patch_terrain_stock_generator(ror):
    """TerrainGeometryManager.cpp: use OGRE's stock terrain generator on GLES2.

    Why this exists (device evidence, rtss14, RoR (2).log ~14:46:33)
    ----------------------------------------------------------------
    The terrain geometry loads ("Terrain created; size=1025 ...") but the whole
    landscape renders blank.  Every terrain program fails the same way::

        Warning: GLSL: preprocessor produced nothing for
          'OgreTerrain/659483710/sm2/vp/hlod/hlod' (src_len=0); compiling the
          raw source. head:
        RenderingAPIException: ... in compile at .../OgreGLSLESProgram.cpp (line 207)
        Warning: material OgreTerrain/659483710 has no supportable Techniques
          and will be blank.

    The tell is ``src_len=0`` - the shader *source is empty* by the time it is
    compiled.  That is not a preprocessor problem (the earlier iOS fix, the OGRE
    GLSL-preproc fallback, fires only because the source is already empty); it
    means nothing ever wrote GLSL into the program.

    Root cause
    ----------
    RoR does not use OGRE's terrain material generator.  It ships a fork -
    ``source/main/terrain/OgreTerrainPSSMMaterialGenerator.{h,cpp}`` - that adds
    PSSM dynamic shadows.  In the fork only the Cg/HLSL shader helpers are
    actually implemented; ``ShaderHelperGLSL`` and ``ShaderHelperGLSLES`` declare
    ``createVertexProgram``/``createFragmentProgram`` but leave **every**
    ``generateVpHeader``/``generateFpHeader``/``generateVpLayer``/... method an
    empty ``{}`` stub (OgreTerrainPSSMMaterialGenerator.h:232-262).  The active
    helper is chosen at runtime by shader-language availability
    (OgreTerrainPSSMMaterialGenerator.cpp:324-337); on desktop Windows Cg or
    HLSL wins and the terrain renders, but the iPhone's GLES2 render system
    supports only ``glsles`` - so ``ShaderHelperGLSLES`` is picked, its empty
    generators produce an empty string, ``setSource("")`` is called, and every
    terrain program compiles empty source and dies.

    Fix
    ---
    On iOS (ROR_PLATFORM_IOS) fall back to OGRE's *stock*
    ``TerrainMaterialGeneratorA``, whose ``ShaderHelperGLSL`` fully implements
    GLSL **and** GLSL ES generation (``generateVpHeader`` emits ``#version 100``
    + precision qualifiers when ``isLanguageSupported("glsles")``).  This is the
    terrain path OGRE itself ships and tests on GLES2.

    The stock generator is safe to use with RoR's terrain data because its layer
    declaration is byte-identical to the fork's - both expect an
    ``albedo_specular`` (RGBA) and a ``normal_height`` (RGBA) sampler with the
    same element layout, so the terrain's layer textures are sampled identically
    (compare OgreTerrainMaterialGeneratorA.cpp:49-65 with
    OgreTerrainPSSMMaterialGenerator.cpp:43-59).

    The only capability lost is PSSM dynamic shadows *on the terrain* on iOS,
    which is a visual nicety far outweighed by having a visible landscape.  The
    two profile-config blocks below are adjusted so they either configure the
    stock SM2Profile (a different type) or skip entirely - importantly, the
    ``matProfile->getReceiveDynamicShadowsPSSM()`` dereference further down would
    otherwise null-deref, since ``matProfile`` is only assigned for the fork.
    """
    path = os.path.join(ror, "source", "main", "terrain", "TerrainGeometryManager.cpp")
    text = read(path)

    # 1) Include the stock generator (only compiled on iOS).
    text = must_replace(
        text,
        "#include <OgreLight.h>\n"
        "#include <Terrain/OgreTerrainGroup.h>\n",
        "#include <OgreLight.h>\n"
        "#include <Terrain/OgreTerrainGroup.h>\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "// Apple/iOS port: OGRE's stock terrain material generator, used in place\n"
        "// of RoR's TerrainPSSMMaterialGenerator because the latter cannot\n"
        "// generate GLSL ES shaders at all (see configureTerrainDefaults()).\n"
        "#   include <Terrain/OgreTerrainMaterialGeneratorA.h>\n"
        "#endif\n",
        "TerrainGeometryManager.cpp include OgreTerrainMaterialGeneratorA.h",
    )

    # 2) Pick the stock generator on iOS instead of the (GLSL-ES-incapable) fork.
    text = must_replace(
        text,
        "    std::string const & custom_mat = terrainManager->GetDef()->custom_material_name;\n"
        "    if (!custom_mat.empty())\n"
        "    {\n"
        "        terrainOptions->setDefaultMaterialGenerator(\n"
        "            Ogre::TerrainMaterialGeneratorPtr(new Terrn2CustomMaterial(custom_mat, false, true)));\n"
        "    }\n"
        "    else\n"
        "    {\n"
        "        terrainOptions->setDefaultMaterialGenerator(\n"
        "            Ogre::TerrainMaterialGeneratorPtr(new Ogre::TerrainPSSMMaterialGenerator()));\n"
        "    }\n",
        "    std::string const & custom_mat = terrainManager->GetDef()->custom_material_name;\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    // Apple/iOS port: true once OGRE's stock generator is selected below;\n"
        "    // the profile-config blocks further down then treat the active\n"
        "    // profile differently (it is a different type, or not configured at all).\n"
        "    bool ios_stock_gen = false;\n"
        "#endif\n"
        "    if (!custom_mat.empty())\n"
        "    {\n"
        "        terrainOptions->setDefaultMaterialGenerator(\n"
        "            Ogre::TerrainMaterialGeneratorPtr(new Terrn2CustomMaterial(custom_mat, false, true)));\n"
        "    }\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    else\n"
        "    {\n"
        "        // Apple/iOS port: RoR's TerrainPSSMMaterialGenerator only implements\n"
        "        // shader generation for Cg/HLSL; its ShaderHelperGLSL and\n"
        "        // ShaderHelperGLSLES leave every generate*() method an empty stub,\n"
        "        // so on a GLES2-only device the terrain programs are generated as\n"
        "        // EMPTY source, compile to nothing, and the terrain is blank.\n"
        "        // OGRE's stock TerrainMaterialGeneratorA has a complete GLSL / GLSL\n"
        "        // ES generator, so use it here (cost: no PSSM terrain shadows).\n"
        "        ios_stock_gen = true;\n"
        "        terrainOptions->setDefaultMaterialGenerator(\n"
        "            Ogre::TerrainMaterialGeneratorPtr(new Ogre::TerrainMaterialGeneratorA()));\n"
        "    }\n"
        "#else\n"
        "    else\n"
        "    {\n"
        "        terrainOptions->setDefaultMaterialGenerator(\n"
        "            Ogre::TerrainMaterialGeneratorPtr(new Ogre::TerrainPSSMMaterialGenerator()));\n"
        "    }\n"
        "#endif\n",
        "TerrainGeometryManager.cpp pick the stock generator on iOS",
    )

    # 3) Skip the fork-specific profile config when the stock generator is in
    #    use (its active profile is a different type), and configure the stock
    #    SM2Profile in its own branch instead.
    text = must_replace(
        text,
        "    // optimizations\n"
        "    TerrainPSSMMaterialGenerator::SM2Profile* matProfile = nullptr;\n"
        "    if (custom_mat.empty())\n"
        "    {\n",
        "    // optimizations\n"
        "    TerrainPSSMMaterialGenerator::SM2Profile* matProfile = nullptr;\n"
        "    if (custom_mat.empty()\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "        && !ios_stock_gen // the stock generator's profile is a different type\n"
        "#endif\n"
        "        )\n"
        "    {\n",
        "TerrainGeometryManager.cpp guard the fork profile config",
    )

    # 3b) ...and configure the stock SM2Profile right after that block closes.
    text = must_replace(
        text,
        "            terrainManager->getShadowManager()->updateTerrainMaterial(matProfile);\n"
        "        }\n"
        "    }\n",
        "            terrainManager->getShadowManager()->updateTerrainMaterial(matProfile);\n"
        "        }\n"
        "    }\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "    else if (ios_stock_gen)\n"
        "    {\n"
        "        // Apple/iOS port: configure OGRE's stock SM2Profile (a different type\n"
        "        // from the fork's, hence not handled above).  Normal + specular mapping\n"
        "        // are forced on - exactly as the desktop OpenGL branch does - because\n"
        "        // the terrain otherwise renders black; the PSSM shadow hookup is\n"
        "        // skipped, the stock generator manages its own shadows.\n"
        "        Ogre::TerrainMaterialGeneratorA::SM2Profile* stockProfile =\n"
        "            static_cast<Ogre::TerrainMaterialGeneratorA::SM2Profile*>(\n"
        "                terrainOptions->getDefaultMaterialGenerator()->getActiveProfile());\n"
        "        if (stockProfile)\n"
        "        {\n"
        "            stockProfile->setLightmapEnabled(m_spec->lightmap_enabled);\n"
        "            stockProfile->setLayerNormalMappingEnabled(true);\n"
        "            stockProfile->setLayerSpecularMappingEnabled(true);\n"
        "            stockProfile->setLayerParallaxMappingEnabled(false);\n"
        "            stockProfile->setGlobalColourMapEnabled(m_spec->global_colormap_enabled);\n"
        "        }\n"
        "    }\n"
        "#endif\n",
        "TerrainGeometryManager.cpp configure the stock SM2Profile on iOS",
    )

    # 4) The casts-dynamic-shadows block dereferences matProfile, which stays
    #    null when the stock generator is used - guard it.
    text = must_replace(
        text,
        "    if (custom_mat.empty())\n"
        "    {\n"
        "        if (matProfile->getReceiveDynamicShadowsPSSM())\n"
        "        {\n"
        "            terrainOptions->setCastsDynamicShadows(true);\n"
        "        }\n"
        "    }\n",
        "    if (custom_mat.empty() && matProfile) // matProfile is null with the stock generator\n"
        "    {\n"
        "        if (matProfile->getReceiveDynamicShadowsPSSM())\n"
        "        {\n"
        "            terrainOptions->setCastsDynamicShadows(true);\n"
        "        }\n"
        "    }\n",
        "TerrainGeometryManager.cpp guard the casts-dynamic-shadows deref",
    )

    write(path, text)


def patch_terrain_sky_light(ror):
    """Terrain.cpp: create the main light even when Caelum is absent (iOS).

    Why this exists (device evidence, rtss15, RoR (3).log)
    ------------------------------------------------------
    RoR's default sky mode is ``GfxSkyMode::CAELUM`` (CVar "SightRange"
    sibling "gfx_sight_mode" defaults to "1" == CAELUM).  ``Terrain::
    initLight()`` then does::

        if (mode == CAELUM) {
        #ifdef USE_CAELUM
            m_main_light = m_sky_manager->GetSkyMainLight();
        #endif
        }
        else if (mode == SKYX) { ... }
        else { // "screw caelum, we will roll our own light"
            m_main_light = createLight(...); ... }

    On iOS ``USE_CAELUM`` is **not** defined - Caelum is a conan dependency
    the iOS build never provides (``ROR_USE_CAELUM`` is not set anywhere and
    the deps workflow does not build Caelum, so ``Caelum::Caelum`` is absent,
    ``CaelumSystem`` is missing from the binary, and SkyManager.cpp compiles
    to nothing).  The CAELUM branch is therefore *empty* at compile time, so
    whenever the sky mode is CAELUM the ``m_main_light`` assignment never
    runs and it stays ``nullptr``: the whole scene ends up with **no
    directional light at all** and is lit only by ambient.  Desktop never
    hits this because desktop always defines USE_CAELUM.

    ``Terrain::initSkySubSystem()`` already guards its Caelum branch with
    ``#ifdef ... else #endif`` so the skybox fallback runs on iOS; only the
    light side is asymmetric.  Fix it the same way - wrap the whole CAELUM
    branch in ``#if defined(USE_CAELUM) ... #endif`` so that when Caelum is
    absent the mode falls through to the "roll our own light" path.  Desktop
    behaviour is byte-for-byte unchanged (the branch compiles identically
    when USE_CAELUM is defined); iOS gains a real directional sun light.
    """
    path = os.path.join(ror, "source", "main", "terrain", "Terrain.cpp")
    text = read(path)

    text = must_replace(
        text,
        "    if (App::gfx_sky_mode->getEnum<GfxSkyMode>() == GfxSkyMode::CAELUM)\n"
        "    {\n"
        "#ifdef USE_CAELUM\n"
        "        m_main_light = m_sky_manager->GetSkyMainLight();\n"
        "#endif\n"
        "    }\n"
        "    else if (App::gfx_sky_mode->getEnum<GfxSkyMode>() == GfxSkyMode::SKYX)\n",
        "#if defined(USE_CAELUM)\n"
        "    if (App::gfx_sky_mode->getEnum<GfxSkyMode>() == GfxSkyMode::CAELUM)\n"
        "    {\n"
        "        m_main_light = m_sky_manager->GetSkyMainLight();\n"
        "    }\n"
        "    else\n"
        "#endif // USE_CAELUM\n"
        "    if (App::gfx_sky_mode->getEnum<GfxSkyMode>() == GfxSkyMode::SKYX)\n",
        "Terrain.cpp: fall through to the built-in light when Caelum is absent",
    )

    # Diagnostic: record the sky ground-truth.  The viewport background, the
    # linear-fog colour and the built-in light's diffuse colour are all
    # m_def->ambient_color (Terrain::initCamera / initFog / initLight).
    #
    # This line earned its keep: on rtss15 the sky rendered black while
    # ambient turned out to be white (1,1,1), which proved the black came
    # from the skybox render - not the clear colour - and pointed at the
    # CAELUM sky mode (which mounts caelum.zip) as the culprit rather than a
    # bad cubemap or shader.  Keep it: one cheap line that instantly separates
    # "background is wrong" from "skybox is wrong" for any future map.
    text = must_replace(
        text,
        "void RoR::Terrain::initSkySubSystem()\n"
        "{\n"
        "#ifdef USE_CAELUM\n",
        "void RoR::Terrain::initSkySubSystem()\n"
        "{\n"
        "    // Apple/iOS diagnostic: sky ground-truth - the background/fog/light\n"
        "    // colour (ambient_color) and which skybox is used.  A cheap one-line\n"
        "    // way to tell 'background is wrong' from 'skybox is wrong' should a\n"
        "    // sky ever render a solid colour again.\n"
        "    LOG(std::string(\"[RoR|ios-sky] mode=\") +\n"
        "        std::to_string((int)App::gfx_sky_mode->getEnum<GfxSkyMode>()) +\n"
        "        \" ambient=(\" + std::to_string(m_def->ambient_color.r) + \", \" +\n"
        "        std::to_string(m_def->ambient_color.g) + \", \" +\n"
        "        std::to_string(m_def->ambient_color.b) +\n"
        "        \") cubemap='\" + m_def->cubemap_config + \"' sight_range=\" +\n"
        "        std::to_string(m_sight_range));\n"
        "#ifdef USE_CAELUM\n",
        "Terrain.cpp: log the sky ground-truth (ambient colour + skybox)",
    )

    write(path, text)


def patch_cvar_sky_default(ror):
    """CVar.cpp: default the sky mode to SANDSTORM on iOS.

    Why this exists (device evidence, rtss15)
    -----------------------------------------
    ``gfx_sky_mode`` defaults to ``"1"`` == ``GfxSkyMode::CAELUM`` ("best
    looking").  But iOS builds neither Caelum nor SkyX - they are optional
    conan deps the iOS workflow never provides - so CAELUM is the one mode
    that cannot actually run on the device:

      * ``main.cpp`` picks the scene ambient from the mode: CAELUM gets a
        dim (0.3,0.3,0.3) ambient meant to be *supplemented* by Caelum's
        lighting, while SANDSTORM gets the full (0.7,0.7,0.7).  With no
        Caelum present the CAELUM branch leaves the whole scene under-lit.
      * ``ContentManager`` still mounts the ``caelum.zip`` resource pack for
        CAELUM, producing the harmless but noisy "unexpected token / legacy
        script" errors for every ``*.os`` Caelum script, because no plugin is
        registered to compile them.
      * ``Terrain::initLight()``'s CAELUM branch is empty without
        ``USE_CAELUM`` (see patch_terrain_sky_light).

    SANDSTORM is the mode RoR itself designates for machines without
    Caelum/SkyX: it uses the same ``tracks/skyboxcol`` skybox but pairs it
    with a real directional sun light and the brighter ambient.  Defaulting
    to it makes the out-of-box iOS scene correctly lit; CAELUM stays
    reachable from the settings menu and is still patched to create a light
    (patch_terrain_sky_light) as a backstop.
    """
    path = os.path.join(ror, "source", "main", "system", "CVar.cpp")
    text = read(path)

    text = must_replace(
        text,
        '"1"/*(int)GfxSkyMode::CAELUM*/);',
        '"0"/*(int)GfxSkyMode::SANDSTORM*/);',
        "CVar.cpp: default sky mode to SANDSTORM (no Caelum/SkyX on iOS)",
    )

    write(path, text)


def patch_cvar_shadow_default(ror):
    """CVar.cpp: default the shadow type to NONE on iOS.

    Why this exists (device evidence, ror-trace log)
    ------------------------------------------------
    ``gfx_shadow_type`` defaults to ``"1"`` == ``GfxShadowType::PSSM``.  RoR's
    PSSM path programs every shadow-casting and shadow-receiving material with
    ``PSSM/shadow_caster_vs`` / ``PSSM/shadow_receiver_vs``, and on this port
    the GLES2 RTSS cannot build those programs - the device log shows::

        Initializing Shadow Subsystem
        material Ogre/shadow/depth/caster has no supportable Techniques
        Pass 0: vertex program PSSM/shadow_caster_vs cannot be used - not supported
        material tracks/Character#0 has no supportable Techniques and will be blank
        Pass 0: vertex program PSSM/shadow_receiver_vs cannot be used - not supported

    The shadow_receiver failure is not cosmetic: the on-foot/driver character
    (``tracks/character: RoR/Managed_Mats_Base``, cloned per instance by
    ``Character::SetupGfx``) ends up with *no* supportable technique at all,
    so it renders as a broken mesh instead of a human - the exact report from
    the device.  Every other RoR material keeps its lit/textured technique
    next to the shadow pass, but the character's is blanked by it.

    With ``GfxShadowType::NONE`` no PSSM programs are requested anywhere:
    ``ShadowManager`` takes its non-PSSM branch, ``ContentManager`` skips
    mounting the ``shadows/pssm`` resource packs, and every material keeps
    its normal technique.  Shadows simply do not render - the sane default
    for a phone port whose GPU path cannot produce them; the option stays
    reachable from the settings menu for anyone who wants to try.
    """
    path = os.path.join(ror, "source", "main", "system", "CVar.cpp")
    text = read(path)

    text = must_replace(
        text,
        '"1"/*(int)GfxShadowType::PSSM*/);',
        '"0"/*(int)GfxShadowType::NONE*/);',
        "CVar.cpp: default shadow type to NONE (PSSM programs unsupported on GLES2)",
    )

    write(path, text)


def patch_cvar_alt_materials_default(ror):
    """CVar.cpp: default the vehicle material set to the RTSS-safe one on iOS.

    Why this exists (device evidence, rtss19)
    -----------------------------------------
    ``gfx_alt_actor_materials`` defaults to ``false``, which routes every
    vehicle material that has a specular map through the ``_nicemetal``
    variants.  Those are Cg programs (``nicemetal_mm.program``:
    ``NiceMetal_VS_mm cg``, ``profiles vs_1_1 arbvp1``) and this port builds
    without the Cg plugin, so on the device every material of the
    ``XBGTfalcon`` mod logged::

        material XBGTbody (XBcoupe.truck [Instance ID 1]) has no supportable
        Techniques and will be blank
        Pass 0: vertex program NiceMetal_VS_mm cannot be used - not supported

    and the truck crashed the app on spawn - a material with no technique at
    all is what the first ``getTechnique(0)->getPass(0)`` on it dereferences.
    With ``true`` the materials come from ``managed_mats_vehicles.material``
    (``managed/.../specular*``), which carry no program references at all, so
    the RTSS generates GLSL ES for them.

    Like the sky and shadow defaults this is only half the fix: the phone
    keeps its own ``RoR.cfg`` across re-installs, so one run of an older build
    writes ``No`` into it and the default never gets a chance.  See
    patch_cvar_ios_overrides(), which re-asserts the value after
    ``loadConfig()``.
    """
    path = os.path.join(ror, "source", "main", "system", "CVar.cpp")
    text = read(path)

    text = must_replace(
        text,
        '"Use alternate vehicle materials", CVAR_ARCHIVE | CVAR_TYPE_BOOL, "false");',
        '"Use alternate vehicle materials", CVAR_ARCHIVE | CVAR_TYPE_BOOL, "true");',
        "CVar.cpp: default to the alternate (RTSS-safe) vehicle materials",
    )

    write(path, text)


def patch_cvar_ios_overrides(ror):
    """main.cpp: re-assert the iOS-critical cvars *after* RoR.cfg is loaded.

    Why the CVar.cpp defaults are not enough
    ----------------------------------------
    ``CVar.cpp`` gives RoR its default values, and ``patch_cvar_sky_default()``
    / ``patch_cvar_shadow_default()`` change three of them for iOS.  But
    ``main()`` creates the cvars and then immediately calls
    ``App::GetConsole()->loadConfig()``, which reads ``config/RoR.cfg`` and
    overwrites every value that file mentions:

        App::GetConsole()->cVarSetupBuiltins();   // defaults set here
        App::GetConsole()->loadConfig();          // RoR.cfg wins from here on

    ``RoR.cfg`` ships empty in ``skeleton.zip``, so the new defaults do apply on
    a fresh install - and that is the trap.  RoR writes ``RoR.cfg`` back
    whenever the settings menu is used or the console runs ``saveconfig``
    (``Console::saveConfig()`` archives every CVAR_ARCHIVE cvar), and the app
    container survives a re-install.  From the second launch on, the phone's own
    ``RoR.cfg`` - which still says ``gfx_shadow_type=1`` - silently undoes the
    patch.

    That is exactly what the device showed: the shadow default was switched to
    NONE, a new build was sideloaded, and the on-foot character still rendered
    as a broken mesh.  The log explained why it could not have helped yet::

        material tracks/Character#0 has no supportable Techniques and will be blank
        Pass 0: vertex program PSSM/shadow_receiver_vs cannot be used - not supported

    The character material is ``tracks/character: RoR/Managed_Mats/Base``, whose
    technique derives ``Shadows/managed/base_receiver``; with PSSM shadows that
    abstract technique (``resources/managed_materials/shadows/pssm/on``) injects
    the unsupported PSSM receiver program, so the clone ends up with no usable
    technique at all.  With ``GfxShadowType::NONE``, ``ContentManager`` mounts
    the ``pssm/off`` folder instead, whose ``base_receiver`` is an empty pass -
    the character keeps its normal material.

    The same mechanism breaks mods.  ``gfx_alt_actor_materials`` defaults to
    ``false``, which routes every vehicle material that has a specular map
    through the ``_nicemetal`` variants.  Those are Cg programs
    (``nicemetal_mm.program``: ``NiceMetal_VS_mm cg``, ``profiles vs_1_1
    arbvp1``) and this port builds without the Cg plugin, so every specular
    vehicle material is left with no supportable technique.  On the device the
    ``XBGTfalcon`` mod's materials all logged "has no supportable Techniques and
    will be blank / Pass 0: vertex program NiceMetal_VS_mm cannot be used - not
    supported" and the truck crashed the app on spawn.  The ``true`` value uses
    the ``managed/.../specular*`` materials from ``managed_mats_vehicles
    .material``, which contain no program references at all and let the RTSS
    generate GLSL ES for them.

    Assigning the values here - after ``loadConfig()``, before OGRE and the
    content manager read them - makes the iOS defaults authoritative again
    without changing anything on desktop, where ``RoR.cfg`` is *supposed* to
    win.

    ``setVal()``, not ``setStr()``: ``CVar::setStr()`` saves the string and
    sets ``m_value_num = 0``, and every reader of these cvars goes through
    ``m_value_num`` (``getEnum<>()`` / ``getBool()``), so ``setStr("true")``
    leaves ``alt_actor_materials`` reading *false* - the mod fix silently
    staying off is exactly the class of bug this patch exists to remove.
    ``Console::loadConfig()`` does it correctly because it routes through
    ``cVarAssign()``/``ParseHelper()``, which parse the text first.

    The block also writes the before -> after values into ror-trace.log
    (``[RoR|iOS] cvars after RoR.cfg``), reading the "after" values back out of
    the cvars rather than printing the intended constants, so the trace shows
    what the engine will actually use.  That line is the fastest way to tell
    from a device trace whether RoR.cfg had overridden anything, and it is the
    marker ``verify_ipa.py`` requires in the shipped binary, so this patch
    cannot fall out of a build unnoticed.
    """
    path = os.path.join(ror, "source", "main", "main.cpp")
    text = read(path)
    text = must_replace(
        text,
        "        // Load RoR.cfg - updates cvars\n"
        "        App::GetConsole()->loadConfig();\n",
        "        // Load RoR.cfg - updates cvars\n"
        "        App::GetConsole()->loadConfig();\n"
        "\n"
        "#ifdef ROR_PLATFORM_IOS\n"
        "        // Apple/iOS port: RoR.cfg (shipped empty, then rewritten by the\n"
        "        // settings menu and 'saveconfig') overrides the compile-time\n"
        "        // defaults set in CVar.cpp, so re-assert the three values this\n"
        "        // GLES2 render path cannot work without.  See\n"
        "        // patch_cvar_ios_overrides() in apply_ios_patches.py for the\n"
        "        // device evidence behind each of them; verify_ipa.py requires the\n"
        "        // marker line below to be present in the shipped binary.\n"
        "        //\n"
        "        // setVal(), never setStr(): setStr() stores the string and zeroes\n"
        "        // m_value_num, and these cvars are read as getEnum<>() / getBool()\n"
        "        // - i.e. from m_value_num - so setStr(\"true\") would leave\n"
        "        // alt_actor_materials reading false.  The trace line prints the\n"
        "        // values as the engine will read them, so a device trace shows\n"
        "        // what actually took effect rather than what was intended.\n"
        "        {\n"
        "            const int  ios_cfg_sky    = App::gfx_sky_mode->getInt();\n"
        "            const int  ios_cfg_shadow = App::gfx_shadow_type->getInt();\n"
        "            const bool ios_cfg_alt    = App::gfx_alt_actor_materials->getBool();\n"
        "            App::gfx_sky_mode->setVal((int)0);           // GfxSkyMode::SANDSTORM (no Caelum/SkyX on iOS)\n"
        "            App::gfx_shadow_type->setVal((int)0);        // GfxShadowType::NONE (PSSM programs unsupported)\n"
        "            App::gfx_alt_actor_materials->setVal(true);  // RTSS-safe vehicle materials (Cg 'nicemetal' unsupported)\n"
        "            RoR_IosStartupTick(\"[RoR|iOS] cvars after RoR.cfg: sky %d->%d, shadow %d->%d, alt_actor_materials %d->%d\",\n"
        "                ios_cfg_sky, App::gfx_sky_mode->getInt(),\n"
        "                ios_cfg_shadow, App::gfx_shadow_type->getInt(),\n"
        "                (int)ios_cfg_alt, (int)App::gfx_alt_actor_materials->getBool());\n"
        "        }\n"
        "#endif\n",
        "main.cpp re-assert the iOS-critical cvars after RoR.cfg",
    )
    write(path, text)


def patch_nicemetal_material_bug(ror):
    """Fix a malformed ``texture_alias`` in the transparent nicemetal material.

    ``resources/managed_materials/managed_mats_vehicles_transparent_nicemetal
    .material`` line 125 reads::

        texture_alias specular_tex 2d

    but ``texture_alias`` takes exactly one argument (the alias name); the
    texture *type* (``2d``) belongs on a ``texture`` line, as the non-nicemetal
    file has it.  OGRE's script compiler rejects the line and gives up on the
    rest of the file::

        Error: ScriptCompiler - fewer parameters expected in
        managed_mats_vehicles_transparent_nicemetal.material(125):
        texture_alias must have at most 1 argument

    Everything defined after line 125 - including
    ``managed/mesh_transparent/specular_nicemetal`` - therefore never exists.

    This is a genuine upstream bug, and on iOS it is the one hard
    ``ScriptCompiler`` *error* in every trace.  ``patch_cvar_ios_overrides()``
    now routes vehicles to the alternate materials, so the broken definition is
    no longer reachable at runtime - but a parse error on every launch is how
    you learn to skim the log, and the next real one hides behind it.  Dropping
    the stray ``2d`` is the whole fix.
    """
    path = os.path.join(
        ror, "resources", "managed_materials",
        "managed_mats_vehicles_transparent_nicemetal.material")
    text = read(path)
    text = must_replace(
        text,
        "texture_alias specular_tex 2d",
        "texture_alias specular_tex",
        "transparent nicemetal material: texture_alias takes 1 argument",
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
    # NewImGuiFrame is not part of InputEngine and reads the OIS keyboard
    # directly - the one such caller, and the first thing to dereference the
    # null keyboard after the main menu comes up.
    patch_gui_manager(args.ror_dir)
    # The character mesh and the spawn hint live outside source/, so they are
    # their own patches rather than part of patch_gui_manager().  Both are
    # resource/GUI fixes the device asks for directly: the "tint" pass that
    # paints the body black on GLES2, and the desktop-only spawn hint.
    # See patch_character_material() / patch_gamechatbox() for the reasoning.
    patch_character_material(args.ror_dir)
    patch_gamechatbox(args.ror_dir)
    # Not a fix but a probe: the only way to tell "bad shading" from "bad
    # skinning" for the black body without a device in hand.  See
    # patch_character_diagnostic().
    patch_character_diagnostic(args.ror_dir)
    # The menu is drawn by ImGui, but it is *queued* for drawing by
    # OgreImGui::renderQueueStarted(), which silently skips the overlay when the
    # active viewport is not the app viewport.  That is the difference between
    # "the menu is not visible" and "the menu is never drawn", so make it
    # observable.
    patch_ogreimgui(args.ror_dir)
    # And the next layer down again: the overlay IS queued and drawn, but on
    # GLES2 its RTSS vertex shader was left holding the previous renderable's
    # matrices, so the pixel-space ImGui vertices collapsed into the left edge.
    # This is the one that actually made the menu invisible; see the (long)
    # header of patch_ogreimgui_overlay().
    patch_ogreimgui_overlay(args.ror_dir)
    # Same class of bug one level up: FetchIcon() asks for every icon in the
    # group that only holds the country flags, and the icons live elsewhere.
    # It only bites on the first launch after a mod-cache rebuild, which is
    # exactly when the main menu draws its "Cache updated" notice box.
    patch_fetchicon(args.ror_dir)
    # The console draws one icon per message every frame; route those loads
    # through the same FetchIcon() so a missing icon (lightbulb.png) stops
    # throwing once per frame.
    patch_console_view(args.ror_dir)
    # The runtime platform layer: the new iOS files themselves plus the two
    # entry-point/frame-loop changes in main.cpp that hand the process over to
    # UIKit.
    patch_ios_platform(args.ror_dir)
    patch_main_cpp(args.ror_dir)
    # GLES2 has no fixed-function pipeline; RoR's materials assume one.  This
    # turns on OGRE's RTShader System and hands it every scene manager, which
    # is what turns "RenderSystem does not support FixedFunction" from a fatal
    # startup error into generated shaders.  Must come after patch_ios_platform
    # - it declares its scene-manager hook in ios_platform.h, which only exists
    # from that point on.
    patch_rtshader(args.ror_dir)
    # Same class as patch_mygui_resources() but one layer down: OGRE's own
    # StdQuad_vp.program names four GLSL ES sources that were never written.
    # See patch_ogrecore_stdquad_glsles() for why they are worth writing.
    patch_ogrecore_stdquad_glsles(args.ror_dir)
    # Resources rather than code, but the same class of problem: files the GUI
    # needs and that simply are not there under the name it asks for.
    # See patch_mygui_resources() for the reasoning.
    patch_mygui_resources(args.ror_dir)
    # Also data, and also version locked: RoR's resources/rtshader is the 2009
    # RTSS function library, which no longer answers the calls OGRE 1.11.6's
    # sub-render-states make ("Error: Cached function not found SGX_*" on
    # device).  See patch_rtshader_media().
    patch_rtshader_media(args.ror_dir)
    # The landscape renders blank because RoR's forked terrain material
    # generator cannot emit GLSL ES at all - its GLSL/GLSLES shader helpers are
    # empty stubs, so on GLES2 the terrain shaders are generated as empty source.
    # Fall back to OGRE's stock generator, which fully supports GLSL ES.
    # See patch_terrain_stock_generator().
    patch_terrain_stock_generator(args.ror_dir)
    # The default sky mode is CAELUM but Caelum is not compiled in on iOS, so
    # Terrain::initLight()'s CAELUM branch is empty and m_main_light stays
    # null - no directional sun light, the scene is lit only by ambient.  Make
    # the light fall through to the built-in path when Caelum is absent.
    # See patch_terrain_sky_light().
    patch_terrain_sky_light(args.ror_dir)
    # Also default the sky mode itself to SANDSTORM - the only sky mode iOS
    # can fully run (no Caelum/SkyX libraries), which selects the brighter
    # scene ambient and the built-in directional light.  See
    # patch_cvar_sky_default().
    patch_cvar_sky_default(args.ror_dir)
    # patch_cvar_shadow_default().
    patch_cvar_shadow_default(args.ror_dir)
    # Shadows are off on iOS, so the "pssm/off" variant of the shared
    # shadow technique is the one that is actually used - and it has an empty
    # pass, which leaves every material built on it with a black ambient.
    # That is what draws the on-foot character solid black.  See
    # patch_shadows_off_ambient().
    patch_shadows_off_ambient(args.ror_dir)
    # Vehicles must not take the Cg 'nicemetal' materials: they carry no
    # technique at all on this GLES2 path, and the spawner's first
    # getTechnique(0)->getPass(0) on one is a null dereference (the device's
    # mod-spawn crash).  See patch_cvar_alt_materials_default().
    patch_cvar_alt_materials_default(args.ror_dir)
    # The CVar.cpp defaults above do not survive RoR.cfg on the second launch
    # (or after any settings change) - main.cpp re-asserts them right after the
    # config file is loaded.  See patch_cvar_ios_overrides().
    patch_cvar_ios_overrides(args.ror_dir)
    # A malformed `texture_alias` line in the transparent nicemetal material
    # aborts the rest of that script and raises the only hard ScriptCompiler
    # error in every trace.  See patch_nicemetal_material_bug().
    patch_nicemetal_material_bug(args.ror_dir)

    print("applied Apple/iOS patches to %s" % args.ror_dir)
    if args.ois_include:
        print("  OIS include: %s" % args.ois_include)
    if args.ois_lib:
        print("  OIS library: %s" % args.ois_lib)


if __name__ == "__main__":
    main()
