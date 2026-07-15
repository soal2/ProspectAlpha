FROM pgvector/pgvector:0.8.2-pg16

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

RUN cd /tmp \
    && git clone https://github.com/jaiminpan/pg_jieba.git \
    && cd pg_jieba \
    && git submodule update --init --recursive \
    && mkdir build \
    && cd build \
    && cmake .. -DPostgreSQL_TYPE_INCLUDE_DIR=/usr/include/postgresql/16/server \
    && make \
    && make install \
    && rm -rf /tmp/pg_jieba

RUN mkdir -p /docker-entrypoint-initdb.d
COPY docker/postgres/init-extensions.sql /docker-entrypoint-initdb.d/01-init-extensions.sql
