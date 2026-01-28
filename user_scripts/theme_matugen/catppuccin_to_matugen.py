#!/usr/bin/env python3
"""
Convert Catppuccin palette colors to matugen theme template variables.

This script takes Catppuccin colors and converts them to matugen template format
for Material Design 3 color variables.
"""

import json
import re
import os
from typing import Dict, Optional

# Catppuccin palette colors for all flavors
CATPPUCCIN_PALETTES = {
    "mocha": {
        "rosewater": "#f5e0dc",
        "flamingo": "#f2cdcd",
        "pink": "#f5c2e7",
        "mauve": "#cba6f7",
        "red": "#f38ba8",
        "maroon": "#eba0ac",
        "peach": "#fab387",
        "yellow": "#f9e2af",
        "green": "#a6e3a1",
        "teal": "#94e2d5",
        "sky": "#89dceb",
        "sapphire": "#74c7ec",
        "blue": "#89b4fa",
        "lavender": "#b4befe",
        "text": "#cdd6f4",
        "subtext1": "#bac2de",
        "subtext0": "#a6adc8",
        "overlay2": "#9399b2",
        "overlay1": "#7f849c",
        "overlay0": "#6c7086",
        "surface2": "#585b70",
        "surface1": "#45475a",
        "surface0": "#313244",
        "base": "#1e1e2e",
        "mantle": "#181825",
        "crust": "#11111b",
    },
    "macchiato": {
        "rosewater": "#f4dbd6",
        "flamingo": "#f0c6c6",
        "pink": "#f5bde6",
        "mauve": "#c6a0f6",
        "red": "#ed8796",
        "maroon": "#ee99a0",
        "peach": "#f5a97f",
        "yellow": "#eed49f",
        "green": "#a6da95",
        "teal": "#8bd5ca",
        "sky": "#91d7e3",
        "sapphire": "#7dc4e4",
        "blue": "#8aadf4",
        "lavender": "#b7bdf8",
        "text": "#cad3f5",
        "subtext1": "#b8c0e0",
        "subtext0": "#a5adcb",
        "overlay2": "#939ab7",
        "overlay1": "#8087a2",
        "overlay0": "#6e7381",
        "surface2": "#5b6078",
        "surface1": "#494d64",
        "surface0": "#363a4f",
        "base": "#24273a",
        "mantle": "#1e2030",
        "crust": "#181926",
    },
    "frappe": {
        "rosewater": "#f2d5cf",
        "flamingo": "#eebebe",
        "pink": "#f4b8e4",
        "mauve": "#ca9ee6",
        "red": "#e78284",
        "maroon": "#ea999c",
        "peach": "#ef9f76",
        "yellow": "#e5c890",
        "green": "#a6d189",
        "teal": "#81c8be",
        "sky": "#99d1db",
        "sapphire": "#85c1dc",
        "blue": "#8caaee",
        "lavender": "#babbf1",
        "text": "#c6d0f5",
        "subtext1": "#b5bfe2",
        "subtext0": "#a5adce",
        "overlay2": "#949cbb",
        "overlay1": "#838ba7",
        "overlay0": "#737994",
        "surface2": "#626880",
        "surface1": "#51576d",
        "surface0": "#414559",
        "base": "#303446",
        "mantle": "#292c3c",
        "crust": "#232634",
    },
    "latte": {
        "rosewater": "#dc8a78",
        "flamingo": "#dd7878",
        "pink": "#ea76cb",
        "mauve": "#8839ef",
        "red": "#d20f39",
        "maroon": "#e64553",
        "peach": "#fe640b",
        "yellow": "#df8e1d",
        "green": "#40a02b",
        "teal": "#179299",
        "sky": "#04a5e5",
        "sapphire": "#209fb5",
        "blue": "#1e66f5",
        "lavender": "#7287fd",
        "text": "#4c4f69",
        "subtext1": "#5c5f77",
        "subtext0": "#6c6f85",
        "overlay2": "#7c7f93",
        "overlay1": "#8c8fa1",
        "overlay0": "#9ca0b0",
        "surface2": "#acb0be",
        "surface1": "#bcc0cc",
        "surface0": "#ccd0da",
        "base": "#eff1f5",
        "mantle": "#e6e9ef",
        "crust": "#dce0e8",
    },
}

