"""Municipal offer from dated GTFS trips, without inferring service from lines.

Counts use the GTFS *service day*. A departure at 25:10 belongs to that service
day, not to the next calendar day's count. Destinations require a later stop
where alighting is allowed. They can include municipalities outside the eligible
population range. No realtime reliability or transfer reachability is inferred.
"""
from __future__ import annotations

import csv
from contextlib import closing
from datetime import date, datetime
import io
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import zipfile


class TransportOfferError(ValueError):
    """The supplied feed cannot support an unambiguous scheduled-trip count."""


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _code(value):
    return isinstance(value, str) and len(value) == 5 and value.isdigit()


def _stamp(value, context):
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except (TypeError, ValueError) as exc:
        raise TransportOfferError(f"{context}: invalid GTFS date {value!r}") from exc


def _seconds(value, context):
    if not value:
        return None
    try:
        parts = value.split(":")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError
        hours, minutes, seconds = map(int, parts)
        if not 0 <= minutes < 60 or not 0 <= seconds < 60:
            raise ValueError
        return hours * 3600 + minutes * 60 + seconds
    except (AttributeError, ValueError) as exc:
        raise TransportOfferError(f"{context}: invalid GTFS time {value!r}") from exc


def _permission(value, context):
    if value in ("", "0"):
        return True
    if value in ("1", "2", "3"):
        return False
    raise TransportOfferError(f"{context}: invalid pickup/drop-off value {value!r}")


class _FeedReader:
    def __init__(self, archive, key):
        self.archive = archive
        self.key = key
        self.files = {}
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            base = PurePosixPath(name).name.lower()
            if base in self.files:
                raise TransportOfferError(f"{key}: ambiguous duplicate file {base}")
            self.files[base] = name

    def rows(self, filename, required_fields=(), optional=False):
        member = self.files.get(filename)
        if member is None:
            if optional:
                return
            raise TransportOfferError(f"{self.key}: missing {filename}")
        with self.archive.open(member) as source:
            with io.TextIOWrapper(source, encoding="utf-8-sig", newline="") as text:
                reader = csv.DictReader(text)
                if not reader.fieldnames:
                    if optional:
                        return
                    raise TransportOfferError(f"{self.key}: {filename} has no header")
                # Renfe publishes padded CSV headers and identifiers. Keep IDs
                # as text (including leading zeroes), trimming only whitespace.
                reader.fieldnames = [name.strip() for name in reader.fieldnames]
                if len(set(reader.fieldnames)) != len(reader.fieldnames):
                    raise TransportOfferError(f"{self.key}: duplicate normalized CSV columns")
                missing = set(required_fields) - set(reader.fieldnames)
                if missing:
                    raise TransportOfferError(f"{self.key}: {filename} lacks {sorted(missing)}")
                for number, row in enumerate(reader, start=2):
                    if None in row or any(value is None for value in row.values()):
                        raise TransportOfferError(f"{self.key}: malformed {filename} row {number}")
                    yield {name: value.strip() for name, value in row.items()}


def _unique_rows(reader, filename, identifier, required_fields=()):
    rows = {}
    for row in reader.rows(filename, (identifier, *required_fields)):
        identity = row[identifier]
        if not identity or identity in rows:
            raise TransportOfferError(f"{reader.key}: missing or duplicate {identifier} in {filename}")
        rows[identity] = row
    return rows


def _calendar(reader, dates):
    calendar = {}
    bounds = []
    for row in reader.rows("calendar.txt", ("service_id", *_WEEKDAYS, "start_date", "end_date"), optional=True):
        service = row["service_id"]
        start = _stamp(row["start_date"], reader.key)
        end = _stamp(row["end_date"], reader.key)
        if not service or service in calendar or start > end:
            raise TransportOfferError(f"{reader.key}: invalid or duplicate calendar service {service!r}")
        if any(row[weekday] not in ("0", "1") for weekday in _WEEKDAYS):
            raise TransportOfferError(f"{reader.key}: invalid weekly calendar flags")
        calendar[service] = (start, end, tuple(row[weekday] == "1" for weekday in _WEEKDAYS))
        bounds.extend((start, end))
    exceptions = {}
    for row in reader.rows("calendar_dates.txt", ("service_id", "date", "exception_type"), optional=True):
        service = row["service_id"]
        day = _stamp(row["date"], reader.key)
        if not service or row["exception_type"] not in ("1", "2") or (service, day) in exceptions:
            raise TransportOfferError(f"{reader.key}: invalid or duplicate calendar exception")
        exceptions[service, day] = row["exception_type"]
        bounds.append(day)
    known_services = set(calendar) | {service for service, _ in exceptions}
    if not known_services:
        raise TransportOfferError(f"{reader.key}: no calendar or calendar_dates services")
    active = {}
    for day in dates:
        services = {service for service, (start, end, weekdays) in calendar.items()
                    if start <= day <= end and weekdays[day.weekday()]}
        for (service, exception_day), kind in exceptions.items():
            if exception_day == day:
                if kind == "1":
                    services.add(service)
                else:
                    services.discard(service)
        active[day] = services
    return known_services, active, min(bounds), max(bounds)


