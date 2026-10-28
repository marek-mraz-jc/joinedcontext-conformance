// The request list of api-mix-100rps.js (T-4714): one request per line,
//   kind <TAB> method <TAB> url [<TAB> probed status [<TAB> JSON body of a POST]]
// A POST without a body sends MCP's tools/list; with one (an MCP tools/call, T-4754) it sends that.
// Empty lines and lines starting with `#` are skipped, so is a row whose probed status is not 2xx
// (a 404 schema the list kept as evidence). An unknown kind or method is an error, never a
// silent drop: a typo would otherwise shrink the mix without anyone noticing.
// A kind is a standard, optionally with the query it asks after a dot (T-5901), e.g. `ogc.cql2`:
// the run reports per kind and per standard, so a slow query shape shows apart from its surface.

export const KINDS = ['ngsi', 'geo', 'temporal', 'odata', 'ogc', 'sta', 'geojson', 'mcp', 'schema', 'csv', 'ical', 'ckan', 'ws'];
const QUERY = /^[a-z0-9-]{1,32}$/;

// The standard a kind belongs to: `ogc.cql2` -> `ogc`.
export function standardOf(kind) {
  return kind.split('.', 1)[0];
}
const METHODS = ['GET', 'POST'];

export function parseRequests(text) {
  const requests = [];
  const skipped = [];
  text.split('\n').forEach((raw, index) => {
    const line = raw.replace(/\r$/, '');
    if (line.trim() === '' || line.startsWith('#')) {
      return;
    }
    const [kind, method, url, status, body] = line.split('\t');
    const at = `line ${index + 1}`;
    const [standard, query, extra] = (kind || '').split('.');
    if (!KINDS.includes(standard) || extra !== undefined || (query !== undefined && !QUERY.test(query))) {
      throw new Error(`${at}: unknown kind '${kind}' (one of ${KINDS.join(', ')}, optionally .<query>)`);
    }
    if (!METHODS.includes(method)) {
      throw new Error(`${at}: unknown method '${method}'`);
    }
    if (!/^(https?|wss?):\/\//.test(url || '')) {
      throw new Error(`${at}: no http(s) or ws(s) url`);
    }
    if (status !== undefined && status !== '' && !/^2\d\d$/.test(status)) {
      skipped.push({ kind, url, status });
      return;
    }
    if (body !== undefined && body !== '') {
      if (method !== 'POST') {
        throw new Error(`${at}: a body on a ${method}`);
      }
      try {
        JSON.parse(body);
      } catch (_) {
        throw new Error(`${at}: the body is not JSON`);
      }
      requests.push({ kind, method, url, body });
      return;
    }
    requests.push({ kind, method, url });
  });
  return { requests, skipped };
}
