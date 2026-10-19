// The request list of api-mix-100rps.js (T-4714): one request per line,
//   kind <TAB> method <TAB> url [<TAB> probed status]
// Empty lines and lines starting with `#` are skipped, so is a row whose probed status is not 2xx
// (a 404 schema the list kept as evidence). An unknown kind or method is an error, never a
// silent drop: a typo would otherwise shrink the mix without anyone noticing.

export const KINDS = ['ngsi', 'odata', 'ogc', 'mcp', 'schema', 'csv', 'ckan', 'ws'];
const METHODS = ['GET', 'POST'];

export function parseRequests(text) {
  const requests = [];
  const skipped = [];
  text.split('\n').forEach((raw, index) => {
    const line = raw.replace(/\r$/, '');
    if (line.trim() === '' || line.startsWith('#')) {
      return;
    }
    const [kind, method, url, status] = line.split('\t');
    const at = `line ${index + 1}`;
    if (!KINDS.includes(kind)) {
      throw new Error(`${at}: unknown kind '${kind}' (one of ${KINDS.join(', ')})`);
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
    requests.push({ kind, method, url });
  });
  return { requests, skipped };
}
