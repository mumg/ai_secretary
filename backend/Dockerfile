FROM --platform=$BUILDPLATFORM golang:1.26.0-alpine AS server-build
WORKDIR /src
COPY backend/go.mod backend/go.sum ./
RUN go mod download
COPY backend/cmd ./cmd
COPY backend/internal ./internal
COPY version ./version
ARG TARGETOS=linux
ARG TARGETARCH
ARG APP_VERSION
RUN test "$APP_VERSION" = "$(cat version)" \
    && CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath \
       -ldflags "-s -w -X main.version=$APP_VERSION" -o /out/improver ./cmd/improver

FROM python:3.13.7-alpine AS web-build
COPY backend/web /web
COPY browser-extension /extension
RUN python /extension/build.py --output /tmp/extension.zip --backend-web /web

FROM alpine:3.22
RUN apk add --no-cache ca-certificates \
    && addgroup -S -g 10001 improver \
    && adduser -S -D -u 10001 -G improver improver \
    && mkdir -p /data/attachments /app \
    && chown -R improver:improver /data /app
WORKDIR /app
COPY --from=server-build /out/improver /usr/local/bin/improver
COPY --from=web-build /web /app/web
COPY backend/third_party /app/third_party
USER improver
ENV WEB_DIR=/app/web LISTEN_ADDR=0.0.0.0:8000
ARG APP_VERSION
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="AI Секретарь" \
    org.opencontainers.image.source="https://github.com/mumg/ai_secretary" \
    org.opencontainers.image.version="${APP_VERSION}" \
    org.opencontainers.image.revision="${SOURCE_REVISION}"
EXPOSE 8000
CMD ["improver", "serve"]
