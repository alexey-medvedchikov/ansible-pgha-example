# PostgreSQL high-available installation example

## Решаемые задачи

* Обеспечение отказоустойчивости (переключение на новый master при падении)
* Упрощение администрирования кластера
* Резервное копирование данных БД
* Унификация решения для всех команд

## Общая схема кластера

```
                                              Clients
                                                 |
                                                 v
                                          +--------------+
                                        +---------------+|
                                        | Load-balancer ||
                                        | +-----------+ ||
                                        | | PgBouncer | ||
                                        | +-----+-----+ ||
                                        |       |       ||
                                        |  +----+----+  ||
                                        |  | PgPool2 |  ||
                                        |  +-+-------+  ++
                                        +----------+----+
                                             |     |
                              +--------------+     +-------------+
                              |                                  v
                              v                          +--------------+
                       +------+-------+                 +--------------+|
+------------------+   |   Database:  |                 |  Database:   ||
|     Backup       |   |    master    |                 |   standby    ||
|                  |   |+------------+|Streaming replica|+------------+||
|+----------------+| +--+ PostgreSQL +------------------>+ PostgreSQL |||
||Barman (backups)+<-+ |+-----+------+|                 |+-----+------+||
|+----------------+| | |      |       |                 |      |       ||
| +-------------+  | | |  +---+----+  |                 |  +---+----+  ||
| | WAL Archive +<---+ |  | Repmgr |  |                 |  | Repmgr |  ||
| +------+------+  |   |  +--------+  |                 |  +--------+  ++
+------------------+   +------+-------+                 +-------+------+
         |                    ^                                 ^
         +--------------------+---------------------------------+
                       Recovery / WAL catchup
```

*   [PgBouncer][1]

    Мультиплексор соединений с возможностью 0-downtime замены бекендов

*   [PgPool2][2]

    Мультиплексор соединений обеспечивающий разделение Read/Write транзакций,
    балансировку, мониторинг streaming репликации.

*   [Repmgr][3]

    Менеджер streaming репликации упрощающий работу с replication slots,
    перенастройкой standby серверов на другой master. Обеспечивает
    автоматический failover в случае выхода master сервера из строя.

*   [Barman][4]

    Менеджер резервного копирования для PostgreSQL.

[1]: https://github.com/alexey-medvedchikov/ansible-pgbouncer-ng
[2]: https://github.com/alexey-medvedchikov/ansible-pgpool2
[3]: https://github.com/alexey-medvedchikov/ansible-repmgr
[4]: https://github.com/alexey-medvedchikov/ansible-barman

## Кастомизация под конкретный сервис

### Первичный деплой

Для создания кластера нужно выставить переменную `repmgr_bootstrap: yes`.
*Внимание: это деструктивная операция! Т.ч. лучше её делать через флаги
`ansible-playbook -e repmgr_bootstrap=yes`*.

Настройка ролей осуществляется заданием переменных `repmgr_is_master`
(выставляется в yes у мастера, default - no) и `repmgr_master_node` (нужна
для standby repmgr клонирования) выставляемая в IP-адрес мастера.

### Создание пользователей БД

1.  В файле [postgresql.yml](playbooks/group_vars/all/postgresql.yml) в
    переменной `__postgresql_users` добавить необходимых пользователей БД.
    *Nota Bene:* Пользователь `postgres` должен присутствовать всегда.

2.  В файле [secure.yml](playbooks/group_vars/all/secure.yml) изменить
    переменную `postgresql_postgres_passwd` и добавить переменные паролей
    пользователей БД, которые были определены в пункте 1.
    *   Как получить .vault_password в расшифрованном виде проще всего описано в
        [.gitlab-ci.yml](.gitlab-ci.yml), секция `before_script`. Заполучив
        расшифрованный `.vault_password` при помощи `ansible-vault` меняем
        содержимое [secure.yml](playbooks/group_vars/all/secure.yml).

### Выделение ресурсов под кластер

Минимальная конфигурация кластера должна быть:

*   2 лоадбалансера
*   3 сервера БД
*   *Optional*. Желательно, но не обязательно, наличие сервера бэкапов

Выделение ресурсов под каждый сервер БД определяется из требований заказчика
результатов нагрузочного тестирования сервиса. Для серверов лоадбалансера
ресурсы выделяются в зависимости от результатов нагрузочного тестирования.

## Восстановление после сбоев

### Q: Отставание репликации / конфликт с репликацией

Необходимо зайти на master-сервер и проверить его состояние:

```shell
pg_isready -U postgres

/var/run/postgresql:5432 - accepting connections
```

Проверить состояние standby-серверов:

```shell
echo 'select pg_current_xlog_location(); select * from pg_stat_replication;' \
    | psql -U postgres -x
-[ RECORD 1 ]------------+-------------
pg_current_xlog_location | E0F/9279F770

-[ RECORD 1 ]----+------------------------------
pid              | 13364
usesysid         | 10
usename          | postgres
application_name | sync_replication
client_addr      | 10.54.255.20
client_hostname  |
client_port      | 18494
backend_start    | 2015-12-04 04:18:27.036608+06
backend_xmin     | 890938759
state            | streaming
sent_location    | E0F/9279F770
write_location   | E0F/9279F770
flush_location   | E0F/9279F770
replay_location  | E0F/9279F770
sync_priority    | 1
sync_state       | sync
-[ RECORD 2 ]----+------------------------------
pid              | 13365
usesysid         | 10
usename          | postgres
application_name | sync_replication
client_addr      | 10.54.255.21
client_hostname  |
client_port      | 28165
backend_start    | 2015-12-04 04:18:27.041218+06
backend_xmin     | 890938759
state            | streaming
sent_location    | E0F/9279F770
write_location   | E0F/9279F770
flush_location   | E0F/9279F770
replay_location  | E0F/9279F770
sync_priority    | 1
sync_state       | potential
...
```

