// The knowledge assistant under 20 concurrent public chats (T-3059, ADR-N-040): every chat answers
// with the stream's whole sequence, and the p95 of a full answer is recorded. The service's memory
// is read beside the run with `kubectl top pod -l app.kubernetes.io/name=jc-assistant` (the service
// serves no metrics route); its budget is 1 GB RSS with the embedding model loaded.
//
// Every chat is a model call paid from the deployment's daily token budget, so the run is bounded:
// JC_K6_CHATS chats in all (default 40, at most 200), never more, whatever the duration says.
import http from 'k6/http';
import crypto from 'k6/crypto';
import { check, fail } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const BASE = (__ENV.JC_ASSISTANT_URL || '').replace(/\/+$/, '');
const PUBLIC_ID = __ENV.JC_ASSISTANT_PUBLIC_ID || 'helsinki-public';
const ORIGIN = __ENV.JC_ASSISTANT_ORIGIN || '';
const CHATS = Math.max(1, Math.min(Number(__ENV.JC_K6_CHATS || 40), 200));
const QUESTIONS = [
  'What is the road weather at the Helsinki weather stations right now?',
  'Which events are on in Helsinki in the coming days?',
  'Which datasets does the city publish about air quality?',
  'Mikä on tiesää Helsingissä nyt?',
];

const answerMs = new Trend('assistant_answer_ms', true);
const answered = new Counter('assistant_answered');
const failed = new Counter('assistant_failed');

export const options = {
  scenarios: {
    twenty_concurrent: {
      executor: 'shared-iterations',
      vus: Math.min(20, CHATS),
      iterations: CHATS,
      maxDuration: '15m',
    },
  },
  thresholds: {
    // Every chat ends in an answer; a refusal or a stream error is a failure of the run.
    assistant_failed: ['count==0'],
    checks: ['rate==1'],
  },
};

export function setup() {
  if (!BASE) fail('JC_ASSISTANT_URL is not set: source tests/dev.env.sh assistant');
}

/** The leading zero bits of a SHA-256 digest. */
function zeroBits(bytes) {
  let bits = 0;
  for (const byte of bytes) {
    if (byte !== 0) return bits + Math.clz32(byte) - 24;
    bits += 8;
  }
  return bits;
}

/**
 * This VU's visitor token (T-4653, API/05 §1.2.1), earned as the widget earns it: the challenge,
 * the first nonce whose SHA-256 of `{challenge}:{nonce}` meets its bits, and the token. Each VU is
 * one visitor, as twenty people asking at once are.
 */
let token = null;
function visitorToken() {
  if (token) return token;
  const root = `${BASE}/api/v1/d/${PUBLIC_ID}`;
  const challenge = http.get(`${root}/challenge`).json();
  let nonce = 0;
  while (zeroBits(new Uint8Array(crypto.sha256(`${challenge.challenge}:${nonce}`, 'binary'))) < challenge.difficulty) nonce += 1;
  const earned = http.post(`${root}/token`, JSON.stringify({ challenge: challenge.challenge, nonce: String(nonce) }), {
    headers: { 'Content-Type': 'application/json' },
  });
  if (earned.status !== 200) fail(`no visitor token: ${earned.status}`);
  token = earned.json().token;
  return token;
}

/** The stream's event names, in order. */
function eventsOf(body) {
  return (body || '')
    .split('\n\n')
    .map((block) => block.split('\n').find((line) => line.startsWith('event: ')))
    .filter(Boolean)
    .map((line) => line.slice(7));
}

export default function () {
  const question = QUESTIONS[__ITER % QUESTIONS.length];
  const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream', 'X-JC-Visitor': visitorToken() };
  if (ORIGIN) headers.Origin = ORIGIN;
  const started = Date.now();
  const res = http.post(`${BASE}/api/v1/d/${PUBLIC_ID}/chat`, JSON.stringify({ message: question }), {
    headers,
    timeout: '180s',
  });
  const names = eventsOf(res.body);
  const ok = check(res, {
    'the chat answers 200': (r) => r.status === 200,
    'the stream opens a conversation and ends done': () => names[0] === 'conversation' && names[names.length - 1] === 'done',
    'the stream carries an answer and no error': () => names.includes('answer') && !names.includes('error'),
  });
  if (ok) {
    answered.add(1);
    answerMs.add(Date.now() - started);
  } else {
    failed.add(1);
  }
}

export function handleSummary(data) {
  const trend = data.metrics.assistant_answer_ms?.values ?? {};
  const line = `assistant: ${data.metrics.assistant_answered?.values.count ?? 0} answered, ${
    data.metrics.assistant_failed?.values.count ?? 0
  } failed, p95 ${Math.round(trend['p(95)'] ?? 0)} ms, max ${Math.round(trend.max ?? 0)} ms\n`;
  return { stdout: line };
}
