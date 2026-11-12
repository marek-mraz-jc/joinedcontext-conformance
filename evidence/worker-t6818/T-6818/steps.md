# T-6818 steps: Claude Code changes a setting on jc-prod through the platform MCP

Recorded 2026-11-12 10:30-11:05Z by worker-t6818. Setting: `metadata.title.en` of Endpoint
`helsinki-weather` in project helsinki ("Helsinki road weather"), public page
https://api.smartcity.joinedcontext.com/api/endpoint/6wrymzu5x2uwjo2xqei66ppw53mafvbu/.

## Files

- Videos stay on the shared mount (/workspace/evidence/T-6818/, 15 MB + 1 MB): `mcp-settings.mp4`: 1920x1080 H.264, 114.2 s. Left: terminal replay of the real `claude -p` stream-json
  transcripts (`work/t1..t4.jsonl`, prompts `work/t?.prompt`). Gaps over 2.2 s between events play as 2.2 s (marked "working…");
  every line is from the run. Right: the Playwright browser clips at 1x (`work/vid/*.webm`), held on their last frame while the terminal plays.
- `mcp-settings-phone.mp4`: 400x860, the browser half at a 400 px phone viewport. It shows the before page, the new title, the revert
  approval, then the old title. Decided (worker-t6818, owner may override): the first approval ran at desktop width only and the revert
  approval ran at phone width, because one Change can be approved only once. The main video shows the phone approval centred in its right half.
- `contact-sheet.png`: 12 frames, evenly spaced.
- `work/`: term_run.py (runs claude -p, MCP jc-platform-prod only, `--tools ""`), browser.py (Playwright segments), approver_login.py
  (off-camera sign-in + TOTP step-up, the realm's OTP is HMAC-SHA256), replay.html, compose.py (ffmpeg in mwader/static-ffmpeg@sha256:d9b22f65…,
  because the image's ffmpeg is Playwright's VP8-only build), timeline.json.

## Captions (main video, from timeline.json; checked on extracted frames at 10, 30 and 80 s)

| from | to | caption |
|---|---|---|
| 0.0 s | 15.3 s | 1  Claude Code (left) renames one Endpoint title through the platform MCP: read, plan, propose. Right: the public page before. |
| 15.3 s | 37.0 s | 2  A second person (four-eyes rule) opens the Change in the Portal, reads the diff, types the name and approves. |
| 37.0 s | 59.4 s | 3  Claude reads it again: the new title is live. The Portal and the public Endpoint page show it. |
| 59.4 s | 92.5 s | 4  Revert the same way: Claude proposes the old title, the approver approves it, here on a phone. |
| 92.5 s | 114.2 s | 5  Back to "Helsinki road weather": jc-prod ends as it started. |

## MCP calls (Claude Code 2.1.303, model sonnet, user mcp-admin)

HTTP: every call returned without a transport error and with is_error false over streamable HTTP, so each was a 200 JSON-RPC answer.
The Claude debug log does not print per-call status codes.

### t1: `claude -p` turn (new session)

Prompt: "In project helsinki, rename the public Endpoint helsinki-weather: its English title should read "Helsinki road weather stations". Read the current title first, plan the change, then propose it for approval. Change nothing else."

| t (s) | tool | arguments | HTTP | result |
|---|---|---|---|---|
| 3.46 | jc_resource_get | `{"kind": "Endpoint", "name": "helsinki-weather", "project": "helsinki"}` | 200 | "phase":"Live"; "title":{"en":"Helsinki road weather"},"description":{"" |
| 5.26 | jc_change_plan | `{"project": "helsinki", "changes": [{"op": "patch", "kind": "Endpoint", "name": "helsinki-weather", "patch": {"metadata": {"title": {"en": "Helsinki road weather stations"}}}}]}` | 200 | "planId":"am2e7msqzvvpnikpzv2ztfegyj"; "lane":"red" |
| 6.86 | jc_change_apply | `{"project": "helsinki", "planId": "am2e7msqzvvpnikpzv2ztfegyj"}` | 200 | "id":"chg-00000008"; "state":"PendingApproval"}],"approve":"a change w"; "lane":"red" |

### t2: `claude -p` turn (--resume)

Prompt: "A colleague approved chg-00000008 in the Portal. Read the change and the Endpoint again: is the new English title live?"

| t (s) | tool | arguments | HTTP | result |
|---|---|---|---|---|
| 2.99 | jc_change_get | `{"id": "chg-00000008", "project": "helsinki"}` | 200 | "phase":"Merged"; "lane":"red" |
| 3.4 | jc_resource_get | `{"kind": "Endpoint", "name": "helsinki-weather", "project": "helsinki"}` | 200 | "phase":"Live"; "title":{"en":"Helsinki road weather stations"},"descri" |

### t3: `claude -p` turn (--resume)

Prompt: "Thanks. Now put the old title back the same way: plan the revert of only that field to "Helsinki road weather" and propose it for approval."

| t (s) | tool | arguments | HTTP | result |
|---|---|---|---|---|
| 2.91 | jc_change_plan | `{"project": "helsinki", "changes": [{"op": "patch", "kind": "Endpoint", "name": "helsinki-weather", "patch": {"metadata": {"title": {"en": "Helsinki road weather"}}}}]}` | 200 | "planId":"zmzrwfv4oil7nwxqaer7m3gmuz"; "lane":"red" |
| 4.55 | jc_change_apply | `{"project": "helsinki", "planId": "zmzrwfv4oil7nwxqaer7m3gmuz"}` | 200 | "id":"chg-00000009"; "state":"PendingApproval"}],"approve":"a change w"; "lane":"red" |

