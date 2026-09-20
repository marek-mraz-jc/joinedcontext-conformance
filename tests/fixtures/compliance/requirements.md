---
sidebar_position: 1
title: "Fixture family"
---

# Fixture family

The ZZ and YY families exist only for `tests/test_compliance_script.py`. They are deliberately
not families of the platform, so scanning the real repositories never reads a fixture id as a
citation.

- **ZZ-01** [S] — The platform MUST refuse a write without a verdict.
- **ZZ-02** — The platform SHOULD name the field it refused.
- **ZZ-03** [S] — The platform MUST never log a secret.
- **ZZ-04** [S] — The platform MUST pin every image by digest.
- **YY-01** [H] — The Portal MUST name the field in the form.
