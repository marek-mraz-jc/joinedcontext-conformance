import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { request as playwrightRequest } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { test, expect, requireEnv, signIn } from '../fixtures/portal.js';

/**
 * Journey 46 (T-4871, EP-92, ADR-N-043): a steward opens the project's MCP servers from the menu,
 * follows a public server to its connect page and reads the tools it lists; an external MCP client
 * (a stranger: no cookie, no token, the address the page shows) initializes, lists the same tools
 * and queries a space through the server, and every answer passes the outputSchema the server
 * published, as strict clients (the MCP inspector, the TypeScript SDK) check it. A viewer meets
 * "New MCP server" disabled with its reason, no edit or remove, and a 403 in words when proposing
 * one anyway; the stranger's write tool call is refused. Nothing is written, so nothing is cleaned up.
 *
 * Not checked here: the call in the project's Activity. The gateway emits no activity record yet
 * (docs Architecture/09-portal.md section on activity kinds); tasks/later/T-later-gateway-mcp-activity.md.
 *
 * JC_MCP_PROJECT (default helsinki) and JC_MCP_SERVER (default: the project's first public server)
 * choose the server. With JC_EVIDENCE_DIR set it keeps 1440 px and 400 px screenshots of each step.
 */
const PROJECT = process.env.JC_MCP_PROJECT ?? 'helsinki';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const ACCEPT = 'application/json, text/event-stream';

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  for (const width of [1440, 400]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: join(EVIDENCE, `${name}-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function openMcpServers(page: Page): Promise<void> {
  await page.goto(`/projects/${PROJECT}/spaces?lang=en`);
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  const link = nav.getByRole('link', { name: 'MCP servers' });
  await expect(link, 'the sidebar must offer MCP servers').toBeVisible({ timeout: 30_000 });
  await link.click();
  await expect(page.getByRole('heading', { name: 'MCP servers', level: 1 })).toBeVisible({ timeout: 30_000 });
}

interface Tool {
  name: string;
  outputSchema?: { required?: string[]; anyOf?: { required?: string[] }[] };
}

/** One JSON-RPC call as an MCP client over Streamable HTTP; the answer's `result`. */
async function rpc(client: APIRequestContext, url: string, method: string, params: object, session?: string) {
  const answer = await client.post(url, {
    headers: { 'Content-Type': 'application/json', Accept: ACCEPT, ...(session ? { 'Mcp-Session-Id': session } : {}) },
    data: { jsonrpc: '2.0', id: 1, method, params },
    maxRedirects: 0,
  });
  expect(answer.status(), `${method} answered ${answer.status()}: ${(await answer.text()).slice(0, 200)}`).toBe(200);
  const text = await answer.text();
  // A server may answer as one SSE event; the JSON is its data line.
  const json = JSON.parse(text.startsWith('{') ? text : (text.split('\n').find((line) => line.startsWith('data:')) ?? '').slice(5));
  expect(json.error, `${method}: ${JSON.stringify(json.error)}`).toBeUndefined();
  return { result: json.result, session: answer.headers()['mcp-session-id'] ?? session };
}

/** What a strict client checks of structuredContent: the schema's required keys, or one anyOf branch's. */
function satisfies(schema: Tool['outputSchema'], content: Record<string, unknown>): boolean {
  if (!schema) return true;
  const has = (keys: string[] = []) => keys.every((key) => key in content);
  return has(schema.required) && (!schema.anyOf || schema.anyOf.some((branch) => has(branch.required)));
}

test('a public MCP server: from the menu to its connect page, and a stranger\'s client queries a space through it', async ({ steward }) => {
  test.setTimeout(180_000);
  const { page } = steward;
  await openMcpServers(page);
  await shot(page, '01-mcp-servers');

  const wanted = process.env.JC_MCP_SERVER;
  const connect = page.getByRole('link', { name: 'Open the public connect page' });
  const card = wanted
    ? page.getByRole('region').filter({ has: page.getByText(wanted, { exact: true }) })
    : page.getByRole('region').filter({ has: connect });
  await expect(card.first(), 'the project must hold a public MCP server with a connect-page link').toBeVisible({ timeout: 30_000 });
  await card.first().getByRole('link', { name: 'Open the public connect page' }).click();
  await expect(page).toHaveURL(new RegExp(`/connect/${PROJECT}/[a-z0-9-]+`), { timeout: 20_000 });

  const tools = page.getByRole('heading', { name: /^Tools/, level: 2 });
  await expect(tools, 'the connect page must list the server\'s tools (CSP, CORS: T-4949)').toBeVisible({ timeout: 30_000 });
  const listed = await page.locator('section[aria-labelledby="connect-tools"] dt').allInnerTexts();
  expect(listed.length, 'the connect page lists no tool').toBeGreaterThan(0);
  const address = (await page.locator('section[aria-labelledby="connect-address"] pre').innerText()).trim();
  expect(address).toMatch(new RegExp(`^https://[^/]+/api/mcp/${PROJECT}/[a-z0-9-]+$`));
  await shot(page, '02-connect-page');

  // The external client: a fresh context, no cookie and no token, the address alone.
  const stranger = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
  try {
    const init = await rpc(stranger, address, 'initialize', {
      protocolVersion: '2025-06-18',
      capabilities: {},
      clientInfo: { name: 'journey-46', version: '1' },
    });
    expect(init.result.capabilities.tools).toBeDefined();
    const list = await rpc(stranger, address, 'tools/list', {}, init.session);
    const catalogue: Tool[] = list.result.tools;
    expect(catalogue.map((tool) => tool.name).sort(), 'the page and the client must see the same tools').toEqual([...listed].sort());
    const schema = (name: string) => catalogue.find((tool) => tool.name === name)?.outputSchema;

    // Without `endpoint` the server asks every member: the answer must pass its own outputSchema.
    const types = await rpc(stranger, address, 'tools/call', { name: 'list_types', arguments: {} }, init.session);
    expect(types.result.isError, JSON.stringify(types.result.content)).toBe(false);
    expect(satisfies(schema('list_types'), types.result.structuredContent), 'list_types answer fails its outputSchema').toBe(true);
    const results: { endpoint: string; entityTypes?: { typeList?: string[] } }[] = types.result.structuredContent.results;
    const member = results.find((entry) => (entry.entityTypes?.typeList ?? []).length > 0);
    expect(member, 'no member of the server names an entity type').toBeDefined();
    const type = member!.entityTypes!.typeList![0];

    for (const args of [{ type: [type], limit: 1 }, { endpoint: member!.endpoint, type: [type], limit: 1 }]) {
      const query = await rpc(stranger, address, 'tools/call', { name: 'query_entities', arguments: args }, init.session);
      expect(query.result.isError, JSON.stringify(query.result.content)).toBe(false);
      const content = query.result.structuredContent;
      expect(satisfies(schema('query_entities'), content), `query_entities ${JSON.stringify(args)} fails its outputSchema`).toBe(true);
      const entities = content.entities ?? content.results?.flatMap((entry: { entities?: unknown[] }) => entry.entities ?? []);
      expect(entities.length, `query_entities ${JSON.stringify(args)} found no ${type}`).toBeGreaterThan(0);
    }

    // The connect page promises read-only: a write tool is not even offered to the stranger.
    expect(catalogue.some((tool) => /^(create|upsert|update|delete|batch|merge|append|replace)_/.test(tool.name))).toBe(false);
    const refused = await stranger.post(address, {
      headers: { 'Content-Type': 'application/json', Accept: ACCEPT, ...(init.session ? { 'Mcp-Session-Id': init.session } : {}) },
      data: { jsonrpc: '2.0', id: 9, method: 'tools/call', params: { name: 'create_entity', arguments: { entity: { id: 'urn:ngsi-ld:Thing:t4871', type: 'Thing' } } } },
    });
    const body = JSON.parse((await refused.text()).replace(/^[\s\S]*?data:/, '').trim() || '{}');
    expect(body.error ?? body.result?.isError, `a stranger's create_entity was not refused: ${JSON.stringify(body)}`).toBeTruthy();
  } finally {
    await stranger.dispose();
  }
});

