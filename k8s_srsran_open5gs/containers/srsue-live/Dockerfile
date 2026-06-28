FROM ghcr.io/sulaimanalmani/srsranzmq/srsue@sha256:7ede93eb1238df0f36afa65c3d963198beac0a4a3ec76f7b98bad2cd893614a4 AS builder

USER root

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        liborc-0.4-dev \
        swig \
    && rm -rf /var/lib/apt/lists/*

RUN test "$(gnuradio-config-info --version)" = "3.8.1.0" \
    && test "$(pkg-config --modversion gnuradio-runtime)" = "3.8.1"

COPY gr-sionna-channel /tmp/gr-sionna-channel

RUN cmake \
        -S /tmp/gr-sionna-channel \
        -B /tmp/gr-sionna-channel/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
    && cmake --build /tmp/gr-sionna-channel/build --parallel \
    && cd /tmp/gr-sionna-channel/build \
    && ctest --output-on-failure \
    && cmake --install /tmp/gr-sionna-channel/build \
    && ldconfig \
    && cd / \
    && PYTHONPATH=/usr/local/lib/python3.8/dist-packages \
        python3 -c \
        "exec(open('/tmp/gr-sionna-channel/python/qa_sparse_channel.py').read())"

FROM localhost/srsue-sparse:stage3-gr38-v1

USER root

COPY --from=builder /usr/local/ /usr/local/

RUN ldconfig \
    && python3 -c \
        "from sionna_channel import commit_both, sparse_channel_cc; print(commit_both, sparse_channel_cc)"
