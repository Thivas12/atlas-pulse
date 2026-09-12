# syntax=docker/dockerfile:1.7
FROM postgis/postgis:17-3.5 AS pgvector-build

ARG PGVECTOR_COMMIT=8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c
ADD https://github.com/pgvector/pgvector.git#${PGVECTOR_COMMIT} /tmp/pgvector

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential postgresql-server-dev-17 \
    && make -C /tmp/pgvector clean \
    && make -C /tmp/pgvector OPTFLAGS="" \
    && make -C /tmp/pgvector install

FROM postgis/postgis:17-3.5

COPY --from=pgvector-build /usr/lib/postgresql/17/lib/vector.so /usr/lib/postgresql/17/lib/
COPY --from=pgvector-build /usr/share/postgresql/17/extension/vector* /usr/share/postgresql/17/extension/
