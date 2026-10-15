# Injected into every conan CMake build through
#   -c tools.cmake.cmaketoolchain:user_toolchain=<this file>
#
# Why: OGRE 1.11 optionally builds PlugIns/CgProgramManager, gated on
#
#   cmake_dependent_option(OGRE_BUILD_PLUGIN_CG "Build Cg plugin" TRUE
#       "Cg_FOUND;NOT APPLE_IOS;NOT WINDOWS_STORE;NOT WINDOWS_PHONE" FALSE)
#
# The NVIDIA Cg Toolkit has been dead for a decade, and the `cg-toolkit`
# conan package ships *Linux ELF* libraries even in its macOS package, so the
# link step dies with:
#
#   ld: multiple errors: unknown file type in '.../lib/libCgGL.so';
#                       unknown file type in '.../lib/libCg.so'
#
# Nothing in this port needs Cg, so make find_package(Cg) fail and force the
# plugin off. `cmake_dependent_option` keeps an already-cached value as long
# as its dependency list is satisfied, so the FORCE below wins.

set(CMAKE_DISABLE_FIND_PACKAGE_Cg TRUE CACHE BOOL "Cg Toolkit is dead - iOS port" FORCE)
set(OGRE_BUILD_PLUGIN_CG OFF CACHE BOOL "Cg Toolkit is dead - iOS port" FORCE)
