"""Command-line interface of the `sparecores-data` package.

Lives outside of the `sc_data` package on purpose: importing that starts
downloading and importing the database, which should not happen before the
command-line arguments are parsed.
"""

import argparse
import logging
import os
import shutil
import sys

logger = logging.getLogger("sc-data")


def main():
    parser = argparse.ArgumentParser(
        prog="sc-data",
        description="Helpers for the Spare Cores Navigator data.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    download = subcommands.add_parser(
        "download",
        help="write the SQLite database file to the given path",
        description=(
            "Download the most recent Spare Cores Navigator data dump, import it "
            "into a SQLite file in the local cache, then copy that file to PATH, "
            "overwriting an already existing file."
        ),
    )
    download.add_argument(
        "path", metavar="PATH", help="path of the SQLite file to write"
    )
    download.add_argument(
        "--db-url", help="URL of the compressed SQL dump to download (SC_DATA_DB_URL)"
    )
    download.add_argument(
        "--timeout", type=float, help="HTTP timeout in seconds (SC_DATA_HTTP_TIMEOUT)"
    )
    download.add_argument(
        "--no-update",
        action="store_true",
        help="use the cached database without checking for a newer version",
    )
    download.add_argument(
        "-v", "--verbose", action="store_true", help="log debug messages to stderr"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        if args.verbose
        else "%(message)s",
        stream=sys.stderr,
    )

    # sc_data picks these up on import
    if args.db_url:
        os.environ["SC_DATA_DB_URL"] = args.db_url
    if args.timeout is not None:
        os.environ["SC_DATA_HTTP_TIMEOUT"] = str(args.timeout)
    if args.no_update:
        os.environ["SC_DATA_NO_UPDATE"] = "1"

    target = os.path.abspath(os.path.expanduser(args.path))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
    except OSError as e:
        sys.exit(f"sc-data: failed to create the parent directory of {target}: {e}")

    logger.info("Preparing the most recent SQLite database in the cache ...")
    try:
        from sc_data import db

        source = db.path
    except Exception as e:
        sys.exit(f"sc-data: failed to prepare the database: {e}")

    logger.info("Copying %s to %s", source, target)
    # copy to a temporary file first, so that PATH is never a partial database
    partial = target + ".part"
    try:
        shutil.copyfile(source, partial)
        os.replace(partial, target)
    except OSError as e:
        if os.path.exists(partial):
            os.unlink(partial)
        sys.exit(f"sc-data: failed to write {target}: {e}")

    logger.info("Done.")
