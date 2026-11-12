#!/usr/bin/env python3
"""Compose the T-6818 videos from the real recordings: terminal transcripts t1..t4.jsonl (replayed by
replay.html) on the left, the Playwright browser clips in vid/ on the right.
Output: ../mcp-settings.mp4 (1920x1080 H.264), ../mcp-settings-phone.mp4 (400x860), ../contact-sheet.png.
ffmpeg runs from mwader/static-ffmpeg:7.0.2 (the image's own ffmpeg is Playwright's VP8-only build).
"""
import json, os, subprocess
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)
IMG = "mwader/static-ffmpeg@sha256:d9b22f65c2049d73bf8ae556bb4c5c7d45e9fb85939d87951ed98f22b0f19105"
BG = "0x1e1e2e"
FONT = "/w/work/DejaVuSans.ttf"
os.makedirs(os.path.join(HERE, "norm"), exist_ok=True)


def ff(*args, probe=False):
    cmd = ["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}", "-v", f"{OUT}:/w", "-w", "/w/work"]
    if probe:
        cmd += ["--entrypoint", "/ffprobe", IMG, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"]
    else:
        cmd += [IMG, "-hide_banner", "-loglevel", "error", "-y"]
    return subprocess.run(cmd + list(args), check=True, capture_output=True, text=True).stdout.strip()


def dur(path):
    return float(ff(path, probe=True))


def norm(src, dst, vf, lead=0.0):
    ff("-ss", f"{lead}", "-i", src, "-vf", vf + ",fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18", dst)
    return dur(dst)


DESK = "scale=960:1080:flags=lanczos"
PHONE_IN_MAIN = f"scale=500:1075:flags=lanczos,pad=960:1080:(ow-iw)/2:(oh-ih)/2:color={BG}"
browser = [("before", "vid/before.webm", DESK), ("approve8", "vid/approve-chg-00000008.webm", DESK),
           ("after8", "vid/after-chg-00000008.webm", DESK), ("approve9", "vid/approve-chg-00000009-phone.webm", "select='not(between(t,18.5,19.8))',setpts=N/25/TB," + PHONE_IN_MAIN),
           ("after9", "vid/after-chg-00000009.webm", DESK)]
bd = {n: norm(src, f"norm/{n}.mp4", vf, 1.5) for n, src, vf in browser}  # 1.5 s: blank page before the first paint
# approve9 drops 18.5-19.8 s: the page reloads after the merge and shows a blank skeleton for that second

# Terminal track: t1, wait(approve8), t2, wait(after8), t3, wait(approve9), t4, wait(after9).
CUT = "\n\n`observedRevision`"   # t4's last paragraph, a side remark filed in chyby.md; the video marks the cut


def term(name):
    evs = [json.loads(l) for l in open(os.path.join(HERE, name + ".jsonl"))]
    for e in evs:
        for c in (e["ev"].get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text" and CUT in c["text"]:
                c["text"] = c["text"].split(CUT)[0]
                e["ev"]["message"]["content"].append({"type": "cut", "text": "[cut: one side remark on a revision field; full text in steps.md]"})
    return {"kind": "term", "prompt": open(os.path.join(HERE, name + ".prompt")).read().strip(), "events": evs}
plan = [term("t1"), {"kind": "wait", "sec": bd["approve8"]}, term("t2"), {"kind": "wait", "sec": bd["after8"]},
        term("t3"), {"kind": "wait", "sec": bd["approve9"]}, term("t4"), {"kind": "wait", "sec": bd["after9"]}]
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 960, "height": 1080}, record_video_dir=os.path.join(HERE, "vid"),
                        record_video_size={"width": 960, "height": 1080})
    pg = ctx.new_page()
    pg.add_init_script("window.PLAN = " + json.dumps(plan) + ";")
    pg.goto("file://" + os.path.join(HERE, "replay.html"))
    pg.wait_for_function("window.DONE === true", timeout=600000, polling=200)
    marks = pg.evaluate("window.MARKS")
    v = pg.video
    ctx.close(); b.close()
    os.replace(v.path(), os.path.join(HERE, "vid", "terminal.webm"))
tdur = norm("vid/terminal.webm", "norm/terminal-raw.mp4", "null")
off = tdur - marks[-1]                       # video starts before performance.now()'s origin
start = off + marks[0]
ff("-ss", f"{start:.2f}", "-i", "norm/terminal-raw.mp4", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "norm/terminal.mp4")
seg = [m - marks[0] for m in marks]          # starts of the 8 plan entries + end, from 0
T = [seg[i + 1] - seg[i] for i in range(8)]  # durations: t1, w, t2, w, t3, w, t4, w

# Browser track: before padded to t1, each clip padded by the following terminal turn.
pads = [("before", T[0] - bd["before"]), ("approve8", T[2]), ("after8", T[4]), ("approve9", T[6]), ("after9", 0)]
inputs, filt = [], []
for i, (n, extra) in enumerate(pads):
    inputs += ["-i", f"norm/{n}.mp4"]
    filt.append(f"[{i}:v]tpad=stop_mode=clone:stop_duration={max(extra, 0):.2f},setpts=PTS-STARTPTS[b{i}]")
