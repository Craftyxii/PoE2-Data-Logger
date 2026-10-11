# PoE2 Data Logger 1.3.2.12 Beta

This beta publishes the [OCR, review and Undo fixes prepared for 1.3.2.10](RELEASE_1.3.2.10.md), including the corrected native reset assertion described in [1.3.2.11](RELEASE_1.3.2.11.md). Neither earlier installer was published.

The 1.3.2.11 native Windows HUD and 100-map logging/export gates passed. Its complete suite ran 1,220 checks and stopped on one asynchronous export test: repeated native `QTest.qWait` calls could starve the Python export worker. The test now drains Qt events and briefly releases the Python interpreter to the worker, using the same 10-second deadline and unchanged draft/export assertions. A diagnostic using the actual CSV worker reproduced the stalled wait; the yielding event loop wrote all three files and retained the family draft in 0.13 seconds.

Application behavior is unchanged from 1.3.2.11; screenshot previews remain available on captured scans. App, executable, installer and prior-client guard metadata are aligned to 1.3.2.12 Beta. Changed functions retain purpose notes.

All native HUD, full regression, 100-map logging/export and installer/update gates remain required before publication. See [the detailed audit](DEEP_BUG_AUDIT.md) and release workflow artifacts for validation and platform limits.
