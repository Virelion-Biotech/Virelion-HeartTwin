# HeartTwin workstation UI references

Reviewed on 2026-10-19. These are public manufacturer materials, not authenticated
access to commercial software. HeartTwin retains its own branding, original source,
procedural illustration and existing execution contracts.

| Reference | Material inspected | Patterns used in HeartTwin |
| --- | --- | --- |
| [SIMULIA Living Heart Model](https://www.3ds.com/products/simulia/life-sciences-healthcare/living-heart-model) | Product description and linked Living Heart Human Model brochure | Geometry, physics and model configuration treated as stages in a modeling workflow rather than a marketing dashboard |
| [Sim4Life V9.6](https://sim4life.swiss/news/sim4life-news/sim4life-v96-release) | Manufacturer's embedded **V96_New_Sidebar.mp4**, including an actual workstation screen | Dark neutral canvas, compact navigation, model tree beside the viewport, context controls and a lower execution area |
| [Sim4Life GUI and usability](https://zmt.swiss/zh_cmn/sim4life-zh-cmn/framework-zh-cmn/gui-and-usability-zh-cmn) and [specifications](https://sim4life.swiss/specifications) | Manufacturer's explanation of tree-based modeling, settings, simulation and analysis | Direct structure visibility, view properties and explicit workflow navigation |
| [inHEART solution](https://www.inheartmedical.com/solution) | Public anatomy examples and process visualization | Anatomy-focused viewing, distinct structures, dark background and separation of input/model exploration from subsequent analysis |

These products differ substantially in scope. inHEART's public materials here concern
image-derived anatomy and procedural planning; Sim4Life is a broader computational
simulation platform; Living Heart is a multiphysics cardiac model. Their commercial,
scientific or regulatory claims do not transfer to HeartTwin through UI similarity.

No commercial logos, screenshots, anatomical meshes, product copy or private code are
embedded in the application. The heart shown before import is a labeled, original
schematic generated from simple parametric surfaces, with no invented result field.

Validation for this UI change includes executable geometry tests (surface extraction,
indices, units, node-order/activation contracts, finite values and viewer limits), static
asset/element checks, JavaScript syntax and the existing HTTP gateway tests including
real subprocess execution. Browser visual QA could not run: the managed preview failed
with `bwrap: Can't mount proc on /newroot/proc: Operation not permitted`. The UI was not
visually verified in a browser in this runtime.