### t4: `claude -p` turn (--resume)

Prompt: "chg-00000009 is approved. Read the Endpoint once more and confirm the title is back to what it was."

| t (s) | tool | arguments | HTTP | result |
|---|---|---|---|---|
| 2.38 | jc_change_get | `{"id": "chg-00000009", "project": "helsinki"}` | 200 | "phase":"Merged"; "lane":"red" |
| 2.87 | jc_resource_get | `{"kind": "Endpoint", "name": "helsinki-weather", "project": "helsinki"}` | 200 | "phase":"Live"; "title":{"en":"Helsinki road weather"},"description":{"" |


## Browser steps (person t6818-approver, steward on project helsinki until 18:00Z, signed in off camera with password + TOTP step-up)

The first approve attempt, before the step-up, answered 403 `second-factor-required` (`/api/v1/auth/login?step_up=true`).
Nothing changed. That clip was discarded and the run repeated after the step-up.

```
3.4s open Approvals list: https://portal.smartcity.joinedcontext.com/projects/helsinki/approvals | Approvals · helsinki · joinedcontext
   7.0s open change chg-00000008: Update Endpoint "helsinki-weather" (1 field changed) · helsinki · joinedcontext
  10.5s read the diff: Title › En before/after
  12.6s typed helsinki-weather to confirm a red-lane change
  13.9s Approve clicked: POST .../changes/chg-00000008/approve -> 202
  23.8s change page after approve: Helsinki | Approvals | chg-00000008 | Update Endpoint "helsinki-weather" (1 field changed) | chg-00000008 | Red | Merged | Proposed through Claude Code (MCP) | Author | MCP Admin(mcp-admin@smartcity.joinedcontext.com)
  24.1s video vid/approve-chg-00000008.webm
4.5s Portal heading: Helsinki road weather stations
   9.6s public heading: Helsinki road weather stations
  12.1s video vid/after-chg-00000008.webm
   4.5s Portal heading: Helsinki road weather stations
   9.6s public heading: Helsinki road weather stations
  11.9s video vid/after-chg-00000008-phone.webm
3.5s open Approvals list: https://portal.smartcity.joinedcontext.com/projects/helsinki/approvals | Approvals · helsinki · joinedcontext
   7.2s open change chg-00000009: Update Endpoint "helsinki-weather" (1 field changed) · helsinki · joinedcontext
  10.7s read the diff: Title › En before/after
  12.8s typed helsinki-weather to confirm a red-lane change
  14.0s Approve clicked: POST .../changes/chg-00000009/approve -> 202
  24.2s change page after approve: Helsinki | Approvals | chg-00000009 | Update Endpoint "helsinki-weather" (1 field changed) | chg-00000009 | Red | Merged | Proposed through Claude Code (MCP) | Author | MCP Admin(mcp-admin@smartcity.joinedcontext.com)
  24.3s video vid/approve-chg-00000009-phone.webm
4.6s Portal heading: Helsinki road weather
   9.8s public heading: Helsinki road weather
  12.2s video vid/after-chg-00000009.webm
   4.5s Portal heading: Helsinki road weather
   9.6s public heading: Helsinki road weather
  11.9s video vid/after-chg-00000009-phone.webm
```

## Before / after read

- Before (10:15Z, `jc_resource_get`): `title.en` "Helsinki road weather", phase Live, observedRevision f369752b.
- During (about 10:45Z, t2): "Helsinki road weather stations", observedRevision 711c1e25.
- After (about 11:05Z, `claude -p` + jc_resource_get): `title.en: Helsinki road weather`, `status.phase: Live`. The Portal heading and the
  public heading read "Helsinki road weather" too. Finding: observedRevision stayed 711c1e25 after chg-00000009 merged (chyby.md).

## Approver person

deployer 10:58Z: "t6818-approver disabled (204), OTP credential dropped (204), Secret jc-prod/t6818-approver-login deleted. Its steward
binding lapses by itself at 18:00Z." Local copies are gone: the browser state and the TOTP secret were deleted at 10:57Z, and the mcp-local token file was removed.

## Security checks

- No credential appears in any file: grep over work/ for bearer, eyJ…, password, refresh_token, access_token, cookie, KEYCLOAK_SESSION and totp
  found only variable names in the scripts. Claude session ids in the transcripts are replaced by `<redacted>`.
- The Keycloak login and the TOTP enrolment ran in a context that was not recorded, so no password field or code is on camera. tesseract is
  not installed: I read the contact sheet and four full-size frames instead, and the UI judge read the same frames. Neither found a password,
  token, cookie, OTP or session id.
- Touched: only `metadata.title.en` of helsinki/helsinki-weather, through two red-lane Changes that a second person approved. It is reverted.

## UI judge (Claude subagent, sonnet, contact sheet + frames 10/30/80 s + phone 20 s)

| criterion | first read | after fix |
|---|---|---|
| terminal readability at 1080p | 4 | 4 |
| layout | 3 (captions over content, footer overlap) | 4: captions in their own 90 px strip, panes scaled to 1760x990 (my re-read of the 30 s frame) |
| story | 4 | 4: plainer captions ("four-eyes rule" instead of PF-52) |
| phone cut | 1 (blank frame at 20 s, clipped caption) | 4: 1.5 s Playwright blank lead trimmed from every clip, short captions, no blank frame at 5 s steps (my re-read) |

Not fixed (low): the approver's author box shows the service address mcp-admin@smartcity.joinedcontext.com. This is a platform service account, not a person.
