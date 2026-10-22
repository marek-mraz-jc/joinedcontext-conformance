import { test, expect } from '@playwright/test';
import { Recorder, type Rules } from './verdict.js';

/**
 * The isolation verdict's own rules (T-4909), offline: a stream that keeps every rule passes, and
 * each kind of leak journey 48 looks for turns it red. No browser, no cluster.
 */
const RULES: Rules = {
  canaryMarkers: ['canary-abc123', 'otherspace-abc123'],
  authOnlyMarker: 'authonly-abc123',
  canaryType: 'GtfsStop',
  hiddenAttribute: 'cause',
  authOnlyAttribute: 'alertSource',
};

const ALERT = 'urn:ngsi-ld:Alert:t4909-abc123-a1';
const STOP = 'urn:ngsi-ld:GtfsStop:t4909-abc123-c1';

function notification(entities: unknown[]): string {
  return JSON.stringify({ metadata: { 'x-op': 'x-notification', 'x-seq': 1 }, body: { type: 'Notification', data: entities } });
}

/** An Alert as the anonymous Endpoint should serve it: shown attributes and a Relationship. */
const anonymousAlert = {
  id: ALERT,
  type: 'Alert',
  severity: { type: 'Property', value: 'medium' },
  refStop: { type: 'Relationship', object: [STOP] },
};
const authenticatedAlert = { ...anonymousAlert, alertSource: { type: 'Property', value: 'authonly-abc123' } };

test('a stream that keeps every rule has no finding and counts its notifications', () => {
  const r = new Recorder(RULES);
  r.frame('anonymous', 'ws:1', JSON.stringify({ metadata: { 'x-op': 'x-welcome', 'x-connection': 'c1' } }));
  r.frame('anonymous', 'ws:1', notification([anonymousAlert]));
  r.frame('authenticated', 'ws:2', notification([authenticatedAlert]));
  r.frame('authenticated', 'rest', JSON.stringify([authenticatedAlert]));
  r.frame('anonymous', 'ws:1', JSON.stringify({ metadata: { 'x-op': 'x-closing', 'x-reason': 'endpoint-changed' } }));
  expect(r.findings).toEqual([]);
  expect(r.notified).toEqual({ anonymous: 1, authenticated: 1 });
});

test('the canary marker anywhere is a leak, on either side', () => {
  const r = new Recorder(RULES);
  r.frame('authenticated', 'ws:2', notification([{ ...authenticatedAlert, note: 'x canary-abc123 y' }]));
  r.frame('anonymous', 'rest', 'plain text otherspace-abc123');
  expect(r.findings.map((f) => f.kind)).toEqual(['canary', 'canary']);
});

test('an entity of the canary type, joined inline or by its expanded type, is a leak', () => {
  const r = new Recorder(RULES);
  const joined = { ...anonymousAlert, refStop: { type: 'Relationship', object: [STOP], entity: { id: STOP, type: 'GtfsStop' } } };
  r.frame('anonymous', 'ws:1', notification([joined]));
  r.frame('authenticated', 'rest', JSON.stringify([{ id: STOP, type: 'https://smartdatamodels.org/dataModel.UrbanMobility/GtfsStop' }]));
  expect(r.findings.map((f) => f.kind)).toEqual(['canary-type', 'canary-type']);
});

test('the hidden attribute by name is a leak even with an innocent value', () => {
  const r = new Recorder(RULES);
  r.frame('authenticated', 'ws:2', notification([{ ...authenticatedAlert, cause: { type: 'Property', value: 'OTHER' } }]));
  expect(r.findings.map((f) => f.kind)).toEqual(['hidden-attribute']);
});

test('the authenticated-only attribute or its value on the anonymous side is a leak, and not on the authenticated one', () => {
  const r = new Recorder(RULES);
  r.frame('authenticated', 'ws:2', notification([authenticatedAlert]));
  expect(r.findings).toEqual([]);
  r.frame('anonymous', 'ws:1', notification([authenticatedAlert]));
  r.frame('anonymous', 'rest', JSON.stringify({ value: 'authonly-abc123' }));
  expect(r.findings.map((f) => f.kind)).toEqual(['auth-only', 'auth-only', 'auth-only']);
});

test('a notification after a hidden-only update is a finding on the channels sealed for it', () => {
  const r = new Recorder(RULES);
  r.frame('anonymous', 'ws:1', notification([anonymousAlert]));
  expect(r.seal(ALERT, ['ws:1', 'ws:3'])).toEqual(['ws:1']);
  // ws:3 never saw it: its first view is a fresh subscription's, not an update.
  r.frame('anonymous', 'ws:3', notification([anonymousAlert]));
  expect(r.findings).toEqual([]);
  r.frame('anonymous', 'ws:1', notification([anonymousAlert]));
  expect(r.findings.map((f) => f.kind)).toEqual(['hidden-only-update']);
});

test('a frame after x-closing on the same connection is a finding, a new connection is not', () => {
  const r = new Recorder(RULES);
  r.frame('anonymous', 'ws:1', JSON.stringify({ metadata: { 'x-op': 'x-closing', 'x-reason': 'endpoint-changed' } }));
  r.frame('anonymous', 'ws:4', notification([anonymousAlert]));
  expect(r.findings).toEqual([]);
  r.frame('anonymous', 'ws:1', notification([anonymousAlert]));
  expect(r.findings.map((f) => f.kind)).toEqual(['after-closing']);
  expect(r.report()).toContain('after-closing on anonymous ws:1');
});
