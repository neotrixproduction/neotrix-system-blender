# Neotrix System for Blender

A Blender project browser and preview-render add-on for Neotrix production workflows.

## Install

1. Download `Neotrix_System_v1.8.4.zip` from [Releases](https://github.com/neotrixproduction/neotrix-system-blender/releases).
2. In Blender, open **Edit → Preferences → Add-ons → Install from Disk** and choose the ZIP.
3. Enable **Neotrix System**. In the 3D View, press **N** and open the **Neotrix** tab.
4. Set your project root, then choose **Explore Project Files**. **Ctrl+Alt+R** also opens the browser.

Tested with **Blender 5.2.1 LTS on Windows**. Minimum declared Blender version: 5.2.
No external Python dependencies are required.

## Browser

Browse projects, variants/films, shots, production stages and save versions.
Search filenames/folders; create versioned saves and open existing `.blend` files.
The filesystem browser understands the Neotrix production folder conventions.
Change the root to your own mapped drive or network share; `R:\` is the default.

Select a file and choose **Read** or **Write comment**. The discussion remains inside
this browser: scroll comments, edit your name, write multiline comments, and choose
**Post comment**. Enter inserts a line break; Ctrl+Enter posts. **Back to files** keeps
selection and filters. Unposted drafts survive switching discussion views while the
browser remains open. **Refresh comments** fetches changes from other applications.

## Quick Render

The sidebar provides still-image, low-resolution lookdev video, Workbench playblast,
and quick-animation previews. Quick animation renders the current frame ±6 at step 1.
Presets are editable. Preview rendering uses a temporary scene and restores the
working scene after completion/cancellation.

Any saved `.blend` file can render, independent of the browser root and without a
version token in its filename. A mapped drive and its network alias access the same
underlying files. Neotrix shot files retain their shot `RENDERS/v###` destinations;
other files use `RENDERS/<blend filename stem>` beside the saved document. Each
preview run has its own timestamp/UUID directory.

## Shared comments

Comments are UTF-8 JSON records under `<filename>.neotrix/comments/<UUID>.json`.
Neotrix Prism and creative-app integrations read/write this same schema. Comments
can be attached to any existing saved file. Each version has a separate discussion.
Keep the `.neotrix` folder with its document when moving or copying files.

See [the shared format](docs/SHARED_COMMENTS.md) and [validation](docs/VALIDATION.md).
This is file-sidecar metadata, not Windows Explorer Details properties or arbitrary
native AE markers/Fusion node notes. The source retains legacy Split Render code;
it is not exposed in the current default sidebar.

## Development

```powershell
python scripts/build_release.py
C:\blender-5.2.1-windows-x64\blender.exe --background --factory-startup --python scripts/smoke_blender.py
```

The release builder includes only `neotrix_system/`, excluding Python caches. The
smoke check exercises fresh registration, shared comments, render paths and cleanup.
