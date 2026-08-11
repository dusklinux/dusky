# Elastic Workspace Rail Design

## Goal
Create a workspace rail where the focused workspace remains unmistakable while hover has its own elastic interaction language.

## States
- Inactive: compact segment showing its workspace number.
- Active idle: elongated segment; workspace number is replaced by a centered glowing dot; a persistent accent ring marks the active segment.
- Hovered inactive: hovered segment becomes elongated and receives a restrained Matugen light/sheens treatment; the active segment contracts to compact width but keeps its glowing dot and ring.
- Hovered active: active segment remains elongated with dot, ring, and hover sheen.
- Pointer leaves rail: hover expansion clears and the currently active workspace returns to elongated idle state.
- Click another workspace: focus transfers; that workspace becomes the new active dot+ring owner and remains elongated after the pointer leaves.

## Geometry
The rail keeps a stable total width. Exactly one segment owns the expanded width at a time: hovered segment when hovering, otherwise focused workspace. Segments have visible gaps and individual glass boundaries. Content remains centered in both compact and expanded sizes.

## Interaction
Hover continues to open the matching workspace preview. Leaving a workspace segment clears only the elastic hover state; the popup ownership logic may remain alive while the pointer transfers into the preview card. Clicking preserves existing exact workspace focus behavior.

## Visual treatment
Active uses a small glowing Matugen dot plus restrained ring. Hover uses a separate glass sheen and controlled illumination, not a large bloom. No vertical movement or geometry bounce is allowed.
