# Injected into every conan CMake build through
#   -c tools.cmake.cmaketoolchain:user_toolchain=["<this file>"]
#
# Why: OGRE 1.11 optionally builds PlugIns/CgProgramManager, gated on
#
#   cmake_dependent_option(OGRE_BUILD_PLUGIN_CG "Build Cg plugin" TRUE
#       "Cg_FOUND;NOT APPLE_IOS;NOT WINDOWS_STORE;NOT WINDOWS_PHONE" FALSE)
#
# The NVIDIA Cg Toolkit has been dead for a decade, and the `cg-toolkit`
# conan package ships *Linux ELF* libraries even in its macOS package, so
# linking that plugin dies with:
#
#   ld: multiple errors: unknown file type in '.../lib/libCgGL.so';
#                       unknown file type in '.../lib/libCg.so'
#
# Nothing in this port needs Cg, so switch the plugin off. Pre-setting the
# cache entry is enough: cmake_dependent_option() only applies its default
# when the option is not already cached.
#
# DO NOT additionally set CMAKE_DISABLE_FIND_PACKAGE_Cg here. The conan
# generated OGREConfig.cmake ends with
#
#   find_dependency(Cg REQUIRED)
#
# (because the ogre3d recipe lists cg-toolkit as a requirement), and a
# REQUIRED package cannot be disabled - every consumer of OGRE, MyGUI first,
# would fail to configure instead.
set(OGRE_BUILD_PLUGIN_CG OFF CACHE BOOL "Cg Toolkit is dead - iOS port" FORCE)
