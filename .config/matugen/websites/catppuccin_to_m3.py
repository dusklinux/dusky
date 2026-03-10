#!/usr/bin/env python3
"""Convert Catppuccin LESS-style CSS to Material Design 3 CSS variables."""

import re
import sys

CATPPUCCIN_TO_M3 = {
    "@mantle": "var(--surface)",
    "@base": "var(--surface_container_lowest)",
    "@surface0": "var(--surface_container_low)",
    "@surface1": "var(--surface_container)",
    "@surface2": "var(--surface_container_high)",
    "@surface3": "var(--surface_container_highest)",
    "@text": "var(--on_surface)",
    "@subtext0": "var(--on_surface_variant)",
    "@subtext1": "var(--on_surface)",
    "@overlay0": "var(--outline)",
    "@overlay1": "var(--outline_variant)",
    "@overlay2": "var(--outline_variant)",
    "@accent": "var(--primary)",
    "@lavender": "var(--primary_container)",
    "@blue": "var(--primary)",
    "@sapphire": "var(--primary_container)",
    "@sky": "var(--primary_fixed)",
    "@teal": "var(--tertiary_container)",
    "@green": "var(--tertiary)",
    "@yellow": "var(--secondary)",
    "@peach": "var(--secondary_container)",
    "@maroon": "var(--error_container)",
    "@red": "var(--error)",
    "@rosewater": "var(--surface_container_high)",
    "@crust": "var(--surface)",
    "@mauve": "var(--primary)",
    "@pink": "var(--secondary)",
    "@flamingo": "var(--secondary_container)",
    "@gray": "var(--outline_variant)",
}

HIGHLIGHT_COLORS = {
    "highlight1": "var(--color-classification-blunder)",
    "highlight2": "var(--color-classification-best)",
    "highlight3": "var(--color-classification-inaccuracy)",
    "highlight4": "var(--color-classification-great)",
}


def convert_catppuccin_to_m3(content: str) -> str:
    lines = content.split("\n")
    result = []
    in_catppuccin_block = False
    in_body = False
    in_root = False
    brace_count = 0
    current_indent = ""

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if "@-moz-document" in stripped:
            result.append(line)
            i += 1
            continue

        if stripped.startswith("@-moz-document"):
            result.append(line)
            i += 1
            continue

        if stripped.startswith("@import"):
            i += 1
            continue

        if ".light-mode" in stripped or ".dark-mode" in stripped:
            i += 1
            continue

        if "#catppuccin(" in stripped:
            i += 1
            continue

        if "#lib.palette()" in stripped or "#lib.defaults()" in stripped:
            i += 1
            continue

        if "@highlight" in stripped and ":" in stripped:
            i += 1
            continue

        if (
            stripped.startswith("@")
            and "=" in stripped
            and not stripped.startswith("@import")
        ):
            i += 1
            continue

        if stripped == "body {" or stripped.startswith("body {"):
            in_body = True
            brace_count = count_braces(stripped)
            result.append(":root {")
            result.append("}")
            result.append("")
            result.append(line)
            brace_count += count_braces(stripped)
            i += 1
            continue

        if (
            stripped.startswith("& when (@styleBoardAndPieces")
            or "& when (" in stripped
        ):
            i += 1
            continue

        if stripped.startswith("@white-piece-bg:") or stripped.startswith(
            "@black-piece-bg:"
        ):
            i += 1
            continue

        if stripped.startswith("@light-cell:") or stripped.startswith("@dark-cell:"):
            i += 1
            continue

        if (
            stripped.startswith("@board:")
            or stripped.startswith("@bishop:")
            or stripped.startswith("@king:")
            or stripped.startswith("@knight:")
            or stripped.startswith("@rook:")
            or stripped.startswith("@pawn:")
            or stripped.startswith("@queen:")
        ):
            i += 1
            continue

        if "#piece(" in stripped:
            i += 1
            continue

        if "@raw:" in stripped or "@svg:" in stripped or "@result:" in stripped:
            i += 1
            continue

        if stripped.startswith("#piece"):
            i += 1
            continue

        if "--theme-piece-set-" in stripped:
            i += 1
            continue

        if ".piece {" in stripped or ".piece." in stripped:
            i += 1
            continue

        if stripped.startswith(".light-mode") or stripped.startswith(".dark-mode"):
            i += 1
            continue

        if stripped.startswith("--theme-board-style-"):
            i += 1
            continue

        if not line.strip():
            result.append(line)
            i += 1
            continue

        converted_line = convert_line(line, in_body)

        if converted_line:
            result.append(converted_line)

        i += 1

    return "\n".join(result)


def count_braces(s: str) -> int:
    return s.count("{") - s.count("}")


def convert_line(line: str, in_body: bool) -> str:
    converted = line

    for catppuccin, m3 in CATPPUCCIN_TO_M3.items():
        converted = converted.replace(catppuccin, m3)

    converted = converted.replace("if(@flavor = latte, ", "")
    converted = re.sub(r", @\w+\);?\s*$", "", converted)

    converted = re.sub(
        r"darken\((@[\w]+),\s*\d+%\)", r"var(--primary_fixed_dim)", converted
    )
    converted = re.sub(
        r"lighten\((@[\w]+),\s*\d+%\)", r"var(--primary_fixed)", converted
    )

    converted = re.sub(r"fade\((@[\w]+),\s*\d+%\)", r"\1", converted)

    return converted


def main():
    if len(sys.argv) < 2:
        print("Usage: catppuccin_to_m3.py <input.css> [output.css]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    with open(input_file, "r") as f:
        content = f.read()

    converted = convert_catppuccin_to_m3(content)

    if output_file:
        with open(output_file, "w") as f:
            f.write(converted)
        print(f"Converted: {input_file} -> {output_file}")
    else:
        print(converted)


if __name__ == "__main__":
    main()
