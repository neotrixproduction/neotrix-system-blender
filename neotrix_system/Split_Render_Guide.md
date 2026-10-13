# Neotrix System · Split Render

Open **N → Neotrix → Split Render**. The arrow hides or shows the section.

## Fast setup

1. Click **Use Scene Collections** to use the scene's top-level geometry collections. Dedicated light collections stay shared. Alternatively, use **+** to choose individual collections, including nested ones.
2. Select the city/environment group and click **Set Base**. The first added group is Base by default.
3. Click **Prepare Split Render**.
4. Click **Render Split Frame** or **Render Split Animation**.

Save the scene inside a Relay shot's PROJECTS folder first. Configuration stays with the blend file when saved; preparation does not automatically save or render.

## Defaults

| Layer | Its collection | Other split groups | Unassigned geometry |
|---|---|---|---|
| Base | Visible | Indirect Only | Visible |
| Isolated group | Visible | Holdout | Holdout |

The Base keeps other groups' reflection and indirect-light contributions. Isolated groups remain cut by surrounding geometry without showing that geometry. Dedicated light/helper collections remain visible for lighting.

Each group has editable **Samples** (0 inherits the scene setting) and **Denoising**. Select a group and expand **Visibility Rules** to edit that layer's column of the collection matrix: Visible, Indirect Only, Holdout or Excluded. Reset restores the default rules; Set Base also resets the rules. Reordering groups changes the Alpha Over order above Base, which always stays at the bottom.

Parent collection restrictions affect children. Keep a parent Visible when its children need different rules. Preparation explains conflicting nested rules, shared objects, overlapping groups, missing collections, and mixed unassigned light/geometry collections. Collection names stay unchanged; output layer labels can be edited and must be unique, at most 48 characters, without slashes or line breaks.

## Output and compositor

One **Multilayer EXR** per frame contains **MergedBeauty** and each group's **Image, Depth, Mist and Emission**. Output uses **DWAA, Half Float (16-bit), RGBA**, under the current shot's `RENDERS/v###/frames`, named `CODE_Variant_0010_v###_0001.exr` according to foldering help. Blender 5.2 writes multipart EXR layers; the parts belong to a single file.

Prepare again after editing configuration, adding/renaming collections, or saving a new version. It updates tagged Neotrix layers/nodes rather than duplicating them. **Restore Original Compositor** restores the graph saved before the first preparation. Artist nodes are preserved in the managed graph copy; the original graph is retained as a backup. Generated controls mute other File Output nodes on the temporary render copy, ensuring only the split EXR is exported.

Split renders switch to **Compositing** and operate on a temporary scene. Only generated view layers render. Dimensions, pixel aspect, camera, frame rate and source render settings stay unchanged; the original frame is restored afterwards. Animation follows the scene's frame range and step. **Stop After Frame** finishes the current frame and stops the queue; Blender's normal Escape cancellation stops the render and queue. Completed files remain available. Rerendering the same version/frame replaces that EXR.

Use the split render buttons for this packaged workflow. Normal F12 uses Blender's regular scene output and existing view-layer enable flags. Quick Render keeps its own preview naming and cannot write the split File Output node.

The automatic merged beauty uses the displayed stack order. Inspect complex transparency, volumes and overlapping effects visually; an Alpha Over merge is not guaranteed to exactly match every single-render scene. Global object/collection render visibility is retained. World mist start/depth remains the scene's own setting.

## Verified demo

The prepared demo is in Relay: `R:\demo2\03_PRODUCTION\WORK\3D\SHOTS\DMO_Split_0050\PROJECTS\DMO_Split_0050_v013.blend`.

Tests verified four groups, nested geometry, shared lighting, retained reflections, holdout occlusion/alpha, per-layer sampling/denoising, repeat preparation, collection renaming, compositor restoration, failure rollback, saved configuration, interactive frame bounds, queue stopping and cancellation-handler cleanup. Actual frame and animation exports were inspected for all channels, DWAA and Half Float. Quick Render was checked for unwanted split exports. See `Neotrix_Split_Render_verified.png` for the merged image and individual layers.
