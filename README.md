# NACSOS Core
[![Volkswagen status](.ci/volkswargen_ci.svg)](https://github.com/auchenberg/volkswagen)

This repository contains the core data management platform of NACSOS.
It accesses the database via the `nacsos-data` package and exposes the functionality via an API.
It also serves the web frontend.

## Endpoints
When using the docker-compose setup, you will reach 
* pgadmin: http://localhost:5050

```
pg_dump -d nacsos_core -h localhost -U root -W -p 5432 > dump.sql
```
 
## Installation
- Requires Python 3.12+, tested with Python 3.12.2

```bash
virtualenv venv
source venv/bin/activate
pip install -r requirements_dev.txt
```

For development, it is advised to install `nacsos-data` locally (not from git) via
```bash
pip install -e  "../nacsos_data[utils,scripts]"
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

# pipeline workers (if needed)
dramatiq server.pipelines.tasks -t 2 -p 2 -Q default nacsos-pipes

# optional dramatiq dashboard
hypercorn drama:app
```

The configuration is read in the following order (and overridden by consecutive steps):
1. Classes in `server/util/config.py`
2. .env config file (whatever is in `NACSOS_CONFIG`; defaulting to `config/default.env`)
3. Environment variables

The default config is set up to work with a locally running docker instance with its respective default config.
It should never be changed, always make a local copy and never commit it to the repository!

Note, you may need to install `apt install python3.12-dev libsystemd-dev`.

```
[Unit]
Description=dramatiq workers
After=network.target

[Service]
Type=simple
User=nacsos
Group=nacsos
Environment="NACSOS_CONFIG=/var/www/nacsos2/nacsos-core/config/server.env"
WorkingDirectory=/var/www/nacsos2/nacsos-core
#ExecStart=/var/www/nacsos2/nacsos-core/venv/bin/dramatiq dramatiq server.pipelines.tasks -t 2 -p 3 -Q default nacsos-pipes --watch /var/www/nacsos2/nacsos-core/server --pid-file /var/www/nacsos2/dramatiq.pid  
ExecStart=/var/www/nacsos2/nacsos-core/venv/bin/dramatiq dramatiq server.pipelines.tasks -t 2 -p 3 -Q default nacsos-pipes --pid-file /var/www/nacsos2/dramatiq.pid
Restart=always
RestartSec=30s
PIDFile=/var/www/nacsos2/dramatiq.pid
KillMode=process
KillSignal=SIGHUP
TimeoutStopSec=30
FinalKillSignal=SIGKILL

[Install]
WantedBy=multi-user.target
```

## Testing database things in the console
```
from sqlalchemy import select
from nacsos_data.db import get_engine
from nacsos_data.db.schemas.users import User
engine = get_engine('config/remote.env')
with engine.session() as session:
    users = session.execute(select(User.email, User.full_name, User.username)
                                       .where(User.setting_newsletter == False,
                                              User.is_active == True)).mappings().all()
    print(users[0])
```