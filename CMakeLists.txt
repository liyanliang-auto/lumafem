cmake_minimum_required(VERSION 3.24)
project(LumaFEM VERSION 0.1.0 LANGUAGES CXX)

if(MSVC AND CMAKE_GENERATOR MATCHES "^Ninja")
    # CMake's automatic Windows decoding can transcode a UTF-8 localized
    # /showIncludes prefix twice. Ninja compares raw bytes, so detect the
    # actual prefix without decoding, under the same requested locale used
    # by every compiler invocation (also when Ninja is called directly).
    # 1033 may fall back to the installed locale; no language pack is needed.
    set(_deps_probe "${CMAKE_BINARY_DIR}/CMakeFiles/LumaFEMShowIncludes")
    file(MAKE_DIRECTORY "${_deps_probe}")
    file(WRITE "${_deps_probe}/lumafem_deps_probe.h" "// dependency prefix probe\n")
    file(WRITE "${_deps_probe}/probe.cpp"
        "#include \"${_deps_probe}/lumafem_deps_probe.h\"\n")
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -E env VSLANG=1033
                "${CMAKE_CXX_COMPILER}" /nologo /TP /showIncludes /Zs
                "${_deps_probe}/probe.cpp"
        RESULT_VARIABLE _deps_probe_result
        OUTPUT_VARIABLE _deps_probe_output
        ERROR_VARIABLE _deps_probe_error
        ENCODING NONE)
    string(REPLACE "\r" "" _deps_probe_output "${_deps_probe_output}")
    string(REGEX MATCH "(^|\n)([^\n]*: +)[A-Za-z]:[/\\\\][^\n]*lumafem_deps_probe\\.h"
        _deps_probe_match "${_deps_probe_output}")
    if(NOT _deps_probe_result EQUAL 0 OR NOT _deps_probe_match)
        message(FATAL_ERROR "Cannot establish the MSVC/Ninja header dependency prefix: ${_deps_probe_error}")
    endif()
    set(CMAKE_CL_SHOWINCLUDES_PREFIX "${CMAKE_MATCH_2}")
    set(CMAKE_CXX_CL_SHOWINCLUDES_PREFIX "${CMAKE_CL_SHOWINCLUDES_PREFIX}")
    # The changed launcher also invalidates objects from pre-fix builds whose
    # dependency records were empty. Preserve any caller-provided launcher.
    list(PREPEND CMAKE_CXX_COMPILER_LAUNCHER "${CMAKE_COMMAND}" -E env VSLANG=1033)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
option(LUMAFEM_BUILD_PYTHON "Build the native desktop binding" ON)
include(cmake/Dependencies.cmake)

add_library(lumafem_core STATIC
    src/core/model.cpp src/core/material.cpp src/core/section.cpp
    src/core/system.cpp src/core/physical_gauge.cpp src/core/analysis.cpp)
target_include_directories(lumafem_core PUBLIC ${PROJECT_SOURCE_DIR}/include)
target_link_libraries(lumafem_core PUBLIC Eigen3::Eigen)
target_compile_definitions(lumafem_core PUBLIC EIGEN_MPL2_ONLY)
if(MSVC)
    target_compile_options(lumafem_core PRIVATE /W4 /permissive- /utf-8 /bigobj)
    target_compile_definitions(lumafem_core PUBLIC NOMINMAX)
else()
    target_compile_options(lumafem_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_library(lumafem_codec STATIC src/adapters/json_codec.cpp)
target_include_directories(lumafem_codec PUBLIC ${PROJECT_SOURCE_DIR}/src/adapters)
target_link_libraries(lumafem_codec PUBLIC lumafem_core nlohmann_json::nlohmann_json)
add_executable(lumafem_cli src/cli/main.cpp)
target_link_libraries(lumafem_cli PRIVATE lumafem_codec)
set_target_properties(lumafem_cli PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

if(LUMAFEM_BUILD_PYTHON)
    set(PYBIND11_FINDPYTHON ON)
    find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module)
    execute_process(COMMAND "${Python_EXECUTABLE}" -m pybind11 --cmakedir
        RESULT_VARIABLE _pybind_result OUTPUT_VARIABLE _pybind_dir OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT _pybind_result EQUAL 0)
        message(FATAL_ERROR "pybind11 is missing; run scripts/bootstrap.ps1 first")
    endif()
    find_package(pybind11 2.13.6 EXACT CONFIG REQUIRED PATHS "${_pybind_dir}" NO_DEFAULT_PATH)
    pybind11_add_module(_lumafem src/bindings/module.cpp)
    target_link_libraries(_lumafem PRIVATE lumafem_codec)
    set_target_properties(_lumafem PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/python
        RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/python)
    foreach(_config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
        set_target_properties(_lumafem PROPERTIES
            LIBRARY_OUTPUT_DIRECTORY_${_config} ${CMAKE_BINARY_DIR}/python
            RUNTIME_OUTPUT_DIRECTORY_${_config} ${CMAKE_BINARY_DIR}/python)
        set_target_properties(lumafem_cli PROPERTIES RUNTIME_OUTPUT_DIRECTORY_${_config} ${CMAKE_BINARY_DIR}/bin)
    endforeach()
endif()

include(CTest)
if(BUILD_TESTING AND EXISTS "${PROJECT_SOURCE_DIR}/tests/CMakeLists.txt")
    add_subdirectory(tests)
endif()
install(TARGETS lumafem_cli RUNTIME DESTINATION bin)
if(LUMAFEM_BUILD_PYTHON)
    install(TARGETS _lumafem LIBRARY DESTINATION python RUNTIME DESTINATION python)
endif()
install(FILES THIRD_PARTY_NOTICES.md requirements-desktop.txt DESTINATION .)