test('a viewer reads the MCP servers and is refused a new one in words', async ({ browser, baseURL }) => {
  test.setTimeout(120_000);
  const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
  const page = await context.newPage();
  try {
    await page.goto('/');
    await signIn(page, requireEnv('PORTAL_VIEWER_USER'), requireEnv('PORTAL_VIEWER_PASSWORD'), baseURL);
    await openMcpServers(page);
    const create = page.getByRole('button', { name: 'New MCP server' }).first();
    await expect(create).toBeVisible({ timeout: 20_000 });
    await expect(create).toHaveAttribute('aria-disabled', 'true');
    // Edit and Remove stay in view, disabled with the reason, never pressable.
    for (const control of await page.getByRole('button', { name: /^(Edit|Remove) / }).all()) {
      await expect(control).toHaveAttribute('aria-disabled', 'true');
    }
    await shot(page, '03-viewer-mcp-servers');

    // The same write past the disabled button: the server refuses it and says why.
    const proposed = await page.request.post(`/api/v1/projects/${PROJECT}/mcpservers`, {
      data: {
        apiVersion: 'joinedcontext.com/v1alpha1',
        kind: 'McpServer',
        metadata: { name: 't4871-viewer', namespace: PROJECT },
        spec: { audience: 'organization', members: [] },
      },
    });
    expect(proposed.status(), await proposed.text()).toBe(403);
    const problem = await proposed.json();
    expect(String(problem.hint ?? problem.detail ?? ''), 'the 403 must say why in words').toMatch(/[a-z]{3,} [a-z]{3,}/i);
    const after = await page.request.get(`/api/v1/projects/${PROJECT}/mcpservers`);
    expect(JSON.stringify(await after.json())).not.toContain('t4871-viewer');
  } finally {
    await context.close();
  }
});
