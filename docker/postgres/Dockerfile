# syntax=docker/dockerfile:1.27.0@sha256:bde3983e9c939224420ddaf6b784cc30e09b035a4dea01f581230c50809f372e
FROM postgis/postgis:17-3.5-alpine@sha256:894f570c0cf0664ed5576a8fd5d5bfb8fb1b19d592885b686c3a88c8bd90c41f

ARG PGVECTOR_COMMIT=8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c
ADD https://github.com/pgvector/pgvector.git#${PGVECTOR_COMMIT} /tmp/pgvector

RUN apk upgrade --no-cache \
    && apk add --no-cache --virtual .pgvector-build-deps build-base $DOCKER_PG_LLVM_DEPS \
    && make -C /tmp/pgvector clean \
    && make -C /tmp/pgvector OPTFLAGS="" \
    && make -C /tmp/pgvector install \
    && rm -rf /tmp/pgvector \
    && apk del .pgvector-build-deps
