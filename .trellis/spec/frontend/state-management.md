# State Management

> How state is managed in this project.

---

## Overview

The frontend uses Zustand for app-wide client state. Prefer narrow selectors that return only the fields a component needs, especially for image-heavy views such as task cards and the input bar.

---

## State Categories

<!-- Local state, global state, server state, URL state -->

- Global client state: prompt, params, task records, selected task ids, modal ids, settings, and lightweight server stats live in `frontend/src/store.ts`.
- Component state: transient UI interaction state such as swipe offsets, selection boxes, menu open flags, and input focus state should stay local.
- Server state: `/web/*` data is folded into the store only after normalization. Polling updates should be treated as informational unless values changed.

---

## When to Use Global State

<!-- Criteria for promoting state to global -->

- Use global state when multiple distant components need the same value or action.
- Keep high-frequency interaction state local unless another component truly needs it.
- In list item components, subscribe to the smallest stable slice. For example, a task card wrapper should subscribe to `selectedTaskIds.includes(task.id)`, not the entire `selectedTaskIds` array when only its own selected state matters.

---

## Server State

<!-- How server data is cached and synchronized -->

- Polling setters should skip store notifications when the merged value is equal to the current value.
- When polling values are partially consumed, components should subscribe to the specific field they use. For example, the input bar uses `serverStats.userConcurrencyLimit`; it should not re-render for unrelated online or queue count changes.

```typescript
const userConcurrencyLimit = useStore((s) => s.serverStats.userConcurrencyLimit)
```

---

## Common Mistakes

<!-- State management mistakes your team has made -->

### Broad Selectors In Image-Heavy UI

**Symptom**: Typing, polling, or selecting cards feels sticky as history grows.

**Cause**: A card or input component subscribes to a whole object such as `settings`, `serverStats`, or `selectedTaskIds`, so unrelated changes re-render many heavy children.

**Fix**: Subscribe to the exact primitive or derived boolean needed, and make setters no-op when values are unchanged.
