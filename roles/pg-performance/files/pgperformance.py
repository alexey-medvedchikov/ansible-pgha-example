#!/usr/bin/env python

from __future__ import print_function

import jinja2 as j2
import yaml as y
import psycopg2 as pg
import time
import errno
import os

CONFIG_PATHS = (
    './pgperformance.yml',
    '/etc/pgperformance.yml',
    '/usr/local/etc/pgperformance.yml'
)

INDEX_TEMPLATE = """
{% macro render_tabs(caption, name, tabs) %}
<div class="tabbed">
    <span>{{ caption }}</span>
    {% for tab in tabs %}
    <input type="radio" name="{{ name }}" id="{{ name }}-{{ loop.index }}" {{ 'checked' if loop.first }} />
    <label for="{{ name }}-{{ loop.index }}">{{ tab }}</label>
    {% endfor -%}
    {{ caller() }}
</div>
{% endmacro %}

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/latest/css/bootstrap.min.css" />
    <style>
        .tabbed > div, .tabbed > input { display: none; }
        .tabbed label { padding: 5px; border: 1px solid #aaa;
            line-height: 28px; cursor: pointer; position: relative; bottom: 1px }
        .tabbed input[type="radio"]:checked + label { background: #ddd; }
        {% for i in range(1, 20) %}
            .tabbed > input:nth-of-type({{ i }}):checked ~ div:nth-of-type({{ i }}){{ ',' if not loop.last }}
        {% endfor %}
        { display: block; padding: 5px; border: 1px solid #aaa; }
    </style>
    <title></title>
</head>
<body>
    <div class="container-fluid">
    <div><h2>Report generated: {{ now }}</h2></div>
    {% set l1_name = 'tabbed' %}
    {% set l1_tabs = metrics|map(attribute='name') %}
    {% call render_tabs('Servers:', l1_name, l1_tabs) %}
        {% for server in metrics %}
        <div>
        {% set l2_name = '{}-{}'.format(l1_name, loop.index) %}
        {% set l2_tabs = server.databases|map(attribute='name') %}
        {% call render_tabs('Databases:', l2_name, l2_tabs) %}
            {% for database in server.databases %}
            <div>
            {% set l3_name = '{}-{}'.format(l2_name, loop.index) %}
            {% set l3_tabs = database.checks|map(attribute='name') %}
            {% call render_tabs('Checks:', l3_name, l3_tabs) %}
                {% for check in database.checks %}
                <div>
                <p>{{ check.desc }}</p>
                {{ check.content }}
                </div>
                {% endfor %}
            {% endcall %}
            </div>
            {% endfor %}
        {% endcall %}
        </div>
        {% endfor %}
    {% endcall %}
    </div>
</body>
</html>
"""

TABLE_TEMPLATE = """
<table class="table table-condensed table-hover">
    <thead>
        <tr>
            {% for col in columns %}
            <th>{{ col.name }}</th>
            {% endfor %}
        </tr>
    </thead>
    <tbody>
        {% for row in rows %}
        <tr>
            {% for cell in row %}
            <td>{{ cell|trim }}</td>
            {% endfor %}
        </tr>
        {% endfor %}
    </tbody>
</table>
"""

class Check(object):
    def __init__(self, name, desc, sql, availability=True, reset=False):
        self._name = name
        self._desc = desc
        self._sql = sql
        self._reset = reset
        self._availability = availability

    def available(self, cursor):
        if self._availability is True:
            return True
        cursor.execute(self._availability)
        result = cursor.fetchall()
        return bool(result[0][0])

    def reset(self, cursor):
        if not self._reset:
            return
        cursor.execute(self._reset)

    def render(self, cursor):
        table_tpl = j2.Template(TABLE_TEMPLATE)
        cursor.execute(self._sql)
        columns = cursor.description
        rows = cursor.fetchall()
        self.reset(cursor)
        content = table_tpl.render(columns=columns, rows=rows)
        return {'name': self._name, 'desc': self._desc, 'content': content}


