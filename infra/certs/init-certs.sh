#!/bin/sh
set -eu

public_host="${PUBLIC_HOST:-localhost}"
mkdir -p /certs

if [ ! -s /certs/client-ca.key ] || [ ! -s /certs/client-ca.crt ]; then
  openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout /certs/client-ca.key \
    -out /certs/client-ca.crt \
    -subj "/CN=Improver Client CA"
fi

if [ ! -s /certs/server.key ] || [ ! -s /certs/server.crt ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days 30 -nodes \
    -keyout /certs/server.key \
    -out /certs/server.crt \
    -subj "/CN=${public_host}" \
    -addext "subjectAltName=DNS:${public_host},DNS:localhost,IP:127.0.0.1"
fi

if [ ! -s /certs/dev-client.key ] || [ ! -s /certs/dev-client.crt ]; then
  openssl req -newkey rsa:3072 -nodes \
    -keyout /certs/dev-client.key \
    -out /certs/dev-client.csr \
    -subj "/CN=Improver Development Device"
  openssl x509 -req -sha256 -days 365 \
    -in /certs/dev-client.csr \
    -CA /certs/client-ca.crt \
    -CAkey /certs/client-ca.key \
    -CAcreateserial \
    -out /certs/dev-client.crt
  # Android's platform provider cannot open PKCS#12 files using every modern
  # OpenSSL 3 MAC/PBES combination. Legacy export keeps the development bundle
  # compatible while TLS itself still negotiates current algorithms.
  openssl pkcs12 -export -legacy \
    -out /certs/dev-client.p12 \
    -inkey /certs/dev-client.key \
    -in /certs/dev-client.crt \
    -certfile /certs/client-ca.crt \
    -passout pass:changeit
fi

chmod 0600 /certs/*.key /certs/*.p12