# Material Design 3 color mapping for catppuccin colors
# Based on user's custom configuration
MATERIAL_COLOR_MAPPING = {
    # Primary colors
    "blue": "primary",
    "mauve": "primary",
    "lavender": "secondary",
    "pink": "tertiary",
    # Error colors
    "red": "error",
    "maroon": "on_error",
    # Surface colors
    "base": "surface",
    "mantle": "surface_container_low",
    "crust": "inverse_surface",
    "surface0": "surface_container_high",
    "surface1": "primary",
    "surface2": "outline",
    "flamingo": "surface_dim",
    "rosewater": "surface_bright",
    # Text colors
    "text": "on_surface",
    "subtext1": "on_surface_variant",
    "subtext0": "on_background",
    "overlay0": "on_background",
    "overlay1": "on_surface_variant",
    "overlay2": "inverse_primary",
    # Container colors
    "peach": "on_primary_container",
    "yellow": "on_secondary_container",
    "green": "on_tertiary_container",
    "teal": "primary_container",
    "sky": "secondary_container",
    "sapphire": "tertiary_container",
}


def convert_to_matugen_var(
    color_name: str, scheme: str = "default", format_type: str = "hex"
) -> str:
    """
    Convert a Catppuccin color name to matugen template variable.

    Args:
        color_name: The Catppuccin color name
        scheme: Color scheme (light, dark, default)
        format_type: Output format (hex, rgb, hsl)

    Returns:
        matugen template variable string
    """
    material_color = MATERIAL_COLOR_MAPPING.get(color_name, "surface")
    return f"{{{{colors.{material_color}.{scheme}.{format_type}}}}}"


def generate_css_variables(flavor: str, scheme: str = "default") -> str:
    """
    Generate CSS variables for a specific Catppuccin flavor.

    Args:
        flavor: Catppuccin flavor (mocha, macchiato, frappe, latte)
        scheme: Color scheme for matugen (light, dark, default)

    Returns:
        CSS variables string
    """
    if flavor not in CATPPUCCIN_PALETTES:
        raise ValueError(f"Unknown flavor: {flavor}")

    palette = CATPPUCCIN_PALETTES[flavor]
    css_vars = []

    css_vars.append(f"/* Catppuccin {flavor.capitalize()} theme for matugen */")
    css_vars.append(f":root {{")

    for color_name, hex_color in palette.items():
        matugen_var = convert_to_matugen_var(color_name, scheme, "hex")
        css_vars.append(
            f"  --catppuccin-{color_name}: {matugen_var}; /* {hex_color} */"
        )

    css_vars.append("}")
    return "\n".join(css_vars)


def generate_template_file(
    flavor: str, output_path: str, scheme: str = "default"
) -> None:
    """
    Generate a matugen template file for a specific Catppuccin flavor.

    Args:
        flavor: Catppuccin flavor
        output_path: Output file path
        scheme: Color scheme
    """
    css_content = generate_css_variables(flavor, scheme)

    template_content = f"""/* Catppuccin {flavor.capitalize()} matugen template */
/* Generated by catppuccin-to-matugen converter */

{css_content}

/* Example usage in your application */
.example-application {{
    background: var(--catppuccin-base);
    color: var(--catppuccin-text);
    border-color: var(--catppuccin-surface0);
    accent-color: var(--catppuccin-blue);
}}

.button {{
    background: var(--catppuccin-blue);
    color: var(--catppuccin-base);
}}

.button-secondary {{
    background: var(--catppuccin-surface1);
    color: var(--catppuccin-text);
}}
"""

    with open(output_path, "w") as f:
        f.write(template_content)

    print(f"Generated matugen template: {output_path}")


def generate_config_toml(
    flavors: list, output_path: str = "matugen-catppuccin.toml"
) -> None:
    """
    Generate a matugen configuration TOML file for Catppuccin themes.

    Args:
        flavors: List of Catppuccin flavors
        output_path: Output TOML file path
    """
    config_lines = [
        "# Catppuccin themes for matugen",
        "# Generated by catppuccin-to-matugen converter",
        "",
        "[config]",
        "# Set your preferred flavors here",
        "",
    ]

    for flavor in flavors:
        if flavor not in CATPPUCCIN_PALETTES:
            continue

        scheme = "dark" if flavor in ["mocha", "macchiato", "frappe"] else "light"

        config_lines.extend(
            [
                f"[templates.{flavor}]",
                f'input_path = "./templates/{flavor}.css"',
                f'output_path = "./output/{flavor}.css"',
                f'mode = "{scheme.capitalize()}"',
                "",
            ]
        )

    with open(output_path, "w") as f:
        f.write("\n".join(config_lines))

    print(f"Generated matugen config: {output_path}")