CHECKS = (
Check(
    name='Autovacuum stat',
    desc='Shows dead rows and whether an automatic vacuum is expected to be triggered',
    sql="""
        WITH table_opts AS (
          SELECT
            pg_class.oid, relname, nspname, array_to_string(reloptions, '') AS relopts
          FROM
             pg_class INNER JOIN pg_namespace ns ON relnamespace = ns.oid
        ), vacuum_settings AS (
          SELECT
            oid, relname, nspname,
            CASE
              WHEN relopts LIKE '%autovacuum_vacuum_threshold%'
                THEN regexp_replace(relopts, '.*autovacuum_vacuum_threshold=([0-9.]+).*', E'\\\\\\1')::integer
                ELSE current_setting('autovacuum_vacuum_threshold')::integer
              END AS autovacuum_vacuum_threshold,
            CASE
              WHEN relopts LIKE '%autovacuum_vacuum_scale_factor%'
                THEN regexp_replace(relopts, '.*autovacuum_vacuum_scale_factor=([0-9.]+).*', E'\\\\\\1')::real
                ELSE current_setting('autovacuum_vacuum_scale_factor')::real
              END AS autovacuum_vacuum_scale_factor
          FROM
            table_opts
        )
        SELECT
          vacuum_settings.nspname AS schema,
          vacuum_settings.relname AS table,
          to_char(psut.last_vacuum, 'YYYY-MM-DD HH24:MI') AS last_vacuum,
          to_char(psut.last_autovacuum, 'YYYY-MM-DD HH24:MI') AS last_autovacuum,
          to_char(pg_class.reltuples, '9G999G999G999') AS rowcount,
          to_char(psut.n_dead_tup, '9G999G999G999') AS dead_rowcount,
          to_char(autovacuum_vacuum_threshold
               + (autovacuum_vacuum_scale_factor::numeric * pg_class.reltuples), '9G999G999G999') AS autovacuum_threshold,
          CASE
            WHEN autovacuum_vacuum_threshold + (autovacuum_vacuum_scale_factor::numeric * pg_class.reltuples) < psut.n_dead_tup
            THEN 'yes'
          END AS expect_autovacuum
        FROM
          pg_stat_user_tables psut
        INNER JOIN
          pg_class ON psut.relid = pg_class.oid
        INNER JOIN
          vacuum_settings ON pg_class.oid = vacuum_settings.oid
        ORDER BY 1;"""
),
Check(
    name='DB bloat',
    desc='Shows table and index bloating ordered by most waste rate',
    sql="""
        WITH constants AS (
          SELECT current_setting('block_size')::numeric AS bs, 23 AS hdr, 4 AS ma
        ), bloat_info AS (
          SELECT
            ma,bs,schemaname,tablename,
            (datawidth+(hdr+ma-(case when hdr%ma=0 THEN ma ELSE hdr%ma END)))::numeric AS datahdr,
            (maxfracsum*(nullhdr+ma-(case when nullhdr%ma=0 THEN ma ELSE nullhdr%ma END))) AS nullhdr2
          FROM (
            SELECT
              schemaname, tablename, hdr, ma, bs,
              SUM((1-null_frac)*avg_width) AS datawidth,
              MAX(null_frac) AS maxfracsum,
              hdr+(
                SELECT 1+count(*)/8
                FROM pg_stats s2
                WHERE null_frac<>0 AND s2.schemaname = s.schemaname AND s2.tablename = s.tablename
              ) AS nullhdr
            FROM pg_stats s, constants
            GROUP BY 1,2,3,4,5
          ) AS foo
        ), table_bloat AS (
          SELECT
            schemaname, tablename, cc.relpages, bs,
            CEIL((cc.reltuples*((datahdr+ma-
              (CASE WHEN datahdr%ma=0 THEN ma ELSE datahdr%ma END))+nullhdr2+4))/(bs-20::float)) AS otta
          FROM bloat_info
          JOIN pg_class cc ON cc.relname = bloat_info.tablename
          JOIN pg_namespace nn ON cc.relnamespace = nn.oid AND nn.nspname = bloat_info.schemaname AND nn.nspname <> 'information_schema'
        ), index_bloat AS (
          SELECT
            schemaname, tablename, bs,
            COALESCE(c2.relname,'?') AS iname, COALESCE(c2.reltuples,0) AS ituples, COALESCE(c2.relpages,0) AS ipages,
            COALESCE(CEIL((c2.reltuples*(datahdr-12))/(bs-20::float)),0) AS iotta -- very rough approximation, assumes all cols
          FROM bloat_info
          JOIN pg_class cc ON cc.relname = bloat_info.tablename
          JOIN pg_namespace nn ON cc.relnamespace = nn.oid AND nn.nspname = bloat_info.schemaname AND nn.nspname <> 'information_schema'
          JOIN pg_index i ON indrelid = cc.oid
          JOIN pg_class c2 ON c2.oid = i.indexrelid
        )
        SELECT
          type, schemaname, object_name, bloat, pg_size_pretty(raw_waste) as waste
        FROM
        (SELECT
          'table' as type,
          schemaname,
          tablename as object_name,
          ROUND(CASE WHEN otta=0 THEN 0.0 ELSE table_bloat.relpages/otta::numeric END,1) AS bloat,
          CASE WHEN relpages < otta THEN '0' ELSE (bs*(table_bloat.relpages-otta)::bigint)::bigint END AS raw_waste
        FROM
          table_bloat
            UNION
        SELECT
          'index' as type,
          schemaname,
          tablename || '::' || iname as object_name,
          ROUND(CASE WHEN iotta=0 OR ipages=0 THEN 0.0 ELSE ipages/iotta::numeric END,1) AS bloat,
          CASE WHEN ipages < iotta THEN '0' ELSE (bs*(ipages-iotta))::bigint END AS raw_waste
        FROM
          index_bloat) bloat_summary
        ORDER BY raw_waste DESC, bloat DESC;"""
),
Check(
    name='Index usage',
    desc="""Shows unused and almost unused indexes, ordered by their size
    relative to the number of index scans. Exclude indexes of very small tables
    (less than 5 pages), where the planner will almost invariably select a
    sequential scan, but may not in the future as the table grows.""",
    sql= """
        SELECT
            schemaname AS schema_name,
            relname AS table_name,
            indexrelname AS index_name,
            pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size,
            idx_scan AS index_scans
        FROM
            pg_stat_user_indexes ui
        JOIN
            pg_index i ON ui.indexrelid = i.indexrelid
        WHERE
            NOT indisunique
                AND idx_scan < 50
                AND pg_relation_size(relid) > 5 * 8192
        ORDER BY
            pg_relation_size(i.indexrelid) / nullif(idx_scan, 0) DESC NULLS FIRST,
            pg_relation_size(i.indexrelid) DESC;"""
),
Check(
    name='Index hits',
    desc='Calculates your index hit rate (effective databases are at 99% and up) and size',
    sql="""
        SELECT
            schemaname AS schema_name,
            relname AS index_name,
            CASE idx_scan
                WHEN 0 THEN NULL
                ELSE ROUND(idx_scan::numeric / (seq_scan + idx_scan), 3)
            END hit_ratio,
            n_live_tup rows_in_table,
            pg_size_pretty(pg_table_size(relid)) AS size
        FROM
            pg_stat_user_tables
        ORDER BY
            hit_ratio ASC;"""
),
Check(
    name='Index cache',
    desc='Indices in-memory cache hit rate (effective databases are at 99% and up) and size',
    sql="""
        SELECT
            schemaname AS schema_name,
            indexrelname AS index_name,
            pg_size_pretty(pg_table_size(relid)) AS size,
            ROUND(idx_blks_hit::numeric / nullif(idx_blks_hit + idx_blks_read,0), 3) AS hit_ratio
        FROM
            pg_statio_user_indexes
        ORDER BY
            pg_table_size(relid) DESC;""",
    reset="""
        SELECT
            pg_stat_reset_single_table_counters(indexrelid)
        FROM
            pg_stat_user_indexes;"""
),
Check(
    name='Table cache',
    desc='Tables in-memory cache hit rate (effective databases are at 99% and up) and size',
    sql="""
        SELECT
            schemaname AS schema_name,
            relname AS table_name,
            pg_size_pretty(pg_table_size(relid)) AS size,
            ROUND(heap_blks_hit::numeric / nullif(heap_blks_hit + heap_blks_read,0), 3) AS hit_ratio
        FROM
            pg_statio_user_tables
        ORDER BY
            pg_table_size(relid) DESC;""",
    reset="""
        SELECT
            pg_stat_reset_single_table_counters(relid)
        FROM
            pg_stat_user_tables;"""
),
Check(
    name='Query stats',
    desc='Shows queries took most time to execute',
    sql="""
        SELECT
            query,
            calls,
            total_time,
            ROUND(total_time::numeric / calls, 3) AS per_call,
            ROUND(rows::numeric / calls, 0) AS rows_per_call,
            ROUND(shared_blks_hit::numeric / nullif(shared_blks_hit + shared_blks_read, 0), 3) AS cache_hit_ratio,
            pg_size_pretty(ROUND(temp_blks_read * current_setting('block_size')::numeric / calls, 1)) AS temp_bytes_read_per_call,
            pg_size_pretty(ROUND(temp_blks_written * current_setting('block_size')::numeric / calls, 1)) AS temp_bytes_written_per_call
        FROM
            pg_stat_statements
        ORDER BY
            total_time DESC
        LIMIT 100;
        """,
    availability="""
        SELECT EXISTS (
            SELECT
                1
            FROM
                pg_catalog.pg_class c
            JOIN
                pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE
                n.nspname = 'public' AND c.relname = 'pg_stat_statements'
        );""",
    reset="""
        SELECT
            pg_stat_statements_reset();"""
),
Check(
    name='Bgwriter statistics',
    desc='Shows bgwriter statistics',
    sql="""
        SELECT
            *
        FROM
            pg_stat_bgwriter;""",
    reset="""
        SELECT
            pg_stat_reset_shared('bgwriter');"""
))


