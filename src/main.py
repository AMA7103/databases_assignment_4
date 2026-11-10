import argparse
import logging
import sys

from src.sync import init, full_load, incremental
from src.validation import validate


def main():
    """Run the Sakila analytics command-line interface."""
    parser = argparse.ArgumentParser(
        description="Synchronize MySQL Sakila data with SQLite analytics."
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "init",
        help="Initialize the SQLite analytics database.",
    )

    subparsers.add_parser(
        "full-load",
        help="Load all Sakila data into SQLite.",
    )

    subparsers.add_parser(
        "incremental",
        help="Synchronize new and updated Sakila records.",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate SQLite data against MySQL Sakila.",
    )

    validate_parser.add_argument(
        "--from",
        dest="start_date",
        help="Start date in YYYY-MM-DD format.",
    )

    validate_parser.add_argument(
        "--to",
        dest="end_date",
        help="End date in YYYY-MM-DD format.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        if args.command == "init":
            init()
            logging.info("Analytics database initialized successfully.")

        elif args.command == "full-load":
            counts = full_load()
            logging.info("Full load completed successfully.")

            for table, count in counts.items():
                logging.info("%s: %d source rows processed", table, count)

        elif args.command == "incremental":
            counts = incremental()
            logging.info("Incremental synchronization completed successfully.")

            for table, count in counts.items():
                logging.info("%s: %d source rows processed", table, count)

        elif args.command == "validate":
            result = validate(
                start_date=args.start_date,
                end_date=args.end_date,
            )

            logging.info("Validation passed.")
            logging.info(
                "Date range: %s to %s",
                result["start_date"],
                result["end_date"],
            )

            for table, count in result["counts"].items():
                logging.info("%s: %d rows verified", table, count)

        return 0

    except (Exception) as exc:
        logging.error("%s failed: %s", args.command, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