def load_conversion_template(template_file: str) -> Optional[str]:
    """
    Load conversion template from file.

    Args:
        template_file: Path to template file

    Returns:
        Template content or None if file not found
    """
    try:
        with open(template_file, "r") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"Error reading template file: {e}")
        return None


def save_conversion_template(template_file: str, template_content: str) -> bool:
    """
    Save conversion template to file.

    Args:
        template_file: Path to save template
        template_content: Template content

    Returns:
        True if successful, False otherwise
    """
    try:
        if os.path.dirname(template_file):
            os.makedirs(os.path.dirname(template_file), exist_ok=True)
        with open(template_file, "w") as f:
            f.write(template_content)
        return True
    except Exception as e:
        print(f"Error saving template file: {e}")
        return False


def get_default_conversion_template() -> str:
    """
    Get the default conversion template.

    Returns:
        Default template content
    """
    return """# Catppuccin to Matugen Conversion Template
# This template defines how Catppuccin colors are mapped to matugen variables
# and how the output is formatted.

# Color mappings: catppuccin_color -> matugen_variable
[mappings]
# Primary colors
blue = "primary"
mauve = "primary"
lavender = "secondary"
pink = "tertiary"

# Error colors
red = "error"
maroon = "on_error"

# Surface colors
base = "surface"
mantle = "surface_container_low"
crust = "inverse_surface"
surface0 = "surface_container_high"
surface1 = "primary"
surface2 = "outline"
flamingo = "surface_dim"
rosewater = "surface_bright"

# Text colors
text = "on_surface"
subtext1 = "on_surface_variant"
subtext0 = "on_background"
overlay0 = "on_background"
overlay1 = "on_surface_variant"
overlay2 = "inverse_primary"

# Container colors
peach = "on_primary_container"
yellow = "on_secondary_container"
green = "on_tertiary_container"
teal = "primary_container"
sky = "secondary_container"
sapphire = "tertiary_container"

# Output formatting
[output]
# Header comment template
header = \"\"\"/* Converted from Catppuccin {flavor} to matugen template */
/* Generated by catppuccin-to-matugen converter */

\"\"\"

# File extension for output files
extension = "_matugen.css"

# Variable format template
# Available placeholders: {color}, {scheme}, {format}
variable_template = "{{colors.{color}.{scheme}.{format}}}"

# Default scheme and format
default_scheme = "default"
default_format = "hex"
"""


def parse_conversion_template(template_content: str) -> tuple:
    """
    Parse conversion template content.

    Args:
        template_content: Template file content

    Returns:
        Tuple of (color_mappings_dict, output_config_dict)
    """
    try:
        import toml

        config = toml.loads(template_content)
        mappings = config.get("mappings", {})
        output_config = config.get("output", {})
        return mappings, output_config
    except ImportError:
        print("Warning: toml library not found. Using simple TOML parser.")
        return parse_simple_toml(template_content)
    except Exception as e:
        print(f"Error parsing template: {e}")
        return parse_simple_toml(template_content)


def parse_simple_toml(template_content: str) -> tuple:
    """
    Simple TOML parser for basic template files.

    Args:
        template_content: Template file content

    Returns:
        Tuple of (color_mappings_dict, output_config_dict)
    """
    mappings = {}
    output_config = {}
    current_section = None
    in_multiline = False
    multiline_key = None
    multiline_content = []

    lines = template_content.split("\n")

    for line in lines:
        original_line = line
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        # Parse sections
        if line.startswith("[") and line.endswith("]") and not in_multiline:
            current_section = line[1:-1]
            continue

        # Handle multi-line strings
        if '"""' in line:
            if not in_multiline:
                # Start of multi-line string
                parts = line.split('"""', 1)
                if "=" in parts[0]:
                    key = parts[0].split("=")[0].strip()
                    multiline_key = key
                    multiline_content = []
                    if len(parts) > 1:
                        multiline_content.append(parts[1])
                    in_multiline = True
                continue
            else:
                # End of multi-line string
                parts = line.split('"""', 1)
                multiline_content.append(parts[0])
                value = "\n".join(multiline_content).strip()

                if current_section == "output":
                    output_config[multiline_key] = value

                in_multiline = False
                multiline_key = None
                multiline_content = []
                continue

        if in_multiline:
            multiline_content.append(line)
            continue

        # Parse regular key-value pairs
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            # Handle quoted strings
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            if current_section == "mappings":
                mappings[key] = value
            elif current_section == "output":
                output_config[key] = value

    return mappings, output_config


