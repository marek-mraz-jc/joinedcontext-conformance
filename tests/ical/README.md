# iCalendar (RFC 5545) validator

`ical_check.py` checks a feed's structure (CRLF, folding at 75 octets, one VCALENDAR, VERSION and
PRODID, UID present and unique, DTSTAMP in UTC, DTSTART, DTEND after DTSTART, TEXT escaping, TZID
defined by a VTIMEZONE, GEO, URL) and, given the view's NGSI-LD root, the Event mapping of Antares
`docs/analysis/ical-mapping.md`: every Event with a `name` and a `startDate` is one VEVENT whose UID
is its id, SUMMARY its name, LOCATION from its address and URL its url. Each finding names its rule.

`fixtures/good.ics` passes; each `fixtures/broken-<rule>.ics` fails with exactly that rule
(`test_ical.py`, run in ci.yml). Live:

```sh
python3 tests/ical/ical_check.py --events https://api.dev.joinedcontext.com/api/endpoint/{slug}/ngsi-ld/v1 \
  https://api.dev.joinedcontext.com/api/endpoint/{slug}/ical/v1/events.ics
ICAL_URL=.../ical/v1/events.ics ICAL_NGSILD_URL=.../ngsi-ld/v1 tests/ical/run.sh -k live
```

`tests/prod-nightly/run.sh` and `tests/standards/run.sh` call it.
