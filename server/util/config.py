from tap import Tap
from tap.utils import get_class_variables
from typing import get_type_hints, Any
from dataclasses import dataclass
from collections import OrderedDict
import os
import toml

NestedConfigDict = dict[str, dict[str, Any]]


@dataclass
class ServerConfig:
    host: str = 'localhost'  # host to run this server on
    port: int = 8080  # port for this serve to listen at


@dataclass
class DatabaseConfig:
    pw: str  # password for the database user
    user: str = 'nacsos'  # username for the database
    database: str = 'nacsos_core'  # name of the database
    host: str = 'localhost'  # host of the db server
    port: int = 5432  # port of the db server


class Config:
    _sub_configs = {
        'server': ServerConfig,
        'db': DatabaseConfig,
    }

    DEFAULT_CONFIG_FILE = 'config/default.toml'

    def __init__(self):
        stored_config = self._read_config_file()
        shlex_config = dict2shlex(stored_config)
        config = self._read_cli_args(shlex_config)

        self.server: ServerConfig = ServerConfig(**config['server'])
        self.db: DatabaseConfig = DatabaseConfig(**config['db'])

    def _read_config_file(self) -> NestedConfigDict:
        conf_file = os.environ.get('NACSOS_CONF', self.DEFAULT_CONFIG_FILE)
        with open(conf_file, 'r') as f:
            return toml.load(f)

    def _read_cli_args(self, shlex_config: list[str]):
        """
        This method generates a Tap (typed-argument-parser) instance from the config classes
        and exposes the variables including help (comments) and types to the command line.
        It then parses all CLI arguments and returns a nested dictionary.
        :return:
        """

        # create a typed argument parser by gathering all sub-configs (as argument prefixes)
        # to expose all config attributes to the command line
        class ProgrammaticArgumentParser(Tap):
            def configure(self):
                class_variables = []
                annotations = []
                for cls_name, cls in Config._sub_configs.items():
                    # append all class attributes, including annotations (e.g. comments)
                    class_variables += [(f'{cls_name}_{var}', data)
                                        for var, data in get_class_variables(cls).items()]

                    # append all annotations (e.g. type hints)
                    annotations += [(f'{cls_name}_{var}', data)
                                    for var, data in get_type_hints(cls).items()]

                    # transfer default parameters to this instance
                    for var in get_type_hints(cls).keys():
                        try:
                            setattr(self, f'{cls_name}_{var}', getattr(cls, var))
                        except AttributeError:
                            # this attribute has no default value
                            pass

                # inject the gathered class variables and annotations to the tap instance
                self.class_variables = OrderedDict(class_variables)
                self._annotations = dict(annotations)
                self.args_from_configs = shlex_config

        # parse command line arguments
        args = ProgrammaticArgumentParser(underscores_to_dashes=True).parse_args()

        config = {}
        for arg, value in args.as_dict().items():
            parts = arg.split('_')
            if parts[0] not in config:
                config[parts[0]] = {}
            config[parts[0]]['_'.join(parts[1:])] = value

        return config


def dict2shlex(config: NestedConfigDict) -> list[str]:
    ret = []
    for cls_name, attrs in config.items():
        for attr, value in attrs.items():
            ret.append(f'--{cls_name}-{attr.replace("_", "-")}')
            ret.append(f'"{value}"')
    return ret

__all__ = []