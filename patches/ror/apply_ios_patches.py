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


def patch_conanfile(ror):
    path = os.path.join(ror, "conanfile.py")
    text = read(path)
    text = must_replace(
        text,
        '        self.requires("ois/1.4.1@rigsofrods/custom")\n',
        '        # Apple/iOS port: OIS is built from source, not fetched by conan.\n',
        "conanfile.py ois requirement",
    )
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ror_dir")
    ap.add_argument("--ois-include", default="")
    ap.add_argument("--ois-lib", default="")
    args = ap.parse_args()

    if not os.path.isdir(args.ror_dir):
        sys.exit("ERROR: not a directory: %s" % args.ror_dir)

    patch_conanfile(args.ror_dir)
    patch_dependencies(args.ror_dir)
    patch_main_cmake(args.ror_dir)

    print("applied Apple/iOS patches to %s" % args.ror_dir)
    if args.ois_include:
        print("  OIS include: %s" % args.ois_include)
    if args.ois_lib:
        print("  OIS library: %s" % args.ois_lib)


if __name__ == "__main__":
    main()
