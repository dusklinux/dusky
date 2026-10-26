# Neovim — editing and configuration reference

> [!abstract] How to use this note
> Start with the mental model, movement and editing grammar; then use the recipes for search, replacement and macros. All keys are **Normal mode** unless a different mode is named. `:` commands are entered in Neovim, not in the shell. Press `<CR>` to execute them.
>
> Reviewed against installed **Neovim 0.12.6** and its local help; key editing recipes were tested in clean scratch buffers. The supplied source tree identifies itself as **0.13.0-dev** and has no Git metadata; it is a different development snapshot, not proof of the ISO version. An isolated build of that snapshot was also tested with the source runtime and installed Tree-sitter parsers. Version-specific differences are called out below. Mappings and plugins can change; inspect them with `:verbose nmap {keys}`.

## Quick lookup

| Goal | Start here |
|---|---|
| Move around and return to where you were | [[#Movement, jumps and marks]] |
| Change a word, quote or block | [[#Editing grammar and text objects]] |
| Replace text on several lines or throughout a file | [[#Search and replace]] |
| Repeat a sequence of edits | [[#Macros]] |
| Edit a column or several line beginnings | [[#Visual selections and block editing]] |
| Find files, buffers or project text | [[#Files, buffers, windows and tabs]] and [[#Project search and quickfix]] |
| Work on several files or search results | [[#Batch editing across files]] |
| Understand options, mappings and plugins | [[#Configuration and introspection]] |

## Mental model and notation

Neovim is modal: **Normal** mode combines commands, **Insert** mode enters text, **Visual** mode selects text, and **Command-line** mode runs Ex commands or searches. `<Esc>` returns to Normal from most modes; in Terminal mode use `<C-\><C-n>`.

`<C-x>` means hold Ctrl and press x; `<CR>` is Enter; `<Esc>` is Escape. `{char}`, `{motion}` and `{register}` are placeholders, not literal braces. Your `<leader>` is Space. A count precedes a command: `5j` moves down five lines.

The editing grammar is **operator + motion** or **operator + text object**. `d2w` deletes through two word motions; `ci"` changes the contents of a quoted string. `.` repeats the last change; `u` undoes it. Search, movement and recording a macro do not themselves change text.

> [!important] Your configuration changes some defaults
> Flash maps Normal-mode `s` and `S`, so they do not perform the stock substitute-character and substitute-line commands. `r` still replaces one character in Normal mode; Flash maps `r` in operator-pending mode. Normal `n`, `N`, `<C-d>` and `<C-u>` also recenter the view. Your Normal `J` joins lines and restores the cursor using mark `z`, replacing any previous mark `z`. Use `:verbose nmap s` or `nvim --clean` to investigate differences.

## Start, save and leave

```bash
nvim file.txt              # open a file
nvim +42 file.txt          # start at line 42
nvim -o a.txt b.txt        # horizontal splits
nvim -O a.txt b.txt        # vertical splits
nvim -p a.txt b.txt        # tab pages
nvim -d a.txt b.txt        # compare files
nvim --clean file.txt      # factory defaults, no user config or plugins
```

| Command | Action |
|---|---|
| `:e file` | Open a file; Tab completes names |
| `:e!` | Reload the current file from disk, discarding unsaved buffer changes |
| `:find file` | Find a file using `path` and open it |
| `:file` | Show the current buffer name and status |
| `:enew` | New empty buffer |
| `:w` / `:update` | Write / write only if modified |
| `:w newfile` | Write a copy; keep editing the current buffer |
| `:saveas newfile` | Write as a new file and switch the buffer name |
| `:q` / `:q!` | Close window / force close, discarding unsaved changes where applicable |
| `:wq` / `:x` | Write and quit / write if modified and quit |
| `:wa` / `:qa` / `:wqa` | Write all modified buffers / quit all / write and quit all |
| `ZZ` / `ZQ` | Like `:x` / `:q!` |

## Movement, jumps and marks

### Within a line and between words

| Keys | Move |
|---|---|
| `h` `j` `k` `l` | Left, down, up, right |
| `0` / `^` / `$` / `g_` | Start / first nonblank / end / last nonblank of line |
| `w` / `b` / `e` / `ge` | Next word start / previous word start / word end / previous word end |
| `W` / `B` / `E` | Equivalent motions over whitespace-delimited WORDs |
| `f{char}` / `F{char}` | Find character forward / backward on this line |
| `t{char}` / `T{char}` | Move just before / just after character on this line |
| `;` / `,` | Repeat last character find in same / opposite direction |
| `%` | Jump to matching bracket or other configured match |
| `gj` / `gk` | Move by displayed line when text wraps |
| `g0` / `g$` | Start / end of the displayed screen line |

A `word` follows the `iskeyword` option and punctuation boundaries; a `WORD` is a run of non-whitespace. `dt,` deletes up to but not including the next comma; `df,` includes it.

### File and screen navigation

| Keys | Move |
|---|---|
| `gg` / `G` | First / last line |
| `42G` or `:42` | Line 42 |
| `{` / `}` | Previous / next paragraph boundary |
| `(` / `)` | Previous / next sentence |
| `<C-e>` / `<C-y>` | Scroll view down / up one line |
| `H` / `M` / `L` | Top / middle / bottom of window |
| `<C-d>` / `<C-u>` | Half-page down / up |
| `<C-f>` / `<C-b>` | Page forward / back |
| `zz` / `zt` / `zb` | Put cursor line at center / top / bottom of window |

### Return to previous locations

| Keys | Action |
|---|---|
| `<C-o>` / `<C-i>` | Older / newer position in the window's jumplist |
| `:jumps` | Inspect jumplist |
| `g;` / `g,` | Older / newer position in the buffer's changelist |
| `:changes` | Inspect changelist |
| `ma` | Set local mark `a` at cursor |
| `` `a `` / `'a` | Return to exact position / first nonblank of marked line |
| `mA` / `` `A `` | Set / jump to uppercase file mark, which can cross files |
| `` `. `` / `` `^ `` | Last change / last Insert-mode position |
| `` `[ `` / `` `] `` | Start / end of previously changed or yanked text; also set when writing the buffer |
| `` `< `` / `` `> `` | Start / end of the last Visual selection |
| `:marks` | Inspect marks |

The jumplist records **jumps**, not every `j` or `k` movement. `<C-i>` may be indistinguishable from Tab in some terminals. Your Normal `<Tab>` mapping cycles buffers, so in that situation it can intercept `<C-i>` rather than move forward through the jumplist. `gi` resumes Insert mode at the last insertion position.

## Editing grammar and text objects

### Operators and common changes

| Operator | Action | Example |
|---|---|---|
| `d` | Delete | `d$` deletes to end of line |
| `c` | Change, then enter Insert mode | `ciw` changes the current word |
| `y` | Yank (copy) | `yi"` copies inside quotes |
| `>` / `<` | Indent / outdent | `>ap` indents a paragraph |
| `=` | Reindent using configured indentation | `==` reindents line |
| `gq` | Format text | `gqap` formats a paragraph |

Double the operator for a line: `dd`, `cc`, `yy`, `>>`, `<<`. A count can apply to the operator or motion: `3dd` deletes three lines; `d2w` deletes through two word motions. `D` and `C` delete or change to the end of the line. `x` deletes the character under the cursor; `rX` replaces it with `X`. `cw` on a nonblank has special behavior similar to `ce`; use `ciw` to change the entire word. `X` deletes the character before the cursor.

### Text objects

`i` means inner; `a` means around. Word objects can include whitespace with `a`; bracket and quote objects include their delimiters with `a`. These are stock descriptions. Your configuration also loads `mini.ai`, which changes or extends some bracket, quote and other text objects; see `:help MiniAi` and `:verbose omap i` when selection boundaries differ.

| Object | Unit | Examples |
|---|---|---|
| `iw` / `aw` | Word | `ciw`, `daw`, `viw` |
| `iW` / `aW` | WORD | `ciW` |
| `i"` / `a"` | Double-quoted text | `ci"`, `ya"` |
| `i'` / `a'` | Single-quoted text | `ci'` |
| `` i` `` / `` a` `` | Backtick-quoted text | `` di` `` |
| `i(` / `a(` | Parenthesized block | `ci(`, `da(` |
| `i[` / `a[` | Bracketed block | `di[` |
| `i{` / `a{` | Braced block | `va{` |
| `ip` / `ap` | Paragraph | `yip`, `dap` |
| `is` / `as` | Sentence | `dis` |
| `it` / `at` | Tag block | `cit` |

`%` is a matching-item motion, not an inside object: `d%` deletes from the cursor to its matching item, inclusive. Use `di(` to delete inside parentheses.

### Insert, repeat and undo

| Keys | Action |
|---|---|
| `i` / `a` | Insert before / after cursor |
| `I` / `A` | Insert at first nonblank / append at end of line |
| `o` / `O` | Open a line below / above |
| `R` | Replace mode until `<Esc>` |
| `u` / `<C-r>` | Undo / redo |
| `.` | Repeat last change |
| `:undolist` / `g-` / `g+` | Inspect / move backward / forward through undo history |
| `:earlier 5m` / `:later 5m` | Navigate undo history by time |

Example: `ciwnew<Esc>` changes a word; move to another word and press `.`. Dot repeats the change, not navigation or an entire macro. In Insert mode `<C-w>` deletes the previous word, `<C-u>` deletes text entered before the cursor on the current line; with no newly entered text, its behavior also depends on `backspace`. `<C-r>{register}` inserts a register. `<C-o>` executes one Normal command and returns to Insert mode.

### Completion and insertion while typing

These keys are used **in Insert mode**; completion plugins can change them.

| Keys | Action |
|---|---|
| `<C-n>` / `<C-p>` | Next / previous keyword completion candidate |
| `<C-x><C-f>` | Complete a file name |
| `<C-r>"` / `<C-r>0` | Insert the unnamed / most recent yank register |
| `<C-r>%` | Insert the current file name |
| `<C-v>` followed by a character | Insert the next character literally; useful for a literal Tab |
| `<C-o>` followed by a Normal command | Execute one Normal command, then return to Insert mode |

See `:help ins-completion` and `:help i_CTRL-R` for other completion sources and register insertion. With a completion menu open, the plugin or `completeopt` settings determine which keys accept or dismiss a candidate.

### Useful line operations

| Keys | Action |
|---|---|
| `J` / `gJ` | Join lines with whitespace adjustment / without inserting whitespace |
| `>>` / `<<` | Indent / outdent line; `3>>` acts on three lines |
| `gg=G` | Reindent the buffer according to current indent settings |
| `~` / `gUiw` / `guiw` | Toggle character case / uppercase / lowercase a word |
| `<C-a>` / `<C-x>` | Increment / decrement a number at or after cursor |
| `gcc` / `gc{motion}` | Toggle comments on the current line / lines covered by a motion |
| `:sort` | Sort all lines by default; `:'<,'>sort u` sorts selected lines and removes duplicates |

Commenting depends on the filetype's `commentstring`; plugins can override these mappings. `3gcc` toggles three lines; `gcap` toggles a paragraph; select lines and press `gc` to toggle the selection. Your configuration loads `mini.comment`, so inspect `:verbose nmap gcc` if behavior differs.

### Move, copy and insert lines with Ex commands

Ex commands accept line addresses. `.` is the current line, `$` the last, `%` the entire buffer, and `'<,'>` the last Visual selection's lines. A destination for `:move` or `:copy` is the line **after which** text is placed.

| Command | Action |
|---|---|
| `:m .+1` / `:m .-2` | Move the current line down / up one line |
| `:t.` | Copy the current line below itself |
| `:10,12m $` | Move lines 10–12 to the end of the buffer |
| `:'<,'>t $` | Copy selected lines to the end |
| `:read file.txt` | Insert a file below the current line |
| `:read !date` | Insert the output of `date` below the current line |

The first and last line cannot move beyond the buffer. After selecting lines, press `:` to insert `'<,'>` automatically before the command. Your `mini.move` plugin also maps `<M-j>` / `<M-k>` to move the current line in Normal mode or the selection in Visual mode; `<M-h>` / `<M-l>` move horizontally. `<M-x>` means Alt+x; terminal key delivery and indentation settings can affect the result.

## Visual selections and block editing

`v` selects characters, `V` selects lines, and `<C-v>` selects a rectangular block. Extend with motions or text objects, then apply `d`, `y`, `c`, `>`, `<` or `=`. `o` switches the active end; `gv` reselects the last selection. Your Visual `>` and `<` mappings reselect automatically.

**Prefix three lines:** place the cursor at the first character of the first line, type `<C-v>jjI# <Esc>`. The prefix appears on the remaining selected lines after Escape. For a suffix on unequal-length lines, select lines with `<C-v>jj$`, then `A;<Esc>`. Block editing depends on the selected columns and short lines; check the result when lines are uneven.

**Replace a column:** select a rectangular region with `<C-v>` and motions, then `cNEW<Esc>`. For text at different columns, search/substitute or a macro is usually a better fit.

Use `gU`, `gu` or `g~` on a selection to uppercase, lowercase or toggle its case. In block mode, `d` deletes the selected rectangle and `rX` replaces each selected character with `X`. `o` changes the active selection endpoint; in block mode `O` switches to the other corner on the same line.

> [!note] Repeating a Visual edit depends on version
> In installed 0.12.6, `.` generally repeats a Visual operation over an area of the same size. For example, deleting `cat` with `viwd` and repeating on `elephant` deletes three characters, not the entire word. In the tested 0.13.0-dev build, replayable Visual selections are repeated semantically, so the same example deletes the entire second word. Check `:help visual-repeat` on the version you run; some selections and mappings have special behavior.

Select lines with `V` and use `g<C-a>` to increment numbers sequentially; for example three lines initially containing `0` become `1`, `2`, `3`. Plain `<C-a>` applies the same increment to each selected line.

## Search and replace

### Search

| Keys | Action |
|---|---|
| `/pattern<CR>` / `?pattern<CR>` | Search forward / backward |
| `n` / `N` | Next match in search direction / opposite direction |
| `*` / `#` | Search forward / backward for word under cursor |
| `g*` / `g#` | Search forward / backward for the text under the cursor without whole-word boundaries |
| `:noh` | Clear current search highlighting until the next search |
| `:set ignorecase smartcase` | Ignore case unless the typed pattern contains uppercase |

Neovim uses its own regular-expression syntax. Useful atoms: `\<` and `\>` for word boundaries; `.` for any character; `^` and `$` for line boundaries; `\+` for one or more; `\s` for whitespace; `\c` / `\C` force case insensitive / sensitive; `\V` makes most characters literal. For example `/\<cat\>` finds the word `cat`; `/\Ccat` forces case-sensitive matching. A delimiter and backslash still need escaping in a literal pattern, e.g. `/\Vpath\/file`.

### Substitute: replace on lines, in a selection or in a file

```text
:[range]s/pattern/replacement/[flags]
```

| Example | Scope and result |
|---|---|
| `:s/old/new/` | First match on current line |
| `:s/old/new/g` | Every match on current line |
| `:%s/old/new/g` | Every match in the buffer |
| `:%s/old/new/gc` | Every match, asking for confirmation |
| `:10,20s/old/new/g` | Lines 10–20 inclusive |
| `:.,$s/old/new/g` | Current line through end |
| `:%s/\<old\>/new/gc` | Whole-word matches, with confirmation |
| `:%s/old/new/gn` | Count matches without changing text |
| `:%s#old/path#new/path#g` | Use `#` as delimiter |
| `:%s/\s\+$//e` | Remove trailing whitespace; `e` suppresses a missing-pattern error |

`g` means all matches **per addressed line**, not the whole file (assuming the usual `nogdefault` setting); `%` supplies the whole-file range. With `c`, answer `y` or `n`, `a` for all remaining, `q` to stop, or `l` to replace this match and stop. `u` can undo the resulting change. In replacement text `&` is the full match, `\1` is the first captured group, and `\r` inserts a line break; escape literal `&`, backslashes and the delimiter as needed. `:help sub-replace-special` has the details.

#### Pattern and replacement examples

| Command | Result |
|---|---|
| `:%s/^/  /` | Insert two spaces at the beginning of each line |
| `:%s/$/;/` | Append `;` to each line |
| `:%s/,/\r/g` | Turn commas into line breaks |
| `:%s/\v^([^,]+),\s*(.+)$/\2 \1/` | Change a line such as `Doe, Jane` to `Jane Doe` |

To wrap every `foo` or `bar` match in brackets, use:

```vim
:%s/\v(foo|bar)/[&]/g
```

`\v` enables very-magic pattern syntax, making grouping and alternation easier to read. The parenthesized parts in the `Doe, Jane` example are capture groups; `\2` and `\1` refer to them in replacement text. `&` in the replacement stands for the entire match. These are text substitutions, not language-aware renames. See `:help pattern` and `:help sub-replace-special` for escaping rules.

**Selected lines:** select with `V` and motions, press `:`; Neovim inserts `'<,'>`. Complete `s/old/new/g` and Enter. The range is whole lines, even after a characterwise selection. To require the whole literal `old` to lie inside a characterwise or blockwise Visual selection, use `:'<,'>s/\%Vol\%Vd/new/g`. The first `\%V` checks the start and the second checks the position of the last character. `\%V` is zero-width: putting it only at the start can allow the rest of a match to extend outside the selection. See `:help /\%V` for more complex patterns.

**Change matches one by one:** search `/old<CR>`, then `cgnnew<Esc>`. Press `.` to change the next match. To skip one, press `2n` and then `.`: `gn` can select a match under the cursor, so `n` alone followed by `.` changes the match you just navigated to. `gn` selects the next search match; `cgn` changes it. Search for `\<old\>` first if whole words matter. The initial forward search normally moves to the next match after the cursor, so check where it lands; it can skip a match under the starting cursor until the search wraps.

**Reuse the last search:** after `/old<CR>`, `:%s//new/g` uses the previous search pattern. `@:` repeats the last command-line command; check its range and effects before using it.

**Apply an Ex command to matching lines:** `:g/pattern/d` deletes matching lines; `:v/pattern/d` deletes nonmatching lines. `:g/^$/d` deletes empty lines, while `:g/^\s*$/d` also deletes whitespace-only lines. `:g/TODO/p` prints matching lines. These act on the current buffer; use undo if the result is wrong.

### Command-line editing and history

These keys work while entering a `:`, `/` or `?` command:

| Keys | Action |
|---|---|
| `<Up>` / `<Down>` | Navigate command or search history |
| `<C-b>` / `<C-e>` | Move to start / end of the command line |
| `<C-w>` / `<C-u>` | Delete previous word / text before cursor |
| `<C-r><C-w>` | Insert the word under the editor cursor |
| `<C-r>%` | Insert the current file name |
| `<C-f>` | Open the command-line window for editing history |
| `<Esc>` | Cancel the command line |

From Normal mode `q:` opens command history, and `q/` opens forward-search history. Edit a history entry in the command-line window and press Enter to execute it; `:q` closes the window.

## Registers, copy and clipboard

`y` copies; `p` puts after the cursor or below the line, and `P` puts before or above. The register's characterwise, linewise or blockwise type determines the shape of the put.

| Register | Purpose | Example |
|---|---|---|
| `"` | Unnamed: most recent yank or delete | `p` |
| `0` | Most recent yank that did not specify another register | `"0p` after a delete |
| `a`–`z` | Named; uppercase appends to corresponding lowercase | `"ayy`, `"ap`, `"Ayy` |
| `+` | System clipboard via available provider | `"+yy`, `"+p` |
| `_` | Black hole; discard without changing normal registers | `"_dd` |
| `/` | Last search pattern | `:reg /` |
| `1`–`9` | Numbered delete/change history, principally linewise or multiline | `"1p` |
| `-` | Most recent small delete | `"-p` |
| `%` / `.` / `:` | Current file name / last inserted text / last command line | `:reg %` |

`yy` yanks the current line and `3yy` yanks three lines. In installed stock Neovim 0.12.6, `Y` is mapped to `y$`, which yanks from the cursor to the end of the line; use `yy` when you want the whole line. Mappings can change this: inspect `:verbose nmap Y`.

`gp` and `gP` put text like `p` and `P`, but leave the cursor after the inserted text. `]p` and `[p` put text while adjusting indentation to the current line.

`:registers` shows contents. Visual `p` replaces the selection and updates the default register with the deleted text; Visual `P` replaces it without changing the default register. `"0p` is useful when a prior yank should survive ordinary deletes, though another yank without an explicit register replaces register `0`. Single-line characterwise deletes usually use the small-delete register `-`; some motions are exceptions. See `:help quote_number` and `:help quote_-`.

On Wayland, `wl-clipboard` provides `wl-copy` and `wl-paste`; provider selection also depends on the session and Neovim configuration. Use `:checkhealth` to investigate. `:set clipboard=unnamedplus` makes ordinary operations use the system clipboard; inspect the existing setting with `:set clipboard?` before changing it.

## Macros

A macro records **keystrokes in a register**, then replays them. Unlike `.`, it can repeat a sequence of moves and edits.

| Keys | Action |
|---|---|
| `qa` | Start recording into register `a`, replacing its contents |
| `q` | Stop recording in Normal mode |
| `@a` / `5@a` | Run register `a` once / five times |
| `@@` | Repeat the last executed register |
| `Q` | Repeat the last recorded macro in installed Neovim 0.12.6; see the version note below |
| `qA` | Append recording to register `a` |
| `:reg a` | Inspect recorded keystrokes |

> [!warning] `Q` changes in the supplied development source
> Installed 0.12.6 uses Normal `Q` to replay the last **recorded** macro; `@@` repeats the last **executed** register. The supplied 0.13.0-dev source instead assigns Normal `Q` to toggle a multiple cursor, and Visual `Q` places cursors on selected lines. Normal `Q`, `zqgn`, and the default `<C-l>` clearing behavior were tested in a clean isolated build of the supplied snapshot; development behavior can change before release. Use `@a` or `@@` for macro playback and consult `:help Q` and `:help multicursor` after updating.
>
> In that snapshot, `zq{motion}` places cursors at motion targets and `q=` toggles follow mode. Its default `<C-l>` mapping clears cursors, but your current configuration maps `<C-l>` to window navigation, so the default shortcut may not be available with your config. Check `:verbose nmap <C-l>` and the installed help before using it.

### Worked example: prefix consecutive lines

Given `alpha`, `beta`, `gamma` on separate lines, put the cursor on the first line and type:

```text
qa0i- <Esc>jq
```

Breakdown: `qa` starts recording; `0` moves to the beginning; `i- <Esc>` inserts the prefix; `j` moves to the next line; `q` stops. The first line is already changed and the cursor is on the second. `2@a` changes the next two lines. Use counts that match the remaining lines: the final `j` at the last line cannot move further, so excessive repetitions can edit that line again.

For irregular lines, start each iteration with an anchoring motion such as `0`, and prefer searches or text objects to fixed numbers of `l` presses. Test one `@a` before repeating. A failed search or unexpected line shape can interrupt or alter playback. Avoid yanking or deleting into the macro's own register while it runs.

### Apply a macro to a selected set of lines

Record a macro that edits **one line and does not itself move to the next line**, e.g. `qa0i- <Esc>q`. Recording already edits that first line. To prefix the remaining two lines of the example, press `jVj@a`: move to the second line, select it and the third with `Vj`, then run `@a`. If you also select the recording line, it will be edited a second time. Installed Neovim's linewise Visual `@` mapping runs the register on each selected line. Test first when the macro changes line counts or uses searches across lines. `:help v_@-default` explains the mapping.

## Surround text with quotes or brackets

Your `mini.surround` plugin uses these custom mappings when it is loaded. Put the cursor in the word or inside the existing delimiters:

| Keys | Action |
|---|---|
| `gsaiw)` | Surround the current word with parentheses: `word` → `(word)` |
| `gsaiw"` | Surround the current word with double quotes |
| `gsd)` | Delete the surrounding parentheses |
| `gsr)"` | Replace surrounding parentheses with double quotes |

Use `:help MiniSurround` for other surrounding identifiers and selection rules. `(` as an output identifier adds interior spaces; `)` does not. These mappings come from your plugin configuration, not stock Neovim.

## Files, buffers, windows and tabs

A **buffer** is an in-memory file or other text; a **window** displays a buffer; a **tab page** contains a layout of windows. Closing a window does not necessarily delete its buffer.

| Command or keys | Action |
|---|---|
| `:ls` | List buffers |
| `:b 3` / `:b name` | Switch by number / matching name |
| `:bn` / `:bp` | Next / previous listed buffer |
| `:bfirst` / `:blast` | First / last listed buffer |
| `<C-^>` | Toggle alternate buffer |
| `:bd` | Delete current buffer from buffer list |
| `:split file` / `:vsplit file` | Open horizontal / vertical split |
| `<C-w>h` / `<C-w>j` / `<C-w>k` / `<C-w>l` | Move to left / down / up / right window |
| `<C-w>c` / `<C-w>o` | Close current / keep only current window in the tab |
| `<C-w>=` | Equalize window sizes |
| `<C-w>_` / `<C-w>\|` | Maximize current window height / width |
| `:tabnew file` | Open a tab page |
| `:tabclose` / `:tabonly` | Close the current tab / close other tabs |
| `<C-w>w` / `<C-w>p` | Cycle to another window / return to the previous window |
| `<C-w>+` / `<C-w>-` | Increase / decrease window height |
| `<C-w>>` / `<C-w><` | Increase / decrease window width |
| `gt` / `gT` / `2gt` | Next / previous / numbered tab |
| `gf` | Edit file name under cursor, using `path` |
| `:pwd` / `:cd directory` | Show / change the working directory |

Your configuration also maps `<C-h>`, `<C-j>`, `<C-k>`, and `<C-l>` to window movement, `<leader>sv` / `<leader>sh` to vertical / horizontal split, and `<leader>e` to the file explorer. The `fzf-lua` mappings `<leader>ff`, `<leader>fb`, `<leader>fg`, `<leader>fh`, and `<leader>fk` find files, buffers, project text, help and mappings; `<leader>?` shows buffer-local mappings. `<leader>fr` finds recent files, `<leader>fw` searches for the word under the cursor in the workspace, and `<leader>/` searches lines in the current buffer. Normal `<Tab>` / `<S-Tab>` cycle buffers through Bufferline, and `<leader>bd` deletes a buffer while preserving splits. Flash maps Normal `s` to a labeled jump: type the target characters, then the displayed label. Normal `S` invokes its Tree-sitter selection. `<leader>sr` opens Grug-far for the current file and `<leader>sg` for the workspace. These depend on the configured plugins and their availability.

### Code navigation with a language server

Your configuration installs the navigation and code-action mappings below **buffer-locally** when an LSP client attaches. The diagnostic shortcuts are configured separately. Availability and results depend on the server, filetype and project; use `<leader>?` to inspect local mappings.

| Keys | Action |
|---|---|
| `gd` / `gD` | Go to definition / declaration |
| `gi` / `<leader>D` | Go to implementation / type definition |
| `K` | Show hover information |
| `grr` | Find references |
| `<leader>rn` | Rename a symbol using the server |
| `<leader>ca` | Show code actions in Normal or Visual mode |
| `<leader>ck` | Show signature help |
| `<leader>cd` | Show diagnostic details at the cursor |
| `<leader>fd` | Search workspace diagnostics with fzf-lua |
| `<leader>cf` | Format with Conform, using LSP fallback if configured |

`<C-o>` can return from a definition jump. A server rename is symbol-aware and may affect multiple files; inspect the resulting edits.

## Project search and quickfix

The quickfix list is a navigable set of results, shared across windows. The location list is similar but belongs to a window. Inspect the active external grep configuration with `:set grepprg? grepformat?`; installed clean Neovim currently reports `rg --vimgrep -uu` and `%f:%l:%c:%m`. `-uu` includes hidden and ignored files. Your current configuration overrides this with `rg --vimgrep`, which normally respects ignore rules and skips hidden files. Inspect the active setting when the file set matters.

```vim
:grep pattern .
:copen
:cnext
:cprev
:cc 3
:cclose
```

`:grep` invokes the configured external program, whose regex and argument rules apply; it is not the same regex language as `:s`. `:vimgrep /pattern/gj **/*.lua` uses Neovim patterns and places results in quickfix; the glob and directory determine the file set. `:lopen`, `:lnext` and `:lprev` operate on the location list. `:make` can populate quickfix from a build using `makeprg` and `errorformat`.

Additional quickfix commands: `:clist` lists entries, `:cfirst` / `:clast` jump to the first / last, and `:colder` / `:cnewer` switch between older / newer quickfix lists. A quickfix window lets you select an entry and press Enter.

## Batch editing across files

First inspect the files and patterns; `<leader>sg` opens your project search-and-replace interface. The built-in argument list offers a scriptable alternative. In a directory containing the intended Markdown files:

```vim
:args *.md
:args
:argdo %s/old/new/ge | update
```

`:args *.md` replaces the argument list with matching files, and `:args` shows it. The last command runs the substitute in each argument buffer and `:update` writes buffers that are modified. `e` allows files with no match to be processed without stopping on that error. This example **writes the files** and also writes other existing changes in those buffers; inspect the list and contents first. It is a literal example only for patterns without regex metacharacters; use Vim regex escaping and a suitable scope for your text. `:argdo` can stop on other errors, leaving later files untouched.

| Command | Runs once per |
|---|---|
| `:argdo {command}` | File in the argument list |
| `:bufdo {command}` | Listed buffer |
| `:cdo {command}` | Valid quickfix entry; a file may occur more than once |
| `:cfdo {command}` | Each consecutive group of quickfix entries from the same file |
| `:windo {command}` / `:tabdo {command}` | Window in the current tab / tab page |

A focused quickfix workflow can use `:grep pattern .`, inspect `:copen`, then use `:cfdo` with an appropriate command and `:update`. `:cfdo` advances to the next file group, not necessarily each distinct file exactly once: an unsorted list ordered `a`, `b`, `a` visits `a` twice. Inspect the list and choose commands whose repeated execution has the intended effect. For a one-line change per matching entry, `:cdo` acts on entries instead. Quickfix selection and substitutions are separate: `%s` acts on each entire file, not only matching result lines. `:help :argdo`, `:help :cdo` and `:help :cfdo` cover error and buffer behavior.

## Diff, shell, terminal and help

`nvim -d a b` opens a comparison. `]c` / `[c` move between changes; `do` obtains a change from another diff buffer and `dp` puts it into another; `:diffupdate` refreshes. Review and write the intended buffer after merging.

`:!command` runs a shell command; `:read !command` inserts its output below the current line. `:%!sort` replaces the entire buffer with the output of `sort`; a Visual selection followed by `:!sort` filters selected lines. Undo with `u` if needed. `:terminal` opens a terminal buffer; `<C-\><C-n>` enters Terminal-Normal mode so you can navigate or leave the window; `i` returns to the terminal job.

`:help` is the primary reference for the installed version: `:help motion.txt`, `:help text-objects`, `:help :substitute`, `:help pattern`, `:help recording`, `:help registers`, `:help windows`, `:help quickfix`, `:help lua-guide`. Use `<C-]>` on a help tag to follow it and `<C-o>` to go back. `:Tutor` starts the interactive tutorial.

## Configuration and introspection

Neovim reads `init.lua` or `init.vim` from its config directory. On Linux it normally follows XDG paths; environment variables and `NVIM_APPNAME` can change the actual paths. Inspect them instead of assuming:

```vim
:lua print(vim.fn.stdpath('config'))
:lua print(vim.fn.stdpath('data'))
:lua print(vim.fn.stdpath('state'))
:lua print(vim.fn.stdpath('cache'))
```

Options can be global, buffer-local, window-local or global-local. `:set number?` inspects an option; `:setlocal number` affects the current local scope; `:verbose set number?` shows where it was last set. `:scriptnames` lists sourced scripts; `:messages` shows messages; `:checkhealth` runs diagnostics. Compare with `nvim --clean` when investigating configuration-dependent behavior.

A minimal Lua example, for understanding rather than an instruction to overwrite your existing configuration:

```lua
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.undofile = true
vim.keymap.set('n', '<leader>w', '<cmd>write<cr>', { desc = 'Save' })
```

`vim.opt` manipulates options; `vim.bo` and `vim.wo` access buffer- and window-local options. `vim.keymap.set` is nonrecursive by default. Inspect a mapping with `:verbose nmap <leader>w`; use `:help vim.keymap.set()` for modes and options. Set `vim.g.mapleader` **before** defining leader mappings.

Your configuration currently enables `undofile`, `clipboard=unnamedplus`, line numbers and relative numbers, and case-insensitive search with `smartcase`; it disables swap files. Inspect the live options because filetype plugins and later configuration can change them.

Filetype plugins, autocommands, language servers and plugins can set options or mappings later. `:set filetype?`, `:verbose setlocal shiftwidth?`, `:verbose nmap K` and `:LspInfo` where available help identify behavior. Your configuration uses lazy.nvim; plugin keymaps may load lazily, and LSP mappings can be buffer-local. `:help packages` documents the separate built-in `pack/*/start` and `pack/*/opt` mechanism.

`undofile` preserves undo history across sessions when Neovim can write its undo file. ShaDa stores other state such as selected registers, marks and histories, subject to its settings. Swap files, when enabled, support recovery after an interrupted session; `:recover` offers recovery. Your current configuration sets `noswapfile`, so it does not create swap files for newly opened buffers; check `:setlocal swapfile?` for the current buffer. Persistent undo and swap recovery serve different purposes. `:mksession Session.vim` saves a workspace layout; `nvim -S Session.vim` restores it. See `:help undo-persistence`, `:help shada`, `:help recovery` and `:help session` for scope and options.

### Options and file inspection

| Command | Action |
|---|---|
| `:setlocal number!` | Toggle line numbers in the current window |
| `:setlocal wrap!` | Toggle display wrapping in the current window |
| `:setlocal spell!` | Toggle spell checking in the current window |
| `:setlocal expandtab? shiftwidth? tabstop?` | Inspect indentation settings |
| `:setlocal filetype?` | Show detected filetype |
| `:echo expand('%:p')` | Show the current file's absolute path |
| `:verbose nmap {keys}` / `:verbose imap {keys}` | Inspect a Normal / Insert mapping and where it was set |
| `:verbose autocmd FileType` | List FileType autocommands and their sources |

Options set interactively last for the current session unless saved in configuration. A later filetype plugin or autocommand may override local settings. `:setlocal` shows changed local options; `:set all` lists options.

### Focused extras

- **Folds:** `za` toggles, `zo` opens, `zc` closes, `zR` opens all and `zM` closes all. `zf{motion}` creates a fold when the fold method permits manual folds; `zd` deletes a manual fold. Behavior depends on `foldmethod`.
- **Spelling:** `:setlocal spell spelllang=en_us`; `]s` / `[s` move between misspellings, `z=` suggests corrections, `zg` adds a word; `zug` undoes that addition. Your `<leader>z` moves to the next misspelling and opens suggestions.
- **Whitespace and file format:** `:set list` shows configured whitespace markers; `:set fileformat? fileencoding?` inspects line endings and encoding. `:setlocal fileformat=unix` chooses LF when written.
- **Command-line history:** use Up/Down while entering `:`, `/` or `?`; `q:` opens the command-line window for editing history. `<C-f>` also opens it from the command line.
- **Undo tree:** your `<leader>u` toggles the Undotree plugin, which can display branching undo history. It depends on the plugin being available.

> [!tip] Practice in a scratch buffer
> Run `nvim --clean`, enter sample lines, and try `ciw`, `cgn`, block insertion and `qa…q`. Use `u` to undo and `:q!` to discard the scratch buffer. This isolates built-in behavior from your mappings.
