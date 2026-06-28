cmake_policy(SET CMP0078 NEW)
cmake_policy(SET CMP0086 NEW)
include(UseSWIG)

set(CMAKE_SWIG_OUTDIR "${CMAKE_CURRENT_BINARY_DIR}")
set(
    CMAKE_SWIG_FLAGS
    "-py3"
    "-I/usr/include/gnuradio/swig"
    "-I${CMAKE_CURRENT_SOURCE_DIR}/../include"
)
set_source_files_properties(
    bindings/sionna_channel_swig.i
    PROPERTIES
        CPLUSPLUS ON
        SWIG_MODULE_NAME sionna_channel_swig
)

swig_add_library(
    sionna_channel_swig
    TYPE MODULE
    LANGUAGE python
    SOURCES bindings/sionna_channel_swig.i
)

target_include_directories(
    sionna_channel_swig
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/../include
        ${Python3_INCLUDE_DIRS}
)

target_link_libraries(
    sionna_channel_swig
    PRIVATE
        gnuradio-sionna_channel
        gnuradio::gnuradio-runtime
        Python3::Python
)

set(PYTHON_INSTALL_DIR
    "${CMAKE_INSTALL_LIBDIR}/python${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/dist-packages/sionna_channel"
)

install(
    TARGETS sionna_channel_swig
    LIBRARY DESTINATION ${PYTHON_INSTALL_DIR}
)

install(
    FILES
        "${CMAKE_CURRENT_BINARY_DIR}/sionna_channel_swig.py"
        sionna_channel/__init__.py
    DESTINATION ${PYTHON_INSTALL_DIR}
)
