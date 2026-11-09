include(FetchContent)

# Archive hashes are fixed, including when using the local bootstrap cache.
set(_eigen_url "https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz")
set(_json_url "https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz")
if(EXISTS "${PROJECT_SOURCE_DIR}/.cache/dependencies/eigen-3.4.0.tar.gz")
    set(_eigen_url "${PROJECT_SOURCE_DIR}/.cache/dependencies/eigen-3.4.0.tar.gz")
endif()
if(EXISTS "${PROJECT_SOURCE_DIR}/.cache/dependencies/json-3.11.3.tar.xz")
    set(_json_url "${PROJECT_SOURCE_DIR}/.cache/dependencies/json-3.11.3.tar.xz")
endif()
FetchContent_Declare(eigen URL "${_eigen_url}"
    URL_HASH SHA256=8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_Declare(nlohmann_json URL "${_json_url}"
    URL_HASH SHA256=d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
# Header-only imports intentionally avoid dependency examples, benchmarks and tests.
FetchContent_GetProperties(eigen)
if(NOT eigen_POPULATED)
    FetchContent_Populate(eigen)
endif()
if(NOT TARGET Eigen3::Eigen)
    add_library(lumafem_eigen INTERFACE)
    target_include_directories(lumafem_eigen SYSTEM INTERFACE "${eigen_SOURCE_DIR}")
    add_library(Eigen3::Eigen ALIAS lumafem_eigen)
endif()
FetchContent_GetProperties(nlohmann_json)
if(NOT nlohmann_json_POPULATED)
    FetchContent_Populate(nlohmann_json)
endif()
if(NOT TARGET nlohmann_json::nlohmann_json)
    add_library(lumafem_json INTERFACE)
    target_include_directories(lumafem_json SYSTEM INTERFACE "${nlohmann_json_SOURCE_DIR}/include")
    add_library(nlohmann_json::nlohmann_json ALIAS lumafem_json)
endif()
