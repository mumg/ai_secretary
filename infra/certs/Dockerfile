FROM alpine:3.22.1

RUN apk upgrade --no-cache && apk add --no-cache openssl
COPY init-certs.sh /usr/local/bin/init-certs
RUN chmod 0755 /usr/local/bin/init-certs

ENTRYPOINT ["/usr/local/bin/init-certs"]

ARG APP_VERSION=unknown
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="AI Секретарь — инициализация сертификатов" \
    org.opencontainers.image.source="https://github.com/mumg/ai_secretary" \
    org.opencontainers.image.version="${APP_VERSION}" \
    org.opencontainers.image.revision="${SOURCE_REVISION}"
