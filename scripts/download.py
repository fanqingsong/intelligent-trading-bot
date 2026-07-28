from datetime import datetime
from pathlib import Path

import click

from common.types import Venue
from inputs import get_download_functions
from service.App import *


"""
Download raw data for the specified venue and store updates in the corresponding files.
"""


def run_download(config_file: str = ""):
    if config_file:
        load_config(config_file)

    data_sources = App.config["data_sources"]
    download_max_rows = App.config.get("download_max_rows", 0)
    _ = download_max_rows  # reserved for venue downloaders

    now = datetime.now()
    venue = Venue(App.config.get("venue"))
    download_klines_fn = get_download_functions(venue)
    download_klines_fn(App.config, data_sources)

    elapsed = datetime.now() - now
    print(f"")
    print(f"Finished downloading {len(data_sources)} data sources from {venue} in {str(elapsed).split('.')[0]}")


@click.command()
@click.option('--config_file', '-c', type=click.Path(), default='', help='Configuration file name')
def main(config_file):
    run_download(config_file)


if __name__ == '__main__':
    main()
