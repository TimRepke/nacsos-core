# NACSOS Core
[![Volkswagen status](.ci/volkswargen_ci.svg)](https://github.com/auchenberg/volkswagen)

This repository contains the core data management platform of NACSOS.
It accesses the database via the `nacsos-data` package and exposes the functionality via an API.
It also serves the web frontend.

```
pg_dump -d nacsos_core -h localhost -U root -W -p 5432 > dump.sql
```
 
## Installation
- Requires Python 3.10+, tested with Python 3.10.2

```bash
virtualenv venv
source venv/bin/activate
pip install -r requirements.txt
```

For development, it is advised to install `nacsos-data` locally (not from git) via
```bash
pip install -e ../nacsos-data/
```
(assuming both projects reside side-by-side, otherwise adapt path accordingly)
If you do so, please keep in mind to update the requirements and temporarily commenting out the respective line in `requirements.txt` when installing the other requirements!

## Running the database with docker
Start up the database by running docker (or use your local instance)
```bash
sudo systemctl start docker
docker-compose up -d
```

## Starting the server
```bash
# set this in case you want to use a different config (optional)
export NACSOS_CONFIG=config/default.env

# for development, using the --reload option is helpful
hypercorn --config=config/hypercorn.toml --reload main:app 
```

The configuration is read in the following order (and overridden by consecutive steps):
1. Classes in `server/util/config.py`
2. .env config file (whatever is in `NACSOS_CONFIG`; defaulting to `config/default.env`)
3. Environment variables

The default config is set up to work with a locally running docker instance with its respective default config.
It should never be changed, always make a local copy and never commit it to the repository!

## Pipelines
Celery systemd:
```bash
$ cat /etc/systemd/system/nacsos2-celery@.service

[Unit]
Description=NACSOS2 Celery Service (%i)
After=network.target

[Service]
Type=simple
User=nacsos
Group=nacsos

WorkingDirectory=/var/www/nacsos2/nacsos-core
EnvironmentFile=/var/www/nacsos2/celery-%i.conf

ExecStart=/var/www/nacsos2/nacsos-core/venv/bin/celery -A ${CELERY_APP} worker --pidfile=${CELERYD_PID_FILE} --logfile=${CELERYD_LOG_FILE}  --loglevel="${CELERYD_LOG_LEVEL}" $CELERYD_OPTS
#ExecStop=/var/www/nacsos2/venv/bin/celery worker stopwait --pidfile=${CELERYD_PID_FILE} --logfile=${CELERYD_LOG_FILE} --loglevel="${CELERYD_LOG_LEVEL}"
#ExecReload=/var/www/nacsos2/venv/bin/celery -A $CELERY_APP worker restart $CELERYD_NODES --pidfile=${CELERYD_PID_FILE} --logfile=${CELERYD_LOG_FILE} --loglevel="${CELERYD_LOG_LEVEL}" $CELERYD_OPTS

Restart=always
RestartSec=60s

[Install]
WantedBy=multi-user.target
```

/var/www/nacsos1/celery-default.conf
```dotenv
CELERY_APP="BasicBrowser"

# Absolute or relative path to the 'celery' command:
CELERY_BIN="/var/www/nacsos1/venv/bin/celery"

CELERYBEAT_USER=nacsos
CELERYBEAT_GROUP=nacsos

# Extra command-line arguments to the worker
CELERYD_OPTS="--concurrency=4 -Q default -P threads -l info -n worker --time-limit=86400"

CELERYD_LOG_FILE=/var/www/nacsos1/logs/celery-default-%n%I.log
#CELERYD_PID_FILE=/var/www/nacsos1/celery-default-%n.pid
CELERYD_PID_FILE=/var/www/nacsos1/celery-default-celery.pid

CELERYD_LOG_LEVEL="INFO"

CELERYBEAT_LOG_FILE=/var/www/nacsos1/logs/celery-beat-default.log
CELERYBEAT_PID_FILE=/var/www/nacsos1/celery-beat-default.pid
```