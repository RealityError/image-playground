# Component Guidelines

> How components are built in this project.

---

## Overview

Components are function components styled with Tailwind utility classes. Keep UI behavior close to the component that owns it, but avoid making image-heavy parent grids responsible for every per-card interaction state.

---

## Component Structure

<!-- Standard structure of a component file -->

- Define small typed helper components near the parent when they are only used by that parent.
- For large lists, introduce a memoized row/card wrapper when it lets each item subscribe to its own state and keeps the parent from re-rendering the full grid.
- Keep high-frequency gesture data in refs where possible, and commit React state updates only when the visual UI needs to change.

---

## Props Conventions

<!-- How props should be defined and typed -->

- Use named TypeScript props interfaces or inline object types for small local helper components.
- Pass task-level action callbacks as `(task) => void` or `(task, event) => void` from a parent, then bind the current task inside the memoized item wrapper.
- Avoid inline per-item callbacks directly in a large `.map()` when they force unchanged task cards to re-render.

---

## Styling Patterns

<!-- How styles are applied (CSS modules, styled-components, Tailwind, etc.) -->

- Tailwind classes are the default styling mechanism.
- Preserve stable dimensions for image-heavy cards so thumbnail loading does not shift the grid.

---

## Accessibility

<!-- A11y requirements and patterns -->

(To be filled by the team)

---

## Common Mistakes

<!-- Component-related mistakes your team has made -->

### Drag Or Pointer Work On Every Event

**Symptom**: Drag selection or scrolling feels uneven in task history.

**Cause**: `mousemove` or `scroll` handlers query DOM layout and update React/global state on every browser event.

**Fix**: Cache static geometry at drag start, schedule visual and selection updates with `requestAnimationFrame`, and skip state writes when the computed result has not changed.
