FROM --platform=$BUILDPLATFORM golang:1.26.0-alpine AS parser-build
WORKDIR /src
COPY document-parser/go.mod document-parser/go.sum ./
RUN go mod download
COPY document-parser/cmd ./cmd
COPY document-parser/internal ./internal
COPY version ./version
ARG TARGETOS=linux
ARG TARGETARCH
ARG APP_VERSION
RUN test "$APP_VERSION" = "$(cat version)" \
    && CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath \
       -ldflags "-s -w -X main.version=$APP_VERSION" -o /out/document-parser ./cmd/document-parser

FROM alpine:3.22
RUN addgroup -S -g 10002 parser \
    && adduser -S -D -u 10002 -G parser parser
WORKDIR /app
COPY --from=parser-build /out/document-parser /usr/local/bin/document-parser
COPY document-parser/third_party /app/third_party
USER parser
EXPOSE 8080
CMD ["document-parser", "serve"]
HEALTHCHECK --interval=10s --timeout=3s --retries=10 CMD ["document-parser", "healthcheck"]
ARG APP_VERSION
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="AI Секретарь — парсер документов" \
    org.opencontainers.image.source="https://github.com/mumg/ai_secretary" \
    org.opencontainers.image.version="${APP_VERSION}" \
    org.opencontainers.image.revision="${SOURCE_REVISION}"
