# syntax=docker/dockerfile:1.7
FROM postgis/postgis:17-3.5-alpine

ARG PGVECTOR_COMMIT=8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c
ADD https://github.com/pgvector/pgvector.git#${PGVECTOR_COMMIT} /tmp/pgvector

RUN apk add --no-cache --virtual .pgvector-build-deps build-base $DOCKER_PG_LLVM_DEPS \
    && make -C /tmp/pgvector clean \
    && make -C /tmp/pgvector OPTFLAGS="" \
    && make -C /tmp/pgvector install \
    && rm -rf /tmp/pgvector \
    && apk del .pgvector-build-deps
