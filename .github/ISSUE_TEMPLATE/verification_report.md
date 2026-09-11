---
name: Verification report
about: Report that you tested an UNVERIFIED guess against real hardware/software
title: "[Verified] "
labels: unverified
---

**Which tracker item is this for?**
Reference the specific issue number (e.g. `#48`) from the [Verification tracker](../../issues?q=is%3Aissue+is%3Aopen+label%3Aunverified).

**What did you test against?**
- AOS8 firmware version:
- Controller type (Mobility Master + MDs / standalone):
- AP model(s):
- Aruba Central region/tenant (if Central-related):

**What did you find?**
Did the guess match, or was it wrong? Paste the real command output / API response /
field names you observed (redact anything sensitive — serials/MACs are fine, IPs and
credentials should be masked).

**Does the guess need correcting?**
- [ ] No — confirmed as-is
- [ ] Yes — here's the correct value:

**Anything else worth noting?**
Edge cases, firmware-version differences, things that only showed up under specific
conditions.
