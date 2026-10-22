/**
 * The verdict of the WebSocket isolation run (T-4909, T-4872 step 3, docs API/07): every frame a
 * socket receives, and every REST answer read beside it, is judged here, so the rules are tested
 * offline (verdict.spec.ts) and the live journey 48 only feeds frames in.
 *
 * A finding is a leak or a broken close:
 *   - `canary`: the canary marker, which only the attribute every Endpoint hides, the entities of
 *     the type no Endpoint shows and the second space's entities carry, appears anywhere;
 *   - `canary-type`: an entity of the type no Endpoint shows, inlined by a join or notified;
 *   - `hidden-attribute`: the attribute every Endpoint hides, by name;
 *   - `auth-only`: the attribute only the authenticated Endpoint shows, or its marker, on the
 *     anonymous one;
 *   - `hidden-only-update`: a notification for an entity that changed only in hidden attributes
 *     since the connection saw it;
 *   - `after-closing`: anything on a connection after its `x-closing`.
 */

export type Kind = 'canary' | 'canary-type' | 'hidden-attribute' | 'auth-only' | 'hidden-only-update' | 'after-closing';

/** Which Endpoint a frame came from: the anonymous public one or the authenticated one. */
export type Side = 'anonymous' | 'authenticated';

export interface Finding {
  kind: Kind;
  side: Side;
  /** The connection (`ws:<n>`) or REST read (`rest`) the frame came from. */
  channel: string;
  detail: string;
}

export interface Rules {
  /** Strings no frame may carry: the canary marker and the second space's marker. */
  canaryMarkers: string[];
  /** The value of the authenticated-only attribute; never on the anonymous side. */
  authOnlyMarker: string;
  /** The entity type no Endpoint shows. */
  canaryType: string;
  /** The attribute every Endpoint hides. */
  hiddenAttribute: string;
  /** The attribute only the authenticated Endpoint shows. */
  authOnlyAttribute: string;
}

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

/** At most this much of a frame goes into a finding: enough to read, never a whole page. */
const EXCERPT = 300;

/** The entities a frame carries, nested or joined, with every object key on the way. */
function walk(node: Json, visit: (object: { [key: string]: Json }) => void): void {
  if (Array.isArray(node)) {
    for (const item of node) walk(item, visit);
  } else if (node !== null && typeof node === 'object') {
    visit(node);
    for (const value of Object.values(node)) walk(value, visit);
  }
}

/** A type member as a short name or an expanded IRI ending in it. */
function names(type: Json, wanted: string): boolean {
  const types = Array.isArray(type) ? type : [type];
  return types.some((t) => typeof t === 'string' && (t === wanted || t.endsWith(`/${wanted}`) || t.endsWith(`#${wanted}`)));
}

function keyNames(key: string, wanted: string): boolean {
  return key === wanted || key.endsWith(`/${wanted}`) || key.endsWith(`#${wanted}`);
}

export class Recorder {
  readonly findings: Finding[] = [];
  /** Notifications per side, the proof each socket was alive and not silent by accident. */
  readonly notified: Record<Side, number> = { anonymous: 0, authenticated: 0 };
  /** Entity ids each connection has seen notified, the precondition of sealing one. */
  private readonly seen = new Map<string, Set<string>>();
  /** Entity id -> the connections it is sealed for: only hidden attributes change from then on. */
  private readonly sealed = new Map<string, Set<string>>();
  private readonly closing = new Set<string>();

  constructor(private readonly rules: Rules) {}

  private find(kind: Kind, side: Side, channel: string, detail: string): void {
    this.findings.push({ kind, side, channel, detail: detail.slice(0, EXCERPT) });
  }

  /** Whether `channel` has seen a notification naming `id`. */
  hasSeen(channel: string, id: string): boolean {
    return this.seen.get(channel)?.has(id) ?? false;
  }

  /**
   * From now on `id` changes only in hidden attributes: a notification naming it on any of
   * `channels` is a finding. Only channels that already saw it are sealed, so a fresh
   * subscription's first view of it is never mistaken for an update.
   */
  seal(id: string, channels: string[]): string[] {
    const sealedFor = channels.filter((channel) => this.hasSeen(channel, id));
    this.sealed.set(id, new Set(sealedFor));
    return sealedFor;
  }

  /** One frame (a WebSocket message or a REST body) as text. */
  frame(side: Side, channel: string, text: string): void {
    const { rules } = this;
    if (this.closing.has(channel)) this.find('after-closing', side, channel, text);
    for (const marker of rules.canaryMarkers) {
      if (text.includes(marker)) this.find('canary', side, channel, text);
    }
    if (side === 'anonymous' && text.includes(rules.authOnlyMarker)) this.find('auth-only', side, channel, text);
    let message: Json;
    try {
      message = JSON.parse(text) as Json;
    } catch {
      // A frame that is not JSON carries no entity; the marker checks above still saw it.
      return;
    }
    walk(message, (object) => {
      if (typeof object.id === 'string' && names(object.type ?? null, rules.canaryType)) {
        this.find('canary-type', side, channel, `${object.id} in ${text}`);
      }
      for (const key of Object.keys(object)) {
        if (keyNames(key, rules.hiddenAttribute)) this.find('hidden-attribute', side, channel, text);
        if (side === 'anonymous' && keyNames(key, rules.authOnlyAttribute)) this.find('auth-only', side, channel, text);
      }
    });
    if (message === null || typeof message !== 'object' || Array.isArray(message)) return;
    const metadata = message.metadata;
    const op = metadata !== null && typeof metadata === 'object' && !Array.isArray(metadata) ? metadata['x-op'] : undefined;
    if (op === 'x-closing') this.closing.add(channel);
    if (op !== 'x-notification') return;
    this.notified[side] += 1;
    const body = message.body;
    const data = body !== null && typeof body === 'object' && !Array.isArray(body) ? body.data : undefined;
    for (const entity of Array.isArray(data) ? data : []) {
      if (entity === null || typeof entity !== 'object' || Array.isArray(entity) || typeof entity.id !== 'string') continue;
      const id = entity.id;
      if (this.sealed.get(id)?.has(channel)) this.find('hidden-only-update', side, channel, `${id} notified after a hidden-only update`);
      let ids = this.seen.get(channel);
      if (!ids) this.seen.set(channel, (ids = new Set()));
      ids.add(id);
    }
  }

  /** One line per finding, for the failure message. */
  report(): string {
    return this.findings.map((f) => `${f.kind} on ${f.side} ${f.channel}: ${f.detail}`).join('\n');
  }
}
