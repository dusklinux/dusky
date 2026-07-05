# Factorio 2.0.77 — Essential Console Commands & Cheats (Verified)

> **Last verified against the official Factorio Wiki (2026 update), valid for Factorio 2.0.77.** :contentReference[oaicite:0]{index=0}
>
> **Open the console:** `~`, `` ` ``, or `/`
>
> ## Important
>
> - Any command beginning with **`/c`**, **`/command`**, **`/sc`**, or **`/silent-command`** permanently **disables achievements for the save**.
> - Normal commands like `/help`, `/players`, `/version`, etc. **do not** disable achievements. :contentReference[oaicite:1]{index=1}

---

# 1. Unlock Every Technology

```lua
/c game.player.force.research_all_technologies()
```

Unlocks every technology instantly.

---

# 2. Complete Only the Current Research

```lua
/c game.player.force.current_research.researched=true
```

Finishes the currently selected research.

---

# 3. Unresearch Every Technology

```lua
/c
for _, tech in pairs(game.player.force.technologies) do
    tech.researched = false
end
```

Useful for resetting a testing world.

---

# 4. Reset Your Entire Force

```lua
/c game.player.force.reset()
```

Resets:

- Technologies
- Bonuses
- Production statistics
- Kill statistics
- Charted map

Use with caution.

---

# 5. Enable Cheat Mode

```text
/cheat
```

Researches every technology and enables cheat mode.

Additional options:

```text
/cheat all
```

Also grants extra starter items.

```text
/cheat off
```

Turns cheat mode off (research remains unlocked). :contentReference[oaicite:2]{index=2}

---

# 6. Spawn Items

Example:

```lua
/c game.player.insert{name="iron-plate", count=1000}
```

More examples:

```lua
/c game.player.insert{name="steel-plate", count=500}

/c game.player.insert{name="processing-unit", count=500}

/c game.player.insert{name="rocket-fuel", count=100}
```

Replace the item name with any valid prototype.

---

# 7. Kill Every Enemy Unit

```lua
/c game.forces.enemy.kill_all_units()
```

Kills all currently spawned enemy units.

**Does not destroy:**

- Biter spawners
- Worms

---

# 8. Destroy Every Enemy Structure

```lua
/c
for _, surface in pairs(game.surfaces) do
    for _, entity in pairs(surface.find_entities_filtered{force="enemy"}) do
        entity.destroy()
    end
end
```

Removes:

- Biters
- Spitters
- Worms
- Spawners

---

# 9. Reveal the Entire Map

```lua
/c game.player.force.chart_all()
```

Charts every generated chunk.

---

# 10. Permanent Daylight

Enable:

```lua
/c game.player.surface.always_day=true
```

Disable:

```lua
/c game.player.surface.always_day=false
```

---

# 11. Change Game Speed

Half speed:

```lua
/c game.speed=0.5
```

Normal:

```lua
/c game.speed=1
```

Double:

```lua
/c game.speed=2
```

Very fast:

```lua
/c game.speed=5
```

---

# 12. Become Invincible

```lua
/c game.player.character.destructible=false
```

Return to normal:

```lua
/c game.player.character.destructible=true
```

---

# 13. Heal Yourself

```lua
/c game.player.character.health=game.player.character.prototype.max_health
```

Restores full health instantly.

---

# 14. Increase Mining Speed

```lua
/c game.player.force.manual_mining_speed_modifier=100
```

Reset:

```lua
/c game.player.force.manual_mining_speed_modifier=0
```

---

# 15. Increase Crafting Speed

```lua
/c game.player.force.manual_crafting_speed_modifier=100
```

Reset:

```lua
/c game.player.force.manual_crafting_speed_modifier=0
```

---

# 16. Increase Running Speed

```lua
/c game.player.character_running_speed_modifier=2
```

Reset:

```lua
/c game.player.character_running_speed_modifier=0
```

---

# 17. Zoom Out Beyond the Normal Limit

```lua
/c game.player.zoom=0.1
```

Useful for screenshots and megabase planning.

> Extremely small values can hurt performance. :contentReference[oaicite:3]{index=3}

---

# 18. Enter the Map Editor

```text
/editor
```

Run it again to return to normal gameplay.

The editor allows:

- Terrain editing
- Resource editing
- Entity placement
- Instant technology changes
- Blueprint testing

---

# 19. Measure a Lua Command's Execution Time

Instead of:

```lua
/c
```

Use:

```text
/mc <lua command>
```

Example:

```text
/mc game.player.force.research_all_technologies()
```

Useful when profiling expensive scripts. :contentReference[oaicite:4]{index=4}

---

# 20. Run Lua Without Printing It to Chat

```text
/sc <lua command>
```

Example:

```text
/sc game.speed=3
```

Executes silently.

---

# Useful Built-in Commands

## Show evolution factor

```text
/evolution
```

---

## Show map seed

```text
/seed
```

---

## Show game time

```text
/time
```

---

## Show game version

```text
/version
```

---

## List players

```text
/players
```

---

## Show all commands

```text
/help
```

---

# Most Common Commands

| Purpose | Command |
|----------|---------|
| Unlock all research | `/c game.player.force.research_all_technologies()` |
| Complete current research | `/c game.player.force.current_research.researched=true` |
| Spawn items | `/c game.player.insert{name="iron-plate", count=1000}` |
| Kill enemies | `/c game.forces.enemy.kill_all_units()` |
| Remove enemy bases | Enemy removal script above |
| Reveal map | `/c game.player.force.chart_all()` |
| Always day | `/c game.player.surface.always_day=true` |
| Change game speed | `/c game.speed=2` |
| Invincibility | `/c game.player.character.destructible=false` |
| Heal player | `/c game.player.character.health=game.player.character.prototype.max_health` |
| Faster mining | `/c game.player.force.manual_mining_speed_modifier=100` |
| Faster crafting | `/c game.player.force.manual_crafting_speed_modifier=100` |
| Faster running | `/c game.player.character_running_speed_modifier=2` |
| Zoom out | `/c game.player.zoom=0.1` |
| Map editor | `/editor` |

---

# Notes

- `/cheat` is an official command in Factorio 2.x. It researches all technologies, enables cheat mode, and accepts the options `all`, `<planet-name>`, `<platform-name>`, and `off`. :contentReference[oaicite:5]{index=5}
- `/editor` is generally the best tool for designing, blueprint testing, and debugging factories.
- The commands above are based on the official Factorio console documentation and remain valid for **Factorio 2.0.77**. :contentReference[oaicite:6]{index=6}