def detect_flavor_from_colors(content: str) -> Optional[str]:
    """
    Detect Catppuccin flavor by analyzing hex colors in the content.

    Args:
        content: File content to analyze

    Returns:
        Detected flavor name or None if not found
    """
    # Find all hex colors in the content
    hex_colors = re.findall(r"#[0-9a-fA-F]{6}", content)

    if not hex_colors:
        return None

    # Count matches for each flavor
    flavor_matches = {}
    for flavor, palette in CATPPUCCIN_PALETTES.items():
        matches = sum(
            1
            for color in hex_colors
            if color.upper() in [c.upper() for c in palette.values()]
        )
        flavor_matches[flavor] = matches

    # Return flavor with most matches (minimum threshold of 3 colors)
    best_flavor = max(flavor_matches.items(), key=lambda x: x[1])[0]
    if flavor_matches[best_flavor] >= 3:
        return best_flavor

    return None


def convert_theme_file(
    input_file: str,
    output_file: str,
    flavor: Optional[str] = None,
    scheme: str = "default",
    template_file: Optional[str] = None,
) -> None:
    """
    Convert an existing theme file with Catppuccin colors to use matugen variables.

    Args:
        input_file: Path to input theme file
        output_file: Path to output theme file
        flavor: Catppuccin flavor (auto-detect if None)
        scheme: Color scheme for matugen
        template_file: Custom conversion template file
    """
    try:
        with open(input_file, "r") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found")
        return
    except Exception as e:
        print(f"Error reading input file: {e}")
        return

    # Load conversion template
    color_mappings = MATERIAL_COLOR_MAPPING  # Default fallback
    output_config = {}

    if template_file:
        template_content = load_conversion_template(template_file)
        if template_content:
            mappings, config = parse_conversion_template(template_content)
            if mappings and config:
                color_mappings = mappings
                output_config = config
                print(f"Using custom template: {template_file}")
            else:
                print("Invalid template, using default mappings")
        else:
            print(f"Template file not found: {template_file}, using default mappings")
    else:
        # Try to load user template
        user_template = os.path.expanduser(
            "~/.config/catppuccin-to-matugen/template.toml"
        )
        template_content = load_conversion_template(user_template)
        if template_content:
            mappings, config = parse_conversion_template(template_content)
            if mappings and config:
                color_mappings = mappings
                output_config = config
                print(
                    "Using user template from ~/.config/catppuccin-to-matugen/template.toml"
                )

    # Auto-detect flavor if not specified
    if flavor is None:
        flavor = detect_flavor_from_colors(content)
        if flavor is None:
            print("Error: Could not detect Catppuccin flavor. Please specify --flavor")
            return
        print(f"Detected Catppuccin flavor: {flavor}")

    if flavor not in CATPPUCCIN_PALETTES:
        print(f"Error: Unknown flavor '{flavor}'")
        return

    palette = CATPPUCCIN_PALETTES[flavor]
    replacements_made = []

    # Get output configuration
    default_scheme = output_config.get("default_scheme", scheme)
    default_format = output_config.get("default_format", "hex")
    var_template = output_config.get(
        "variable_template", "{{colors.{color}.{scheme}.{format}}}"
    )

    # Replace each Catppuccin hex color with matugen variable
    for color_name, hex_color in palette.items():
        if color_name not in color_mappings:
            continue

        matugen_color = color_mappings[color_name]

        # Handle template with placeholders
        matugen_var = var_template.replace("{color}", matugen_color)
        matugen_var = matugen_var.replace("{scheme}", default_scheme)
        matugen_var = matugen_var.replace("{format}", default_format)

        # Replace hex color in various formats
        patterns = [
            # #hex followed by semicolon (e.g., #f38ba8;)
            (
                re.compile(rf"{re.escape(hex_color)}\s*;", re.IGNORECASE),
                matugen_var + ";",
            ),
            # #hex without semicolon (e.g., #f38ba8)
            (re.compile(rf"{re.escape(hex_color)}\b", re.IGNORECASE), matugen_var),
            # "hex" or 'hex' followed by semicolon (e.g., "#f38ba8";)
            (
                re.compile(rf'["\']{re.escape(hex_color)}["\']\s*;', re.IGNORECASE),
                '"' + matugen_var + '";',
            ),
            # "hex" or 'hex' without semicolon (e.g., "#f38ba8")
            (
                re.compile(rf'["\']{re.escape(hex_color)}["\']', re.IGNORECASE),
                '"' + matugen_var + '"',
            ),
        ]

        for pattern, replacement in patterns:
            if pattern.search(content):
                content = pattern.sub(replacement, content)
                replacements_made.append(f"{color_name} ({hex_color}) -> {matugen_var}")

    # Get header template
    header_template = output_config.get(
        "header",
        f"""/* Converted from Catppuccin {flavor.capitalize()} to matugen template */
/* Generated by catppuccin-to-matugen converter */

""",
    )

    header = header_template.format(
        flavor=flavor, scheme=default_scheme, format=default_format
    )

    # Write the converted content
    try:
        with open(output_file, "w") as f:
            f.write(header + content)
        print(f"Converted theme file: {input_file} -> {output_file}")
        print(f"Made {len(replacements_made)} replacements:")
        for replacement in replacements_made:
            print(f"  - {replacement}")
    except Exception as e:
        print(f"Error writing output file: {e}")


