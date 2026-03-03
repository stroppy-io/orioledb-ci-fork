#!/bin/bash

set -eux

export PATH="$GITHUB_WORKSPACE/pgsql/bin:$PATH"

RUN_NUMBER="${RUN_NUMBER:-1}"
RESULTS_DIR="$GITHUB_WORKSPACE/results"
PGDATA="$GITHUB_WORKSPACE/pgdata"
BENCH_DURATION="${BENCH_DURATION:-10m}"
SCALE_FACTOR="${SCALE_FACTOR:-1}"

mkdir -p "$RESULTS_DIR"

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
work_mem = '16MB'
checkpoint_completion_target = 0.9
EOF

# Start PostgreSQL
pg_ctl -D "$PGDATA" -l "$PGDATA/postgresql.log" start

# Wait for PostgreSQL to be ready
pg_isready -t 30

# Run single TPC-C benchmark
echo "=== Benchmark run ${RUN_NUMBER} ==="
docker run --rm --network host \
	-e DRIVER_URL="postgres://$(whoami)@localhost:5432/postgres" \
	-e DURATION="$BENCH_DURATION" \
	-e SCALE_FACTOR="$SCALE_FACTOR" \
	-e SQL_FILE="/workloads/tpcc/tpcc.sql" \
	ghcr.io/stroppy-io/stroppy \
	run /workloads/tpcc/tpcc.ts /workloads/tpcc/tpcc.sql \
	2>&1 | tee "$RESULTS_DIR/run_${RUN_NUMBER}.log"

# Stop PostgreSQL and clean up
pg_ctl -D "$PGDATA" stop
rm -rf "$PGDATA"
