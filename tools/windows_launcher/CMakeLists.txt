cmake_minimum_required(VERSION 3.24)
project(LumaFEMWindowsLauncher LANGUAGES CXX RC)

if(NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8 OR NOT CMAKE_GENERATOR STREQUAL "Ninja")
    message(FATAL_ERROR "The desktop launcher requires MSVC x64 and Ninja.")
endif()
if(NOT CMAKE_CXX_COMPILER_ARCHITECTURE_ID STREQUAL "x64")
    message(FATAL_ERROR "The desktop launcher requires the x64 compiler target.")
endif()

# Probe the raw localized bytes, matching every direct Ninja compiler invocation.
set(_deps_probe "${CMAKE_BINARY_DIR}/CMakeFiles/LumaFEMShowIncludes")
file(MAKE_DIRECTORY "${_deps_probe}")
file(WRITE "${_deps_probe}/lumafem_deps_probe.h" "// dependency prefix probe\n")
file(WRITE "${_deps_probe}/probe.cpp" "#include \"${_deps_probe}/lumafem_deps_probe.h\"\n")
execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env VSLANG=1033
            "${CMAKE_CXX_COMPILER}" /nologo /TP /showIncludes /Zs "${_deps_probe}/probe.cpp"
    RESULT_VARIABLE _deps_probe_result OUTPUT_VARIABLE _deps_probe_output
    ERROR_VARIABLE _deps_probe_error ENCODING NONE)
string(REPLACE "\r" "" _deps_probe_output "${_deps_probe_output}")
string(REGEX MATCH "(^|\n)([^\n]*: +)[A-Za-z]:[/\\\\][^\n]*lumafem_deps_probe\\.h"
    _deps_probe_match "${_deps_probe_output}")
if(NOT _deps_probe_result EQUAL 0 OR NOT _deps_probe_match)
    message(FATAL_ERROR "Cannot establish MSVC/Ninja dependency prefix: ${_deps_probe_error}")
endif()
set(CMAKE_CL_SHOWINCLUDES_PREFIX "${CMAKE_MATCH_2}")
set(CMAKE_CXX_CL_SHOWINCLUDES_PREFIX "${CMAKE_CL_SHOWINCLUDES_PREFIX}")
list(PREPEND CMAKE_CXX_COMPILER_LAUNCHER "${CMAKE_COMMAND}" -E env VSLANG=1033)

get_filename_component(LUMAFEM_PROJECT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(LUMAFEM_ICON "${LUMAFEM_PROJECT_ROOT}/python/lumafem/gui/assets/lumafem.ico")
if(NOT EXISTS "${LUMAFEM_ICON}")
    message(FATAL_ERROR "Approved LumaFEM ICO is missing: ${LUMAFEM_ICON}")
endif()
file(TO_CMAKE_PATH "${LUMAFEM_ICON}" LUMAFEM_ICON)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${LUMAFEM_ICON}" "${CMAKE_CURRENT_SOURCE_DIR}/launcher.rc.in")
configure_file(launcher.rc.in "${CMAKE_CURRENT_BINARY_DIR}/launcher.rc" @ONLY)
set_source_files_properties("${CMAKE_CURRENT_BINARY_DIR}/launcher.rc" PROPERTIES
    OBJECT_DEPENDS "${LUMAFEM_ICON};${CMAKE_CURRENT_SOURCE_DIR}/launcher.rc.in")
add_executable(LumaFEM WIN32 launcher.cpp "${CMAKE_CURRENT_BINARY_DIR}/launcher.rc")
target_compile_features(LumaFEM PRIVATE cxx_std_17)
target_compile_options(LumaFEM PRIVATE /W4 /permissive- /utf-8)
target_compile_definitions(LumaFEM PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(LumaFEM PRIVATE shell32 user32)
set_target_properties(LumaFEM PROPERTIES CXX_EXTENSIONS OFF
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