Здесь мы смотрим на client_addr и количество подключенных серверов. А также
на state (streaming - репликация работает нормально, catchup -
сервер догоняет текущую репликацию, backup - процесс производит basebackup,
startup - репликация только началась) и sync_state (async - асинхронная
репликация, sync - синхронный standby (в PG=<9.5 может быть только один),
potential - потенциальный синхронный standby). Также надо обратить внимание
на разницу между pg_current_xlog_location и \*\_location на standby: всё это
может сказать о том, где возникла проблема.

Если сервер присутсвует в списке pg_stat_replication и разница в положении
xlog'а большая, то вполне возможно, что разработчики накатили огромную
транзакцию (DML, pg_repack, VACUUM FULL) и теперь вся система пытается
разгрести данные по standby серверам. Во избежание таких ситуаций разработчики
должны применять подобные изменения кусками, т.е. например делать
ALTER TABLE NOT NULL и заполнять новые колонки позже.

### Q: Не произошёл автоматический failover

Нужно сделать failover руками. Запинываем master, чтобы он больше не поднялся
во избежание split brain. Находим самый свежий standby, например так:

```shell
ansible dbs -K -i inventory/test -m shell -a 'psql -U postgres \
    -c "select pg_last_xlog_replay_location();"'
```

Выводим standby из режима recovery (пути могут меняться в зависимости от версии
PG и пути к кластеру, указаны дефолтные настройки для PG 9.4 и кластера
по-умолчанию):

```shell
sudo -u postgres /usr/lib/postgresql/9.4/bin/pg_ctl \
    -D /var/lib/postgresql/9.4/main promote
```

Переключаем каждый из standby на новый мастер, редактируя recovery.conf (лежит
в datadir), меняя primary_conninfo. После чего делаем restart сервера. После
каждого переключения проверяем состояние репликации (см. пред. Q) и делаем
attach либо скриптом (/etc/pgpool2/pgpool2_reattach.sh), либо руками
pcp_attach_node.

### Q: Восстановление упавшего master-сервера в качестве standby

```shell
invoke-rc.d postgresql stop
rm -rf /var/lib/postgresql/9.4/main
sudo -u postgres repmgr -f /etc/repmgr.conf --force --host <masternode> \
    --dbname repmgr --user repmgr --verbose standby clone
invoke-rc.d postgresql start
sudo -u postgres repmgr -f /etc/repmgr.conf standby unregister
sudo -u postgres repmgr -f /etc/repmgr.conf standby register
# Проверяем, что все работает
invoke-rc.d repmgrd restart
```

### Q: Восстановление упавшего standby-сервера

Аналогично предыдущему пункту.

### Q: Восстановление сервера из бекапов

1.  Изолируем рабочую часть кластера, если она осталась: выключаем реплику,
    останавливаем везде repmgr чтобы никто не вмешивался в востановление базы,
    ликвидируем возможность спонтанного восстановления серверов.

2.  Удалённое восстановление производится с сервера бекапов под пользователем
    barman (если не переназначен):
    ```shell
    barman recover --remote-ssh-command 'ssh postgres@host-addr' main latest \
    /var/lib/postgresql/9.4/main
    ```

## Мониторинг кластера

В кластере автоматически разворачивается zabbix-agent и настраиваются проверки
на мониторинг базы PostgreSQL. Необходимо загрузить шаблон
zabbix/postgresql-extended-template.xml в zabbix и прицепить его к
соответсвующим хостам БД.

На backup-ноде запущен сборщик runtime-статистики доступный по адресу
http://backupX:80/

### Доступность

Доступность кластера обеспечивается наличием автоматического переключения роли
мастер-сервера и несколькими stanbdy-серверами. Наиболее важные параметры:

Сервера БД:

* Нагрузка на сервер
* Использование оперативной памяти
* Свободное место на диске
* Режим работы сервера (master / recovery)
* Возможность отвечать на запросы (для master'а желательно проверять запись)

Сервера балансировщики:

* Стандартный OS runtime
* Наличие процесса PgBouncer
* Наличие процесса PgPool2

Cервера резервного копирования:

* Стандартный OS runtime
* Проверка того, что бекап сделан

### Производительность

* Стандартный linux runtime: IO, net & ram & cpu usage.
* Extension pg_stat_statements мониторит скорость выполнения запросов
* Extension pgstattuple мониторит состояние реляций в базе на уровне кортежей
* Extension pg_buffercache мониторит состояние кеша БД
* View pg_stat_user_\* содержит информацию об использовании таблиц и индексов
* View pg_statio_user_\* содержит статистику IO таблиц, индексов и т.п

### Резервное копирование

#### Мониторинг

Мониторинг резервного копирования должен осуществляться методом "Dead man's
switch". Т.е. успешный бекап не генерирует уведомлений, а отсутствие актуальных
данных резервной копии в назначенное время вызывают ошибку.

Необходимо следить за следующими характеристиками:

* Свободное место на серверах резервного копирования
* Время, затраченное на резервное копирование
* Актуальность бекапа (см. "Dead man's switch")
* Целостность бекапа (размер, также возможно запускать базу в режиме RO)
* Наличие и целостность WAL-архивов (при использовании PITR)

#### Регулярное развертывание

Как одно из средств проверки целостности резервной копии рекомендуется раз в
сутки (автоматически) разворачивать БД из неё и проверять целостность и
работоспособность базы.