def main():
    """Main function to run the converter."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Convert Catppuccin colors to matugen templates"
    )
    parser.add_argument(
        "--flavor",
        choices=["mocha", "macchiato", "frappe", "latte"],
        help="Catppuccin flavor to convert",
    )
    parser.add_argument(
        "--scheme",
        choices=["light", "dark", "default"],
        default="default",
        help="Color scheme for matugen",
    )
    parser.add_argument(
        "--output", default="./output", help="Output directory for generated files"
    )
    parser.add_argument(
        "--all", action="store_true", help="Generate templates for all flavors"
    )
    parser.add_argument(
        "--convert",
        help="Convert existing theme file with Catppuccin colors",
    )
    parser.add_argument(
        "--convert-output",
        help="Output path for converted theme file (default: input_file + '_matugen')",
    )
    parser.add_argument(
        "--template",
        help="Custom conversion template file (TOML format)",
    )
    parser.add_argument(
        "--create-template",
        help="Create a default conversion template at specified path",
    )
    parser.add_argument(
        "--edit-template",
        action="store_true",
        help="Edit the user template in ~/.config/catppuccin-to-matugen/",
    )

    args = parser.parse_args()

    # Handle template creation/editing
    if args.create_template:
        template_content = get_default_conversion_template()
        if save_conversion_template(args.create_template, template_content):
            print(f"Created template file: {args.create_template}")
        return

    if args.edit_template:
        import subprocess

        user_config_dir = os.path.expanduser("~/.config/catppuccin-to-matugen")
        os.makedirs(user_config_dir, exist_ok=True)
        user_template = os.path.join(user_config_dir, "template.toml")

        # Create template if it doesn't exist
        if not os.path.exists(user_template):
            template_content = get_default_conversion_template()
            save_conversion_template(user_template, template_content)
            print(f"Created user template: {user_template}")

        # Try to open with default editor
        editor = os.environ.get("EDITOR", "nano")
        try:
            subprocess.run([editor, user_template])
        except Exception as e:
            print(f"Could not open editor: {e}")
            print(f"Template file location: {user_template}")
            print("You can edit it manually or set the EDITOR environment variable")
        return

    # Create output directory if it doesn't exist
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(f"{args.output}/templates", exist_ok=True)

    if args.convert:
        # Handle file conversion mode
        if args.convert_output:
            output_file = args.convert_output
        else:
            # Generate default output name
            name, ext = os.path.splitext(args.convert)
            output_file = f"{name}_matugen{ext}"

        convert_theme_file(
            args.convert, output_file, args.flavor, args.scheme, args.template
        )

    elif args.all:
        flavors = ["mocha", "macchiato", "frappe", "latte"]

        # Generate individual templates
        for flavor in flavors:
            template_path = f"{args.output}/templates/{flavor}.css"
            generate_template_file(flavor, template_path, args.scheme)

        # Generate config file
        config_path = f"{args.output}/matugen-catppuccin.toml"
        generate_config_toml(flavors, config_path)

        print(f"\nGenerated all Catppuccin templates in {args.output}/")

    elif args.flavor:
        template_path = f"{args.output}/templates/{args.flavor}.css"
        generate_template_file(args.flavor, template_path, args.scheme)

    else:
        # Default: show CSS variables for mocha flavor
        print(generate_css_variables("mocha", args.scheme))


if __name__ == "__main__":
    main()
