#!/bin/bash
set -e

# Remove all README files
find . -name "README*" -type f -delete
echo "Removed all README files"

# Get all subdirectories (excluding . and ..)
for dir in */; do
	dir="${dir%/}"

	# Skip if not a directory
	[ -d "$dir" ] || continue

	less_file="$dir/catppuccin.user.less"

	# Skip if the less file doesn't exist
	[ -f "$less_file" ] || continue

	echo "Processing: $dir"

	# Move and rename to parent directory as .css
	mv "$less_file" "${dir}.css"

	# Delete the now-empty folder
	rmdir "$dir"

	# Convert using catppuccin_to_m3.py (converts in place by overwriting)
	python3 catppuccin_to_m3.py "${dir}.css" "${dir}.css"

	# Delete the original (pre-conversion file) - but wait, we just converted in place
	# Actually the conversion modifies the file, so there's no separate original to delete
	# Unless user means delete the .css after conversion? That seems wrong.
	# Let me interpret as: the conversion produces output, and we keep that.

	echo "Completed: $dir -> ${dir}.css"
done

echo "All done!"
