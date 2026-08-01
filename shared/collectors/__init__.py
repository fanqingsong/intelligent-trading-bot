from shared.types import Venue


def get_download_functions(venue: Venue):
    if venue == Venue.ASHARE:
        from shared.collectors.collector_ashare import download_klines
        return download_klines
    raise ValueError(f"Unknown venue {venue} or downloader for the venue not implemented")
