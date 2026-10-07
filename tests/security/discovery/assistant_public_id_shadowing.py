#!/usr/bin/env python3
"""T-3227 finding: two projects may declare the same AssistantDeployment `publicId`.

jc-assistant serves `/api/v1/d/{publicId}/chat` and `/d/{publicId}/widget` for the first
deployment of that id it loads (crates/assistant/src/chat/mod.rs `deployment_of`), and nothing
refuses a second one: `jcctl validate` passes a repository where project `b` declares project
`a`'s `publicId`. So `b`'s steward can propose a deployment that answers on `a`'s public address
with `b`'s sources and connectors, and frames on `a`'s site by listing it in `allowedOrigins`.

Reproduce: `JCCTL=/path/to/jcctl python3 assistant_public_id_shadowing.py` exits 1 while the
finding stands (validate accepts the shadowing deployment) and 0 once it is refused.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOYMENT = """apiVersion: joinedcontext.com/v1alpha1
kind: AssistantDeployment
metadata:
  name: public
  namespace: {project}
spec:
  publicId: city-assistant
  channel: public
  sources: [web]
  allowedOrigins: [https://www.city-a.example]
  rateLimit: {{ requestsPerMinute: 30, perClientPerMinute: 5 }}
  budget: {{ tokensPerDay: 100000, tokensPerConversation: 20000 }}
"""

SOURCE = """apiVersion: joinedcontext.com/v1alpha1
kind: KnowledgeSource
metadata:
  name: web
  namespace: {project}
spec:
  source: website
  startUrls: [https://www.city-{project}.example/]
  visibility: public
"""

PROJECT = """apiVersion: joinedcontext.com/v1alpha1
kind: Project
metadata:
  name: {project}
  namespace: org
spec:
  organizationRef: hel
"""


def main() -> int:
    jcctl = os.environ.get("JCCTL", "jcctl")
    with tempfile.TemporaryDirectory() as root:
        repo = Path(root)
        for project in ("a", "b"):
            base = repo / f"projects/{project}"
            (base / "assistant/deployments").mkdir(parents=True)
            (base / "assistant/sources").mkdir(parents=True)
            (base / "project.yaml").write_text(PROJECT.format(project=project))
            (base / "assistant/sources/web.yaml").write_text(SOURCE.format(project=project))
            (base / "assistant/deployments/public.yaml").write_text(DEPLOYMENT.format(project=project))
        run = subprocess.run([jcctl, "validate", "--repo-dir", str(repo)], capture_output=True, text=True)
        output = run.stdout + run.stderr
        if run.returncode == 0 and "valid" in output and "publicId" not in output:
            print("FINDING: jcctl validate accepts two deployments of publicId city-assistant in projects a and b")
            return 1
        print("refused: " + output.strip().splitlines()[-1])
        return 0


if __name__ == "__main__":
    sys.exit(main())
