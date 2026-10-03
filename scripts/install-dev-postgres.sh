#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=17.11
SOURCE_DIR="$ROOT/.local/src/postgresql-$VERSION"
ARCHIVE="$ROOT/.local/src/postgresql-$VERSION.tar.bz2"
PREFIX="$ROOT/.local/postgresql-17"
JOBS=${PG_BUILD_JOBS:-2}

if [ -x "$PREFIX/bin/initdb" ]; then
  "$PREFIX/bin/postgres" --version
  exit 0
fi

mkdir -p "$ROOT/.local/src"
curl -fsSL "https://ftp.postgresql.org/pub/source/v$VERSION/postgresql-$VERSION.tar.bz2" -o "$ARCHIVE"
tar -xjf "$ARCHIVE" -C "$ROOT/.local/src"
cd "$SOURCE_DIR"
./configure --prefix="$PREFIX" --without-readline --without-zlib --without-icu
make -j"$JOBS"
make install
"$PREFIX/bin/postgres" --version
