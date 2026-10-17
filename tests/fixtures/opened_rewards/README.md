Original paired Runeshape evidence: `Pair67_7socket_Family_28_TranscendentAlloy_opened` from `Runeshape_Seed_Evidence/01_Paired_Evidence`. The screenshot contains seven socket frames and one Transcendent Alloy reward; the gold selection arrow is outside the socket row.

- JPG: unchanged 1920 × 1080 original, SHA-256 `99846078fbab6cdf7a1800327ca14751e34d3d51bb322f5f7410cf38ca18f512`.
- PNG: unchanged 575 × 770 lossless panel crop from the paired evidence labels, SHA-256 `eff87f06866227683045e13b134b63c25eb3bc97f83c00290eecdad46d1339db`.

The 302 × 404 resize reproduces the false four-socket contour prefix. Production OCR tests use the original pixels without injected reward text.

`Pair63_4socket_Family_64_AncientWitchcraft_opened.jpg` is the unchanged 1920 × 1080 original from `Runeshape_Seed_Evidence/01_Paired_Evidence`, SHA-256 `0f14a942fb74e5fbaaed38c18e4451cc35e3ed4f7e0e5dd72e34148ad42436f1`. It has four socket frames and multiple rewards. Its detected nonzero left edge reproduces losing the first frame during repeated panel normalization.

`Pair74_6socket_Family30_ChillingFlux_opened.jpg` is the unchanged original from the same paired evidence, SHA-256 `d5f2e0f2edec709740e7c731c79e30bc34eb3b85f8d5750ad0ff206110e35294`. Its first row has six sockets. A connected first border leaves five isolated later frames, which must not be promoted to a complete five-socket row.

`Pair32_6socket_Family73_Thaumaturgic_Gems_opened.jpg` is the unchanged original from ZIP part 3, SHA-256 `61059c80e2eef29977773ff7557ba4156dfd99ef35e22cb7d6e99380f6c68ee5`. The first reward is Thaumaturgic Flux (Level 20), with six sockets. Native OCR garbles that first reward while recognizing the following rows; a bounded retry must read the original pixels rather than infer its level from the family.

`Pair61_5socket_Family_57_Thaumaturgic_Verisuim_chain_opened.jpg` is the unchanged original from ZIP part 2, SHA-256 `8083c88fdde92c5e47ddb40db72716d2a1456c1647bde7de947dd92e30091a1e`. The first reward is Thaumaturgic Flux (Level 19), with five sockets. Native OCR skips it, but the existing heading pass reads it before Verisium Manifestations (Level 20). Keep that observed earlier reward and the following skill levels separately.

The following unchanged originals from the supplied three-part `Runeshape_Seed_Evidence` archive reproduce missing native reward rows. These are source screenshots, not derived detector outputs. The existing independent manifest in `../propagation_crowns/manifest.json` supplies readable reward labels; its clipped/unnamed exclusions remain unchanged.
- `Pair12_3socket_Family70_Warding_opened.png`: unchanged 573 × 793 source, SHA-256 `61316faf07a1241dd8a1213ce07746baa678065b2b1848dc2a46f179994560d2`.
- `Pair40_6socket_Family_33_opened.jpg`: unchanged 1920 × 1080 source, SHA-256 `2fd5192d71c117e87068ce45b60b86a0a111372db88609d9f19d287abb1e1268`.
- `Pair87_7socket_Family27_Alloy_chain_opened.jpg`: unchanged 1920 × 1080 source, SHA-256 `6f8687d08118abfe3937a52bf78bbc0f98694a5195c2b7b69ad1c680af4d6b61`.
- `Pair92_3socket_Family70_Warding_Thaumaturgic18_opened.jpg`: unchanged 1920 × 1080 source, SHA-256 `422177e09d0921927542e6142d8d41ae1e52b85fcd1172a07ca496a1a8a64f26`.

Pair12 has a reward band merged with a horizontal separator. Pair40, Pair87, and Pair92 have right-edge frame components that incorrectly raise the native alignment floor. Tests require decoding each readable reward from the original pixels, including its actual quantity/level, on the full source and the detected panel. Family completion remains separate and intended.

The direct native-reader checks crop the prepared panel from source coordinates, preserving every independently readable reward: Pair12 `(43, 131, 573, 601)`, Pair40 `(0, 0, 575, 842)`, Pair87 `(49, 0, 575, 842)`, Pair92 `(48, 207, 575, 842)`. Full-source checks separately exercise native panel preparation.