def _mean(rows):
    count = len(rows)
    return {"days": count,
            "frequency": sum(row["frequency"] for row in rows) / count if count else None,
            "connectivity": sum(row["connectivity"] for row in rows) / count if count else None}


def build_transport_offer(feeds: list[dict], stop_municipalities: dict,
                          dates: list[date], eligible_codes: list[str]) -> dict:
    """Return JSON-serializable daily counts and means over the requested dates.

    ``daily[code][ISO]`` contains ``frequency`` (unique outward trips),
    ``connectivity`` (unique direct municipal destinations), ``destinations``,
    ``has_scheduled_data``, ``has_active_service``, ``cobertura_presentes``
    (feed keys with a published boarding/alighting stop in this municipality),
    ``feeds_in_range`` and first/last eligible departure seconds. Departure
    bounds use the earliest qualifying municipal boarding of each counted trip,
    matching the one-trip-per-municipality deduplication rule. Hours above
    24:00 remain above 86400 seconds and belong to the same GTFS service date.

    ``municipalities[code]`` contains ``frequency`` and ``connectivity`` means
    over *all requested dates*, including calculated zero-service days, plus
    ``weekdays``, ``saturday`` and ``sunday`` means. With seven dates the overall
    means are seven-day means. An absent day category has ``days=0`` and ``None``
    means. ``has_scheduled_data`` means that at least one feed publishes any
    boarding/alighting trip stop for the municipality, including outside the
    requested period. It does not assert complete network or mode coverage.

    Integrators must use coverage/calendar metadata before turning a zero into
    an IVP value. Unknown municipalities remain unknown, rather than being
    guessed from stop names or headsigns. Feed identifiers namespace all trips.
    ``stop_times`` is streamed into a temporary disk SQLite index, allowing
    arbitrary row/sequence order without a full stop-times table in RAM.
    A feed's explicit ``allow_incomplete_trips=True`` enables diagnostic output
    when trips lack stop_times. Every orphan is retained in feed metadata with
    its route and active dates. It never creates departures or certifies that
    the affected municipalities have complete coverage. The default rejects it.
    """
    if not dates or any(type(day) is not date for day in dates) or len(set(dates)) != len(dates):
        raise TransportOfferError("dates must contain distinct datetime.date service dates")
    if any(not _code(code) for code in eligible_codes) or len(set(eligible_codes)) != len(eligible_codes):
        raise TransportOfferError("eligible_codes must contain distinct five-digit municipality codes")
    days = sorted(dates)
    codes = sorted(eligible_codes)
    eligible = set(codes)
    keys = [feed.get("key") for feed in feeds]
    if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        raise TransportOfferError("feeds must have distinct nonempty keys")
    normalized_mappings = {}
    for key in keys:
        mapping = stop_municipalities.get(key)
        if not isinstance(mapping, dict) or any(code is not None and not _code(code) for code in mapping.values()):
            raise TransportOfferError(f"{key}: stop_municipalities requires five-digit codes or None")
        normalized = {}
        for stop, code in mapping.items():
            if not isinstance(stop, str) or not stop.strip():
                raise TransportOfferError(f"{key}: stop IDs must be nonempty text")
            stop = stop.strip()
            if stop in normalized and normalized[stop] != code:
                raise TransportOfferError(f"{key}: conflicting normalized municipality mapping for {stop!r}")
            normalized[stop] = code
        normalized_mappings[key] = normalized
    daily = {code: {day: {"frequency": 0, "destinations": set(), "active_feeds": set(),
                         "first_departure_seconds": None, "last_departure_seconds": None}
                    for day in days} for code in codes}
    coverage = {code: set() for code in codes}
    feed_stats = {}
    for feed in feeds:
        key = feed["key"]
        mapping = normalized_mappings[key]
        allow_incomplete = feed.get("allow_incomplete_trips", False)
        if not isinstance(allow_incomplete, bool):
            raise TransportOfferError(f"{key}: allow_incomplete_trips must be an explicit boolean")
        excluded = feed.get("exclude_route_ids", [])
        if not isinstance(excluded, list) or any(not isinstance(r, str) for r in excluded):
            raise TransportOfferError(f"{key}: exclude_route_ids must be an explicit list of route IDs")
        excluded = set(excluded)
        try:
            with zipfile.ZipFile(Path(feed["path"])) as archive:
                reader = _FeedReader(archive, key)
                for _ in reader.rows("frequencies.txt", optional=True):
                    raise TransportOfferError(f"{key}: nonempty frequencies.txt is unsupported; template trips cannot be counted as departures")
                routes = _unique_rows(reader, "routes.txt", "route_id")
                if not excluded.issubset(routes):
                    raise TransportOfferError(f"{key}: excluded route not present in feed")
                stops = _unique_rows(reader, "stops.txt", "stop_id")
                trips = _unique_rows(reader, "trips.txt", "trip_id", ("route_id", "service_id"))
                services, active, start, end = _calendar(reader, days)
                active_trip_days = {}
                for identity, trip in trips.items():
                    if trip["route_id"] not in routes or trip["service_id"] not in services:
                        raise TransportOfferError(f"{key}: trip {identity!r} has an unknown route or service")
                    active_trip_days[identity] = tuple(day for day in days if trip["service_id"] in active[day] and trip["route_id"] not in excluded)
                row_count = 0
                seen_trips = set()
                with tempfile.TemporaryDirectory(prefix="habitamadrid-gtfs-") as temp:
                    with closing(sqlite3.connect(str(Path(temp) / "sequences.sqlite"))) as db:
                        db.execute("PRAGMA journal_mode=OFF")
                        db.execute("PRAGMA synchronous=OFF")
                        db.execute("CREATE TABLE visits (trip TEXT, sequence INTEGER, code TEXT, pickup INTEGER, dropoff INTEGER, seconds INTEGER, PRIMARY KEY(trip, sequence)) WITHOUT ROWID")
                        batch = []
                        for row in reader.rows("stop_times.txt", ("trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time")):
                            identity = row["trip_id"]
                            if identity not in trips or row["stop_id"] not in stops:
                                raise TransportOfferError(f"{key}: stop_times references an unknown trip or stop")
                            try:
                                sequence = int(row["stop_sequence"])
                                if sequence < 0:
                                    raise ValueError
                            except ValueError as exc:
                                raise TransportOfferError(f"{key}: invalid stop_sequence") from exc
                            pickup = _permission(row.get("pickup_type", ""), key)
                            dropoff = _permission(row.get("drop_off_type", ""), key)
                            arrival = _seconds(row["arrival_time"], key)
                            departure = _seconds(row["departure_time"], key)
                            code = mapping.get(row["stop_id"])
                            if code in eligible and (pickup or dropoff) and trips[identity]["route_id"] not in excluded:
                                coverage[code].add(key)
                                for day in active_trip_days[identity]:
                                    daily[code][day]["active_feeds"].add(key)
                            # Validate all rows, but index only journeys active
                            # in the requested period. Large national feeds can
                            # contain months of inactive service templates.
                            if active_trip_days[identity]:
                                batch.append((identity, sequence, code, pickup, dropoff,
                                              departure if departure is not None else arrival))
                            row_count += 1
                            seen_trips.add(identity)
                            if len(batch) >= 5000:
                                db.executemany("INSERT INTO visits VALUES(?,?,?,?,?,?)", batch)
                                batch.clear()
                        if batch:
                            db.executemany("INSERT INTO visits VALUES(?,?,?,?,?,?)", batch)
                        orphan_records = [
                            {"trip_id": identity, "route_id": trips[identity]["route_id"],
                             "active_dates": [day.isoformat() for day in active_trip_days[identity]]}
                            for identity in sorted(set(trips) - seen_trips)
                        ]
                        if orphan_records and not allow_incomplete:
                            raise TransportOfferError(f"{key}: {len(orphan_records)} published trip(s) have no stop_times; explicit allow_incomplete_trips=True is diagnostic only")
                        db.commit()

                        def consume(identity, visits):
                            trip_days = active_trip_days[identity]
                            if not trip_days:
                                return
                            downstream = set()
                            outward = {}
                            first_times = {}
                            # Database order is descending sequence. Only stops already
                            # visited in this scan can be later destinations of this origin.
                            for code, pickup, dropoff, seconds in visits:
                                if code in eligible and pickup:
                                    destinations = downstream - {code}
                                    if destinations:
                                        outward.setdefault(code, set()).update(destinations)
                                        if seconds is not None:
                                            first_times[code] = min(first_times.get(code, seconds), seconds)
                                if code is not None and dropoff:
                                    downstream.add(code)
                            for code, destinations in outward.items():
                                for day in trip_days:
                                    result = daily[code][day]
                                    result["frequency"] += 1
                                    result["destinations"].update(destinations)
                                    seconds = first_times.get(code)
                                    if seconds is not None:
                                        previous = result["first_departure_seconds"]
                                        result["first_departure_seconds"] = seconds if previous is None else min(previous, seconds)
                                        previous = result["last_departure_seconds"]
                                        result["last_departure_seconds"] = seconds if previous is None else max(previous, seconds)

                        current = None
                        visits = []
                        for identity, code, pickup, dropoff, seconds in db.execute("SELECT trip,code,pickup,dropoff,seconds FROM visits ORDER BY trip, sequence DESC"):
                            if current is not None and identity != current:
                                consume(current, visits)
                                visits = []
                            current = identity
                            visits.append((code, pickup, dropoff, seconds))
                        if current is not None:
                            consume(current, visits)
                feed_stats[key] = {"route_count": len(routes), "trip_count": len(trips),
                                   "excluded_route_ids": sorted(excluded),
                                   "stop_count": len(stops), "stop_time_count": row_count,
                                   "mapped_stop_count": sum(mapping.get(stop) is not None for stop in stops),
                                   "calendar_start": start.isoformat(), "calendar_end": end.isoformat(),
                                   "active_trip_count": {day.isoformat(): sum(day in active_trip_days[trip] for trip in trips) for day in days},
                                   "active_trips_with_stop_times": {day.isoformat(): sum(day in active_trip_days[trip] for trip in seen_trips) for day in days},
                                   "orphan_trip_count": len(orphan_records),
                                   "active_orphan_trip_count": sum(bool(record["active_dates"]) for record in orphan_records),
                                   "orphans": orphan_records,
                                   "diagnostic_incomplete_trips": bool(orphan_records)}
        except sqlite3.IntegrityError as exc:
            raise TransportOfferError(f"{key}: duplicate (trip_id, stop_sequence) in stop_times") from exc
        except (OSError, zipfile.BadZipFile, UnicodeError, KeyError) as exc:
            raise TransportOfferError(f"{key}: unreadable or malformed GTFS feed: {exc}") from exc
    output_daily = {}
    summaries = {}
    for code in codes:
        present = sorted(coverage[code])
        output_daily[code] = {}
        for day in days:
            result = daily[code][day]
            destination_codes = sorted(result.pop("destinations"))
            active_feeds = sorted(result.pop("active_feeds"))
            output_daily[code][day.isoformat()] = {
                **result, "connectivity": len(destination_codes), "destinations": destination_codes,
                "has_scheduled_data": bool(present), "has_active_service": bool(active_feeds),
                "cobertura_presentes": present, "active_feeds": active_feeds,
                "feeds_in_range": [key for key in present if feed_stats[key]["calendar_start"] <= day.isoformat() <= feed_stats[key]["calendar_end"]]}
        summaries[code] = {
            **_mean(list(output_daily[code].values())),
            "weekdays": _mean([output_daily[code][day.isoformat()] for day in days if day.weekday() < 5]),
            "saturday": _mean([output_daily[code][day.isoformat()] for day in days if day.weekday() == 5]),
            "sunday": _mean([output_daily[code][day.isoformat()] for day in days if day.weekday() == 6]),
            "has_scheduled_data": bool(present), "cobertura_presentes": present,
            "covered_reference_days": sum(bool(row["feeds_in_range"]) for row in output_daily[code].values())}
    return {"schema_version": 1, "date_basis": "GTFS service day; hours >=24 remain in their service date",
            "dates": [day.isoformat() for day in days], "feeds": feed_stats,
            "daily": output_daily, "municipalities": summaries}
