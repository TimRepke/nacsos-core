# NACSOS Core
This repository contains the core data management platform of NACSOS.
It accesses the database via the `nacsos-data` package and exposes the functionality via an API.
It also serves the web frontend.

## Installation
- Requires Python 3.10+, tested with Python 3.10.2

```bash
virtualenv venv
source venv/bin/activate
pip install -r requirements.txt
```

Double-check, that you get the correct version of starlette! https://github.com/tiangolo/fastapi/issues/4800

For development, it is advised to install `nacsos-data` locally (not from git) via
```bash
pip install -e ../nacsos-data/
```
(assuming both projects reside side-by-side, otherwise adapt path accordingly)

There needs to be a change to the hypercorn code as per https://gitlab.com/pgjones/hypercorn/-/merge_requests/70/diffs

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