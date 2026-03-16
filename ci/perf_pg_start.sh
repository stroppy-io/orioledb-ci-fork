#!/bin/bash

set -eux

export PATH="$GITHUB_WORKSPACE/pgsql/bin:$PATH"

PGDATA="$GITHUB_WORKSPACE/pgdata"

# Initialize PostgreSQL
rm -rf "$PGDATA"
initdb -N --encoding=UTF-8 --locale=C -D "$PGDATA"

# Configure for benchmarks
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
SHARED_BUFFERS_MB=$(( TOTAL_MEM_KB / 4 / 1024 ))

cat >> "$PGDATA/postgresql.conf" <<EOF
listen_addresses = 'localhost'
shared_preload_libraries = 'orioledb'
default_table_access_method = 'orioledb'
shared_buffers = '${SHARED_BUFFERS_MB}MB'
max_connections = 200
max_wal_size = '4GB'
checkpoint_completion_target = 0.9
EOF

# Start PostgreSQL
if ! pg_ctl -D "$PGDATA" -l "$PGDATA/postgresql.log" start; then
  echo "=== PostgreSQL failed to start. Log: ==="
  cat "$PGDATA/postgresql.log"
  exit 1
fi

# Wait for PostgreSQL to be ready
if ! pg_isready -t 30; then
  echo "=== PostgreSQL not ready. Log: ==="
  cat "$PGDATA/postgresql.log"
  exit 1
fi

# Register the OrioleDB extension
psql -d postgres -c "CREATE EXTENSION IF NOT EXISTS orioledb;"

