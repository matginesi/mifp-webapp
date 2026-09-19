# UI Kit

`ui-kit.html` is the debug-only living reference for the conference design system. It is intentionally not part of normal navigation. Open it from the Debug totem when `runtime.debug: true`.

The page renders **production components with production CSS**, rather than maintaining a second demo theme. It is the visual ground truth for:

- typography and theme/palette colour tokens;
- primary, outline, ghost, link and disabled button states;
- inputs, selects and registration form fields;
- cards, notices, warnings and badges;
- the polished Speakers **TBC** empty state;
- selectable image/media galleries using the current conference images when available;
- People cards and Program rows;
- registration pricing/deadline cards;
- responsive grid behaviour.

Use the Debug theme and palette selectors to audit contrast and component states together. With debug disabled, YAML appearance defaults are applied and no theme-switching UI is rendered.

When a new reusable production component is introduced, add one representative instance to `renderUiKit()` in `js/site.js` so the kit remains current.
