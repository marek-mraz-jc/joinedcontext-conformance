# T-6808 steps: MCP tool calls on jc-prod as mcp-admin

Script: evidence/T-6808/steps.py (login: evidence/T-6807/mcp_login.py). No token in this file.

## Run 2026-11-12T08:08Z

initialize 200 ({'name': 'joinedcontext-portal', 'version': '0.1.0'})

| tool | arguments | HTTP | answer |
|---|---|---|---|
| jc_organization_read | `{"aspect": "projects", "project": "helsinki"}` | 200 | ok: {"apiVersion": "joinedcontext.com/v1alpha1", "kind": "List", "items": [{"name": "banskabystrica", "sample": true, "title": {"en": "City of Banská Bystrica", "sk": "Mesto Banská Bystrica"}, "organization": "hel"}, {"name": "bbsk", "sample": false, "title": {"en": "Banská Bystrica Self-Governing Region", "sk": "Banskobystrický samosprávny kraj"}, "organization": "hel"}, {"name": "helsinki", "sample": false, "title": {"en": "Helsinki", "sk": "Helsinki"}, "organization": "hel"}, {"name": "helsinki-mobility", "sample": false, "title": {"en": "Mobility analytics", "sk": "Analytika mobility"}, "organ ... |
| jc_change_plan | `{"project": "mcp-admin-check", "changes": [{"op": "create", "kind": "Project", "name": "mcp-admin-check", "manifest": {"apiVersion": "joinedcontext.com/v1alpha1", "kind": "Project", "metadata": {"name": "mcp-admin-check"}, "spec": {"organizationRef": "hel"}}}]}` | 200 | ok: {"planId": "cjd2wfkgsqmmykayouipwes7e5", "baseEtag": "5ad38304b535c2987dbd24657c1a11b884984ff600d9f389deb0d4e634fee792", "expiresAt": "2026-11-12T08:38:04.995523201+00:00", "lane": "red", "diff": [{"op": "create", "portal": "https://portal.smartcity.joinedcontext.com/projects/mcp-admin-check/projects", "kind": "Project", "name": "mcp-admin-check", "lane": "red", "plan": {"summary": {"create": 1, "update": 0, "delete": 0}, "fields": [{"path": "metadata.name", "to": "mcp-admin-check"}, {"path": "metadata.namespace", "to": "org"}, {"path": "spec.organizationRef", "to": "hel"}]}, "goesWith": null, ... |

Stopped before jc_change_apply: plan lane `red`, verdict ok `True`; next says: 'call jc_change_apply with this planId within 30 minutes: apply opens a change request and changes nothing live; a person reviews and approves it in the Portal, so when the person asked for this change, apply it without asking them again'. Not in tools/list (94 tools): jc_change_approve, jc_change_withdraw, jc_project_open.

| jc_organization_read | `{"aspect": "projects", "project": "helsinki"}` | 200 | ok: {"apiVersion": "joinedcontext.com/v1alpha1", "kind": "List", "items": [{"name": "banskabystrica", "sample": true, "title": {"en": "City of Banská Bystrica", "sk": "Mesto Banská Bystrica"}, "organization": "hel"}, {"name": "bbsk", "sample": false, "title": {"en": "Banská Bystrica Self-Governing Region", "sk": "Banskobystrický samosprávny kraj"}, "organization": "hel"}, {"name": "helsinki", "sample": false, "title": {"en": "Helsinki", "sk": "Helsinki"}, "organization": "hel"}, {"name": "helsinki-mobility", "sample": false, "title": {"en": "Mobility analytics", "sk": "Analytika mobility"}, "organ ... |

Projects after the walk: ['banskabystrica', 'bbsk', 'helsinki', 'helsinki-mobility', 'praha', 'slovensko']; `mcp-admin-check` present: False.


## Run 2026-11-12T08:08Z

initialize 200 ({'name': 'joinedcontext-portal', 'version': '0.1.0'})

| tool | arguments | HTTP | answer |
|---|---|---|---|
| jc_organization_read | `{"aspect": "projects", "project": "helsinki"}` | 200 | ok: {"apiVersion": "joinedcontext.com/v1alpha1", "kind": "List", "items": [{"name": "banskabystrica", "sample": true, "title": {"en": "City of Banská Bystrica", "sk": "Mesto Banská Bystrica"}, "organization": "hel"}, {"name": "bbsk", "sample": false, "title": {"en": "Banská Bystrica Self-Governing Region", "sk": "Banskobystrický samosprávny kraj"}, "organization": "hel"}, {"name": "helsinki", "sample": false, "title": {"en": "Helsinki", "sk": "Helsinki"}, "organization": "hel"}, {"name": "helsinki-mobility", "sample": false, "title": {"en": "Mobility analytics", "sk": "Analytika mobility"}, "organ ... |
| jc_change_plan | `{"project": "mcp-admin-check", "changes": [{"op": "create", "kind": "Project", "name": "mcp-admin-check", "manifest": {"apiVersion": "joinedcontext.com/v1alpha1", "kind": "Project", "metadata": {"name": "mcp-admin-check"}, "spec": {"organizationRef": "hel"}}}]}` | 200 | ok: {"planId": "xyzk5wrv72zl3jgethy6p6bbss", "baseEtag": "5ad38304b535c2987dbd24657c1a11b884984ff600d9f389deb0d4e634fee792", "expiresAt": "2026-11-12T08:38:14.275009187+00:00", "lane": "red", "diff": [{"op": "create", "portal": "https://portal.smartcity.joinedcontext.com/projects/mcp-admin-check/projects", "kind": "Project", "name": "mcp-admin-check", "lane": "red", "plan": {"summary": {"create": 1, "update": 0, "delete": 0}, "fields": [{"path": "metadata.name", "to": "mcp-admin-check"}, {"path": "metadata.namespace", "to": "org"}, {"path": "spec.organizationRef", "to": "hel"}]}, "goesWith": null, ... |

Stopped before jc_change_apply: plan lane `red`, verdict ok `True`; next says: 'call jc_change_apply with this planId within 30 minutes: apply opens a change request and changes nothing live; a person reviews and approves it in the Portal, so when the person asked for this change, apply it without asking them again'. Not in tools/list (94 tools): jc_change_approve, jc_change_withdraw, jc_project_open.

| jc_organization_read | `{"aspect": "projects", "project": "helsinki"}` | 200 | ok: {"apiVersion": "joinedcontext.com/v1alpha1", "kind": "List", "items": [{"name": "banskabystrica", "sample": true, "title": {"en": "City of Banská Bystrica", "sk": "Mesto Banská Bystrica"}, "organization": "hel"}, {"name": "bbsk", "sample": false, "title": {"en": "Banská Bystrica Self-Governing Region", "sk": "Banskobystrický samosprávny kraj"}, "organization": "hel"}, {"name": "helsinki", "sample": false, "title": {"en": "Helsinki", "sk": "Helsinki"}, "organization": "hel"}, {"name": "helsinki-mobility", "sample": false, "title": {"en": "Mobility analytics", "sk": "Analytika mobility"}, "organ ... |

Projects after the walk: ['banskabystrica', 'bbsk', 'helsinki', 'helsinki-mobility', 'praha', 'slovensko']; `mcp-admin-check` present: False.

