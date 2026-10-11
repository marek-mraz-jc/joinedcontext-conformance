// SAP Cloud SDK (odata-v4 4.9.1) against a view's OData endpoint (T-3510): run.sh generated the
// client from the view's $metadata into gen/View; this reads one Entity of the first EntitySet and,
// under a restricted view, finds none of the names the view hides in the client or the Entity.
'use strict';
const { view } = require('./gen/View');

async function main(root, hidden) {
  const service = view();
  // the EntitySet APIs are the service's getters (`initApi` is a method)
  const proto = Object.getPrototypeOf(service);
  const name = Object.getOwnPropertyNames(proto).find((n) => Object.getOwnPropertyDescriptor(proto, n).get);
  if (!name) throw new Error('the generated client has no EntitySet');
  const api = service[name];
  const rows = await api.requestBuilder().getAll().top(1).execute({ url: root });
  if (rows.length !== 1 || !rows[0].id) throw new Error(`${api.entityConstructor._entityName}?$top=1 read ${rows.length} Entities`);
  const fields = Object.values(api.schema).map((f) => f._fieldName);
  const shown = hidden.filter((n) => fields.includes(n) || n in rows[0]);
  if (shown.length) throw new Error(`the client shows hidden members: ${shown.join(', ')}`);
  console.log(`SAP Cloud SDK read ${api.entityConstructor._entityName}('${rows[0].id}')`);
}

main(process.argv[2], (process.argv[3] ?? '').split(',').filter(Boolean)).catch((e) => {
  console.error(e.rootCause?.response?.data ?? e.message);
  process.exit(1);
});
