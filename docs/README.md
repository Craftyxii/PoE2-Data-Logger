# Project documentation

## Current guides

- [Project README](../README.md): downloads, features, exports and local storage.
- [User guide](USER_GUIDE.md): setup, scans, review, chains, Undo and exports.
- [Developer guide](../CONTRIBUTING.md): environment, regression checks and release workflow.
- [Source index](../SOURCES.md) and [dependencies](../DEPENDENCIES.md): module purposes and bundled components.
- [Changelog](../CHANGELOG.md): pending work followed by published version history.
- [Propagation recipe IDs](PROPAGATION_RECIPE_IDS.md) and [chain discard](CHAIN_DISCARD.md): targeted data and correction behavior.
- [Working-source regression review](CHANGE_REGRESSION_REVIEW.md): the latest targeted review, additional empty-map fix and verification limits.

The working source can include features absent from the latest installer. The README and changelog identify that distinction.

## Release and audit archive

Release notes and validation reports describe their named version or tested source revision. They are retained as historical evidence, not as current test status. Versions 1.3.2.8, 1.3.2.10 and 1.3.2.11 were unpublished builds; their changes were incorporated into later releases.

| Scope | Documents |
| --- | --- |
| Current source, 1.3.2.15 Beta | [Release notes](RELEASE_1.3.2.15.md), [targeted regression review](CHANGE_REGRESSION_REVIEW.md) |
| Previous beta, 1.3.2.14 | [Release notes](RELEASE_1.3.2.14.md), [code-review fixes](CODE_REVIEW_FIXES_1.3.2.14.md) |
| 1.3.2.13 | [Release notes](RELEASE_1.3.2.13.md), [OCR audit](OCR_BETA_1.3.2.13_AUDIT.md) |
| 1.3.2.12 | [Release notes](RELEASE_1.3.2.12.md) |
| Unpublished 1.3.2.10 / 1.3.2.11 | [1.3.2.10 notes](RELEASE_1.3.2.10.md), [1.3.2.11 notes](RELEASE_1.3.2.11.md) |
| 1.3.2.9 and preceding unpublished build | [1.3.2.9 notes](RELEASE_1.3.2.9.md), [validation](RELEASE_1.3.2.9_VALIDATION.md), [1.3.2.8 notes](RELEASE_1.3.2.8.md), [validation](RELEASE_1.3.2.8_VALIDATION.md) |
| Earlier betas | [1.3.2.7](RELEASE_1.3.2.7.md), [1.3.2.6](RELEASE_1.3.2.6.md), [1.3.2.5](RELEASE_1.3.2.5.md), [1.3.2.4](RELEASE_1.3.2.4.md), [1.3.1.3](RELEASE_1.3.1.3.md) |
| Stable release | [1.3.2 notes](RELEASE_1.3.2.md), [validation record](RELEASE_VALIDATION.md) |
| Additional historical investigations | [Deep bug audit](DEEP_BUG_AUDIT.md), [post-1.3.2.6 audit](POST_1.3.2.6_AUDIT.md), [targeted review validation](TARGETED_REVIEW_FIX_VALIDATION.md) |

Executable release gates are defined in [the Windows workflow](../.github/workflows/release.yml). Source or documentation changes are not published by editing these files.
