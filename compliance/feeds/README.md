# Feed validator

`validate_feed.py` checks an Atom (RFC 4287), RSS 2.0 or JSON Feed 1.1 feed, the
HTTP answer that serves it, and whether the three feeds of one dataset list the
same items (T-5201). It uses lxml, jsonschema and the Python standard library
only.

## 1. Run it

```sh
cd compliance/feeds
# a file: document rules only; --self is the URL the self link must carry
./validate_feed.py --lang sk --self https://host/x/feed.json feed.json

# a URL: document rules plus the HTTP rules (media type, charset, ETag or
# Last-Modified, 304, CORS, size cap, RFC 5005 paging links)
./validate_feed.py --lang sk \
  https://opendata.dev.joinedcontext.com/feeds/dataset.atom

# the three feeds of one dataset: each alone, then their item ids and order
./validate_feed.py --lang sk --cross ATOM_URL RSS_URL JSON_URL

# every feed the dataset pages advertise with <link rel="alternate">, as tsv
./validate_feed.py --sweep https://opendata.dev.joinedcontext.com --lang sk \
  --allow-host gateway.dev.joinedcontext.com --out feeds-sweep.tsv
```

The format is sniffed from the body; `--format atom|rss|json` overrides it. Each
finding prints as `ERROR rule (spec section): message` or `WARNING ...`, and the
exit code is 1 on any error. The rule list with its spec sections is the module
docstring (`python3 -c "import validate_feed; help(validate_feed)"`).

## 2. Calls from the feed tasks

- T-5202, the Antares feed surface: a CI step renders the surface's fixtures to
  files and runs `validate_feed.py --lang sk --self <the URL the test served>
  FILE` on each, then `--cross` on the three files of one view. A test may
  import the module and assert `validate_feed.validate(body, fmt, url, lang) ==
  []` and `validate_feed.cross_check({...}) == []`.
- T-5203, the gateway and the catalogue link: after the deploy, run
  `validate_feed.py --lang sk --cross` on the three dev URLs of `bbsk-podujatia`
  and of one non-event dataset (0 errors), and dispatch the `feeds-sweep`
  workflow with the gateway host in `allow_host`.

## 3. The sweep

The sweep reads `package_search` (up to 1000 datasets), opens each dataset page
and the catalogue's front page, and validates only the feeds a page advertises:
feeds are off by default (owner, 2026-10-22T09:25Z), so a dataset without one is
no finding. An advertised feed that answers 404 is an `http-status` error. The
tsv has one row per feed: dataset, format, url, errors, warnings, rules. When a
page advertises several formats, a `cross` row reports item lists that differ.

## 4. Security

- Parsing refuses any DTD, never resolves an entity and never reaches the
  network (lxml `resolve_entities=False`, `no_network=True`);
  `fixtures/bomb.atom` proves an entity bomb and an external entity are refused.
- The sweep fetches https only, on the catalogue's host and each `--allow-host`;
  a redirect elsewhere is refused. Every fetch has a 20 s timeout and reads at
  most `--max-bytes` (5 MiB).
- Paging links are followed one hop, on the feed's own host only.

## 5. Sources

- `schema/atom.rng`: RFC 4287 Appendix B, converted from the compact syntax by
  hand; the header lists the two loosenings.
- `schema/jsonfeed-1.1.schema.json`: written from
  <https://www.jsonfeed.org/version/1.1/>, which publishes no schema.
- RSS 2.0 and its best practices: <https://www.rssboard.org/rss-specification>,
  <https://www.rssboard.org/rss-profile>.
- RFC 3339, RFC 822, RFC 5005, RFC 9110.

## 6. Tests

`python3 -m pytest compliance/feeds -q`: each good fixture passes, one broken
twin per document rule fails with exactly that rule, the HTTP rules run against
a local `http.server`, and the sweep runs against canned catalogue answers.