filt.append("".join(f"[b{i}]" for i in range(len(pads))) + f"concat=n={len(pads)}:v=1:a=0[br]")
ff(*inputs, "-filter_complex", ";".join(filt), "-map", "[br]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "norm/browser.mp4")

# Captions: one per story step, at the bottom across both halves.
caps = [(0, seg[1], "1  Claude Code (left) renames one Endpoint title through the platform MCP: read, plan, propose. Right: the public page before."),
        (seg[1], seg[2], "2  A second person (four-eyes rule) opens the Change in the Portal, reads the diff, types the name and approves."),
        (seg[2], seg[4], "3  Claude reads it again: the new title is live, first in the Portal, then on the public Endpoint page."),
        (seg[4], seg[5], "4  Revert the same way: Claude proposes the old title through the MCP."),
        (seg[5], seg[6], "5  On a phone, the approver opens the revert, types the name and approves it."),
        (seg[6], seg[8], "6  Back to \"Helsinki road weather\": jc-prod ends as it started.")]
draw = []
for i, (a, z, text) in enumerate(caps):
    open(os.path.join(HERE, f"cap{i}.txt"), "w").write(text)
    draw.append(f"drawtext=fontfile={FONT}:textfile=cap{i}.txt:fontsize=30:fontcolor=white:box=0:"
                f"boxborderw=0:x=(w-text_w)/2:y=1010:enable='between(t,{a:.2f},{z:.2f})'")
open(os.path.join(HERE, "note.txt"), "w").write("Real run on jc-prod, 2026-11-12. Terminal waits over 2.2 s are shortened; browser clips play at 1x.")
draw.append(f"drawtext=fontfile={FONT}:textfile=note.txt:fontsize=18:fontcolor=white@0.7:x=w-text_w-16:y=h-28")
ff("-i", "norm/terminal.mp4", "-i", "norm/browser.mp4", "-filter_complex",
   f"[0:v]scale=880:990:flags=lanczos,pad=1040:990:80:0:color={BG}[t];[1:v]scale=880:990:flags=lanczos[b];"   # terminal ground fills the margin
   f"[t][b]hstack=inputs=2:shortest=1,pad=1920:1080:0:0:color={BG}," + ",".join(draw) + "[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "slow",
   "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "/w/mcp-settings.mp4")

# Phone cut: the browser half as a phone shows it. (clip, [(from, to)] in s after the 1.5 s lead, caption);
# trimmed to loaded frames: after8 shows "Loading…" until 3 s, approve9 enables Approve at 11 s (held 1.5 s) and shows Merged from 20 s.
phone = [("before-phone", [(0, None)], "1  Before: public page"),
         ("after-chg-00000008-phone", [(3.0, None)], "2  Approved: new title"),
         ("approve-chg-00000009-phone", [(9.0, 11.5, 1.5), (11.5, 14.5), (19.8, None)], "3  Revert: approver approves"),
         ("after-chg-00000009-phone", [(0, None)], "4  Reverted: old title")]
inputs, filt, parts = [], [], []
for i, (n, cuts, text) in enumerate(phone):
    norm(f"vid/{n}.webm", f"norm/{n}.mp4", "scale=400:860", 1.5)
    open(os.path.join(HERE, f"pcap{i}.txt"), "w").write(text)
    inputs += ["-i", f"norm/{n}.mp4"]
    filt.append(f"[{i}:v]split={len(cuts)}" + "".join(f"[s{i}_{k}]" for k in range(len(cuts))))
    for k, (a, z, *hold) in enumerate(cuts):
        rng = f"start={a}" + (f":end={z}" if z else "")
        hold = f",tpad=stop_mode=clone:stop_duration={hold[0]}" if hold else ""   # holds the enabled Approve before the click
        filt.append(f"[s{i}_{k}]trim={rng},setpts=PTS-STARTPTS{hold},pad=400:920:0:0:color={BG},"
                    f"drawtext=fontfile={FONT}:textfile=pcap{i}.txt:fontsize=19:fontcolor=white:x=(w-text_w)/2:y=860+(60-text_h)/2[p{i}_{k}]")
        parts.append(f"[p{i}_{k}]")
filt.append("".join(parts) + f"concat=n={len(parts)}:v=1:a=0[v]")
ff(*inputs, "-filter_complex", ";".join(filt), "-map", "[v]", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
   "-movflags", "+faststart", "/w/mcp-settings-phone.mp4")

total = dur("/w/mcp-settings.mp4")
times = [round(total * (i + 0.5) / 12, 2) for i in range(12)]
sel = "+".join(f"eq(n\\,{int(t * 25)})" for t in times)
ff("-i", "/w/mcp-settings.mp4", "-vf", f"select='{sel}',scale=960:540,tile=3x4", "-frames:v", "1", "-vsync", "vfr", "/w/contact-sheet.png")
json.dump({"segments_s": seg, "captions": [(round(a, 1), round(z, 1), t) for a, z, t in caps], "total_s": total,
           "sheet_times_s": times, "browser_clip_s": bd}, open(os.path.join(HERE, "timeline.json"), "w"), indent=1)
print(json.dumps({"total": total, "captions": [(round(a, 1), t[:40]) for a, _, t in caps]}, indent=1))
