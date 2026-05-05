# syntax=docker/dockerfile:1.6
# Build and run a Cosmos SDK simd node with ML-DSA-44 support. The build stage
# uses the Go 1.23.x Debian toolchain because the repo's `make build` expects
# cgo+glibc (matches what runs in CI and on the host today).
ARG GO_VERSION=1.23.5

FROM golang:${GO_VERSION}-bookworm AS builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      make git bash ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .

# LEDGER_ENABLED=false keeps the build hermetic: libusb is not installed in the
# builder image and the testnet never needs ledger hardware. CGO stays on so
# we match the defaults of `make build` exactly (matching the existing
# Makefile target — do not change build behaviour).
ENV LEDGER_ENABLED=false
RUN make build

FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates bash jq curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/build/simd /usr/local/bin/simd
COPY docker/testnet/scripts/node_entrypoint.sh /usr/local/bin/node_entrypoint.sh
RUN chmod +x /usr/local/bin/node_entrypoint.sh

# P2P, RPC, Prometheus, REST, gRPC. Compose maps these to unique host ports
# per validator.
EXPOSE 26656 26657 26660 1317 9090

ENV COSMOS_HOME=/cosmos
ENTRYPOINT ["/usr/local/bin/node_entrypoint.sh"]
CMD ["start"]
