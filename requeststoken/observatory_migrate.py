"""Optional, offline import of the previous observatory SQLite cache into Redis.

Run with all observatory workers stopped. The source is opened read-only; the
destination must be empty. No lease or retry timer is copied from SQLite.
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from requeststoken.observatory import DownloadObservatory, count, iso_day
from requeststoken.observatory_store import DEFAULT_PREFIX, RedisSnapshotStore


def load_legacy(path):
    uri = "file:" + quote(str(Path(path).resolve())) + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as database:
        database.row_factory = sqlite3.Row
        row = database.execute(
            "SELECT payload, checked_at, status FROM snapshot WHERE id='requests'",
        ).fetchone()
        if row is None:
            raise ValueError("The source has no Requests snapshot")
        envelope = DownloadObservatory._decode(json.dumps({
            "snapshot": json.loads(row["payload"]), "checkedAt": row["checked_at"],
            "status": row["status"],
        }))
        fields = {"snapshot": json.dumps(envelope)}
        for record in database.execute("SELECT * FROM source_checks"):
            day = iso_day(record["day"])
            stamp = datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("A source check has no timezone")
            check = {
                "day": day, "fetchedAt": record["fetched_at"],
                "latestDate": iso_day(record["latest_date"]),
                "rawTotal": count(record["raw_total"]), "rawLatest": count(record["raw_latest"]),
                "reportedDays": count(record["reported_days"]),
            }
            if check["rawTotal"] < check["rawLatest"] or not 1 <= check["reportedDays"] <= 10_000:
                raise ValueError("Invalid source-check counts")
            fields["check:" + day] = json.dumps(check)
    return fields


def import_legacy(path, store):
    fields = load_legacy(path)
    # WATCH protects against a worker starting while this offline import runs.
    # One HSET transfers the snapshot and all checks without a partial import.
    with store.client.pipeline() as transaction:
        transaction.watch(*store.keys)
        if transaction.exists(*store.keys):
            raise ValueError("Destination namespace is not empty; nothing was imported")
        transaction.multi()
        transaction.hset(store.state_key, mapping=fields)
        transaction.execute()
    return len(fields) - 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Path to the previous observatory.sqlite file")
    parser.add_argument("--dry-run", action="store_true", help="Validate the source without contacting Redis")
    args = parser.parse_args()
    try:
        if args.dry_run:
            total = len(load_legacy(args.database)) - 1
            print(f"Valid snapshot and {total} daily source checks. No data changed.")
        else:
            if not os.environ.get("REDIS_URL"):
                parser.error("Set REDIS_URL before importing")
            store = RedisSnapshotStore.from_url(
                os.environ["REDIS_URL"], os.environ.get("REQUESTS_OBSERVATORY_REDIS_PREFIX", DEFAULT_PREFIX),
            )
            total = import_legacy(args.database, store)
            print(f"Imported the snapshot and {total} daily source checks. SQLite was not modified.")
    except Exception as exc:
        # Do not expose Redis URLs/passwords in command output.
        parser.exit(1, f"Import failed ({type(exc).__name__}). Check the source, Redis connection, and empty destination.\n")


if __name__ == "__main__":
    main()
