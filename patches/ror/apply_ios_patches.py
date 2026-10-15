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
    # libSystem, which every app links anyway.  The frameworks the app actually
    # needs (UIKit, OpenGLES, AudioToolbox, ...) are added by the Xcode project
    # in .github/workflows/build-ipa.yml, not here.
    text = must_replace(
        text,
        "if (WIN32)\n"
        "    set(OS_LIBS \"Ws2_32\")\n"
        "else ()\n",
        "if (WIN32)\n"
        "    set(OS_LIBS \"Ws2_32\")\n"
        "elseif (APPLE AND CMAKE_SYSTEM_NAME STREQUAL \"iOS\")\n"
        "    set(OS_LIBS \"\")\n"
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
        "endif ()\n",
        "main/CMakeLists.txt OIS include/link",
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
    patch_main_cmake(args.ror_dir)
    # Platform layer: these fix things that are broken on iOS specifically,
    # independent of how the build is driven.
    patch_errorutils(args.ror_dir)
    patch_platformutils(args.ror_dir)
    patch_appcontext(args.ror_dir)
    # Platform-specific headers/libs that simply do not exist with that
    # meaning on iOS.
    patch_soundmanager(args.ror_dir)
    patch_server_script_curl_helpers(args.ror_dir)
    # Missing guard holes for the supported "SocketW without libcurl" build,
    # which is the configuration we run on iOS.
    patch_network_header(args.ror_dir)
    patch_repo_selector(args.ror_dir)
    patch_multiplayer_selector(args.ror_dir)

    print("applied Apple/iOS patches to %s" % args.ror_dir)
    if args.ois_include:
        print("  OIS include: %s" % args.ois_include)
    if args.ois_lib:
        print("  OIS library: %s" % args.ois_lib)


if __name__ == "__main__":
    main()