def config_load(paths):
    """
    Load and validate configuration:

    servers: <- mandatory, non-empty
      - name: name <- mandatory
        address: address <- mandatory
        user: user <- mandatory
        password: password <- mandatory
    """
    for path in paths:
        try:
            with open(path, 'r') as fp:
                config = y.load(fp)
            break
        except IOError as e:
            if e.errno == errno.ENOENT:
                pass
    else:
        errmsg = "No config file found in following paths:\n{}".format("\n".join(CONFIG_PATHS))
        raise Exception(errmsg)
    assert config.get('servers'), 'No servers section present'
    assert len(config['servers']), 'Empty servers section'
    for i, server in enumerate(config['servers'], start=1):
        for attr in 'name', 'address', 'user', 'password':
            assert server.get(attr), 'No {} attribute for server #{}'.format(attr, i)
    return config


def metrics_fetch(servers):
    """
    Constructs data object for template rendering

    [ # List of servers
        {
            'name': ...
            'databases': [ # List of databases
                'name': ...
                'checks': [ # List of checks
                    'name': ...
                    'content': ...
                ]
            ]
        }
    ]
    """
    servers_metrics = []
    for srv in servers:
        databases_metrics = []
        srv_conn = pg.connect(host=srv['address'], user=srv['user'], password=srv['password'])
        srv_cursor = srv_conn.cursor()
        srv_cursor.execute('SELECT datname FROM pg_database WHERE NOT datistemplate')
        databases = [row[0] for row in srv_cursor.fetchall()]
        srv_cursor.close()
        srv_conn.close()
        for db in databases:
            conn = pg.connect(host=srv['address'], user=srv['user'], database=db, password=srv['password'])
            cursor = conn.cursor()
            checks_metrics = []
            for check in CHECKS:
                if check.available(cursor):
                    content = check.render(cursor)
                    checks_metrics.append(content)
            cursor.close()
            conn.close()
            databases_metrics.append({'name': db, 'checks': checks_metrics})
        srv_name = '{} ({})'.format(srv['name'], srv['address'])
        servers_metrics.append({'name': srv_name, 'databases': databases_metrics})
    return servers_metrics


def main():
    config = config_load(CONFIG_PATHS)
    index_tpl = j2.Template(INDEX_TEMPLATE)
    metrics = metrics_fetch(config['servers'])
    now = time.strftime('%Y.%m.%d %H:%M:%S')
    print(index_tpl.render(metrics=metrics, now=now))


if __name__ == '__main__':
    main()
