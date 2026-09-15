# syntax=docker/dockerfile:1.27.0@sha256:bde3983e9c939224420ddaf6b784cc30e09b035a4dea01f581230c50809f372e
FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648

# The upstream image gives Caddy a file capability for privileged ports.  Our
# Compose profiles instead manage that capability explicitly: the free-tier
# edge adds NET_BIND_SERVICE, while the workstation edge listens on 8080 and
# drops every capability.  Keeping the file capability makes Docker Desktop
# reject exec when it is outside the container's capability bounding set.
RUN apk upgrade --no-cache \
    && setcap -r /usr/bin/caddy
