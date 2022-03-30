# NACSOS Core
This repository contains the core data management platform of NACSOS.
It accesses the database via the `nacsos-data` package and exposes the functionality via an API.
It also serves the web frontend.

## Installation
- Requires Python 3.9+, tested with Python 3.10.2

```bash
virtualenv venv
source venv/bin/activate
pip install -r requirements.txt
``` 

At the moment, there is a breaking bug in tap, so you have to edit `tap/utils.py` in line 182 after installation:
```python
def get_class_column(obj: type) -> int:
    """Determines the column number for class variables in a class."""
    first_line = 1
    for token_type, token, (start_line, start_column), (end_line, end_column), line in tokenize_source(obj):
        if token.strip() == '@':
            first_line += 1
        if start_line <= first_line or token.strip() == '':
            continue

        return start_column
```
Keep track of https://github.com/swansonk14/typed-argument-parser/issues/80

For development, it is advised to install `nacsos-data` locally (not from git) via
```bash
pip install -e ../nacsos-data/
```
(assuming both projects live side-by-side)

## Running the database with docker
Start up the database by running docker (or use your local instance)
```bash
sudo systemctl start docker
docker-compose up
```

## Starting the server
```bash
python main.py

# optionally, you can specify the config file directly
NACSOS_CONFIG=config/local.toml python main.py

# additionally, you can always override all exposed config variables directly
# for more info, check
python main.py -h
```

The configuration is read in the following order (and overridden by consecutive steps):
- `@dataclasses` in `server/util/config.py`
- TOML config file (either `config/default.toml` or whatever is in `NACSOS_CONFIG`)
- Command line arguments

The default config is set up to work with a locally running docker instance with its respective default config.
It should never be changed, always make a local copy and never commit it to the repository!