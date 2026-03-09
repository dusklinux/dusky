# Dusky TUI Master Template - Extreme Stress Testing Guide

This guide details the specific edge cases implemented in the `stress_test_dusky.sh` environment. The goal is to break the parsers, trap the bash math evaluators, and verify UI state integrity under extreme load.

## 1. Tab 0: "The Wall" (Massive List & Memory/Render Load)
**Objective:** Test rendering performance, terminal buffer overrun, and scrolling logic with massive datasets.
* **Test A (Rapid Scroll):** Hold `j` (down) or use `Page Down` to scroll rapidly from item 1 to 250.
    * *Expectation:* UI remains responsive. No screen tearing. Scroll indicator `▼ (more below) [x/250]` updates instantly.
* **Test B (Boundary Trap):** Scroll to exact item #250. Press `j` again.
    * *Expectation:* The list must stop cleanly at 250. Verify that `SCROLL_OFFSET` does not enter an invalid negative state or crash the array index.

## 2. Tab 1: "The Abyss" (Deep Nesting & Scope Tracking)
**Objective:** Verify that the `awk` parser correctly tracks blocks nested 6 levels deep, ignoring fake blocks.
* **Test A (Deep Write):** Select "Level 6 (Depth 6)" and press `l` (right) to increment.
    * *Expectation:* The status bar should not show a write failure. Open the target config and verify `val_l6` changed inside `l1 { l2 { l3 { l4 { l5 { l6 { ... } } } } } }`.
* **Test B (Comment Trap Integrity):** The config contains `# Trap: braces in comments { }` inside level 5.
    * *Expectation:* The `awk` brace counting logic (`push_block`/`pop_block`) must completely ignore commented braces. If it fails, Tab 1 items will show as `⚠ UNSET` because the scope path tracking was corrupted.

## 3. Tab 2: "Minefield" (Parser Traps & Arithmetic)
**Objective:** Validate input sanitization, floating-point precision, and octal evaluation traps.
* **Test A (Octal Traps - CRITICAL):** Modify "Octal 08 (Trap)" and "Octal 09 (Trap)".
    * *Expectation:* The engine must catch the leading `0`. Incrementing `08` must yield `9`. If the TUI crashes and dumps you to the terminal, the Base-10 sanitization failed.
* **Test B (Float Micro):** Increment "Float Micro" (`0.00001`).
    * *Expectation:* Math should step to `0.00002` (via `awk`). Ensure it doesn't drop floating precision, round to `0`, or crash native bash math.
* **Test C (Float Negative):** Decrement "Float Negative" (`-50.5`).
    * *Expectation:* Steps to `-50.6`. Test crossing the zero boundary (`-0.1` -> `0.0` -> `0.1`).
* **Test D (Missing/Empty Keys):** "Explicit Empty" (`val_empty`).
    * *Expectation:* Should handle the empty string gracefully and apply the default/minimum value upon first adjustment.

## 4. Tab 3: "Menus" (Drill Down & Context Switching)
**Objective:** Test UI state preservation when switching views.
* **Test A (State Save/Restore):** Scroll down to item 3 in Tab 3. Press `Enter` to open "Deep Controls >".
    * *Expectation:* View switches to Submenu. Press `Esc`. You MUST be returned to exactly item 3 in Tab 3, not the top of the list.
* **Test B (Cross-Tab Modification):** Inside the submenu, modify "Deep Value L6".
    * *Expectation:* Because this maps to the same config block as Tab 1, it must write correctly to the file without duplicating the block.

## 5. Tab 4: "Palette" (Regex, Hex, and Hash Parsing)
**Objective:** Stress test the comment-stripping regex logic.
* **Test A (Hash Space Trap):** "Hex Space Trap" (`# 998877`).
    * *Expectation:* Ensure the parser handles the space correctly. If it assumes it's an inline comment and strips it, verify it doesn't corrupt the file on write.
* **Test B (Comment Stripping):** "Hex Cmt Spc" (`#ff0000 # comment`).
    * *Expectation:* Should display strictly as `#ff0000`. The `# comment` must be stripped by `awk` during read, but ideally preserved in the file during write.
* **Test C (No-Space Comment):** "Hex Cmt Tgt" (`#00ff00#comment`).
    * *Expectation:* Tests regex strictness. Should read as `#00ff00#comment`.

## 6. Tab 5: "Root" (Global Scope Config)
**Objective:** Test keys that exist outside of any `{ }` block.
* **Test A:** Toggle "Root Performance" (`performance = true` at the top of the file).
    * *Expectation:* Must modify the key at the *top* of the file. It absolutely must NOT append a duplicate key at the bottom of the file under a global scope fallback.

## 7. Tab 6: "Void" (Empty UI State)
**Objective:** Ensure arrays with zero length do not trigger division-by-zero or indexing faults.
* **Test A:** Switch to Tab 6.
    * *Expectation:* UI renders an empty box. Pressing `j`, `k`, `h`, `l`, or `Enter` must do absolutely nothing and cause no terminal errors.

## 8. Tab 7: "Awk-ward" (Special Characters)
**Objective:** Inject reserved regex and shell characters into `awk` strings.
* **Test A:** Cycle through "Awk Char Trap 1".
    * *Expectation:* The cycle contains `~!@$%^&*()_+` and `<>?{}`. When the write function runs, `awk` must not interpret these as regex operators (e.g., `&` acting as a backreference) or crash the string substitution.
