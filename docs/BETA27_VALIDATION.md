# Beta 27 release validation — 2026-10-22

Published source: `fec15f15a37d37c64157b3a6949866bb44615f57`.
[Windows verification run](https://github.com/Craftyxii/PoE2-Data-Logger/actions/runs/39854230231)
and [verified publication run](https://github.com/Craftyxii/PoE2-Data-Logger/actions/runs/39866700826)
both succeeded on their first attempts. The release tag points directly to that
source; later documentation updates do not change the installer.

## Windows release gates

| Gate | Result |
| --- | --- |
| Platform, capture-region, review-navigation and export fixtures | Passed |
| Native Windows interaction | Passed: 922 Win32 SendInput events, 23 phases |
| 100-map UI/logging workflow | Passed: 2,587 expected commits, zero failures |
| Complete isolated regression suite | Passed: 2,249 tests, zero failures/errors, three expected skips; 3,487.797 seconds |
| Frozen executable and bundled resources | Passed |
| Installation, update and running-client protection | Passed |
| Startup, recognition and saved-file retention | Passed |
| Installer checksum and exact-source publication | Passed |

The full-suite skips are the POSIX parent-death contract and the native Windows
and 100-map tests that run as separate mandatory gates. The 100-map workflow
injects scan results at the UI boundary; it checks logging, snapshots, row
decisions, totals, CSV/XLSX exports, backup and restart persistence. Actual
recognition is checked separately with captured images and catalog simulations.

Installer: [PoE2-Data-Logger-Setup-v1.3.2.27-beta.exe](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.27-beta/PoE2-Data-Logger-Setup-v1.3.2.27-beta.exe).
Its SHA-256 is
`0048bd512be4dcc081a2c405042bcc0458602cfcdeb47f18183ff84b91ff444d`.
The published checksum, publisher's hash and GitHub asset digest agree.

## Focused checks and source identity

The 152-check focused set passed without failures or skips. After the first
Windows run exposed one legacy test that omitted Ritual's explicit row approval,
that test was corrected without changing application behavior. All nine chain
discard checks and all 12 Ritual row-action checks then passed. The complete
Windows suite above validates the corrected test on the published source.

The catalog/real-capture audit ran on
`e5e2689096fad2cad19faf2e23e155c1b0d2a208`. Its only difference from the release
source is `tests/test_chain_discard_ui.py`; every other root Git object is
identical, including application code, artwork, dependencies and build tools.
The earlier audit remains attributed to its tested source.

- All 449 catalog IDs, 441 names and eight equipment footprints were compared
  with the original baseline through Currency and Ritual scans.
- Ritual retained all 449 complete grayscale footprints; 435 retained the
  expected identity as a name or candidate. All clear names/counts/categories
  were correct, with no extra footprints or new regressions.
- Currency retained 401 complete controls, with no new per-entry issues or
  wrong clear names/counts. Existing unconfirmed observations remain recorded.
- Of 3,143 direct artwork queries, 3,142 retained the expected identity and one
  existing Quill Rain control had no match. No confidently wrong identity,
  lost shared-art ambiguity or matching error was observed.
- Twenty-seven original/grayscale capture pairs produced 54 scans. The four
  confirmed armor, quantity-one, Idol conflict and Tetzlapokal cases passed
  their recorded reproductions. Four existing whole-PNG grayscale stress
  observations remain separate from artwork-only grayscale controls.
- Sacred Bloom is covered in both scanners, with repeated occurrences,
  Mirror/silver controls and preservation of an older profile's Item category.

These bundled-art controls exercise the pipeline; they do not establish accuracy
on unseen gameplay. The inline 23,281-Tribute screenshot did not supply original
scan pixels; the confidence correction was checked with captured training cases
and focused acceptance/ambiguity controls.

## Review, counting and HUD scope

Verified identities/counts bypass review. Unresolved identity, quantity, deferred
state and shared artwork remain independent confirmation requirements. A
verified missing quantity marker retains count one. Pending or rejected guesses
do not register names or teach artwork when the final page is saved.

Ritual reuses Currency's row actions in its existing Review column. Other HUD
controls, table columns, layout and styling remain. All 19 build methods match
the preceding layout after normalizing one guidance literal; native input also
checks the requested Ritual row approval.

Separate non-deferred Ritual pages count every occurrence independently for all
items and Omens. Deferred occurrences contribute zero new finds. Correcting the
same capture replaces its current page contribution while retaining audit
history. The focused occurrence checks cover repeated pages, snapshots, export
totals and restart persistence.
