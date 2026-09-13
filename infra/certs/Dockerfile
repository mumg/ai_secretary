FROM alpine:3.22.1

RUN apk upgrade --no-cache && apk add --no-cache openssl
COPY init-certs.sh /usr/local/bin/init-certs
RUN chmod 0755 /usr/local/bin/init-certs

ENTRYPOINT ["/usr/local/bin/init-certs"]
