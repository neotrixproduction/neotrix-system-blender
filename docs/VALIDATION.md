# v1.8.4 validation

Validated 2026-10-13 on Windows with Blender 5.2.1 LTS.

- Python syntax checked for every packaged module; archive integrity checked.
- Exact release ZIP extracted and loaded by a fresh factory-startup Blender process.
- Add-on registered successfully; render operator/settings available; clean unregister.
- Ordinary and Neotrix-shot preview destinations checked independently of root.
- Shared read/write verified for Blender, Fusion, AE, Photoshop and Illustrator file extensions.
- 24 concurrent comment appends retained; malformed records reported without hiding valid ones.
- Browser helper tests verified multiline input, Ctrl+Enter posting and draft retention.
- Live GUI browser discussion and Back to files visually reviewed in the running Blender.
- A small live preview render completed and cleaned up its temporary scene earlier in the session.
- Real mapped-drive/UNC bidirectional comments check passed against the project share.

The new AE/Fusion launch entries belong to Neotrix Prism and were not exercised
inside those hosts in this release validation. Native Explorer Properties support,
other Blender versions/platforms and a clean second workstation remain unverified.

Reproduce the factory check with scripts/smoke_blender.py. Set NEOTRIX_ADDON_SOURCE
to an extracted ZIP directory to test the distribution rather than the checkout.
