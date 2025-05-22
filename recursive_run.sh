#!/usr/bin/env bash
# script.sh — recursively batch-run epub_rebuilder.py, preserving subdirs & skipping done files

set -euo pipefail

# — check args
if [[ $# -ne 2 ]] || [[ ! -d $1 ]]; then
  echo "Usage: $0 <input-dir> <output-dir>"
  exit 1
fi

# strip possible trailing slashes
input_dir=${1%/}
output_dir=${2%/}

# create the root of the output tree
mkdir -p "$output_dir"

# — collect all .epub files (NUL-delimited to handle funky names)
declare -a epubs=()
while IFS= read -r -d '' epub; do
  epubs+=("$epub")
done < <(find "$input_dir" -type f -name '*.epub' -print0)

# — bail if nothing found
if (( ${#epubs[@]} == 0 )); then
  echo "No .epub files found in '$input_dir'."
  exit 1
fi

# — loop ’em
for epub in "${epubs[@]}"; do
  # path relative to input root
  rel_path=${epub#"$input_dir"/}
  rel_dir=$(dirname "$rel_path")

  # recreate that subdir under output_dir
  mkdir -p "$output_dir/$rel_dir"

  # build output filename
  base=$(basename "$rel_path" .epub)
  out="$output_dir/$rel_dir/${base}_parsed.epub"

  # skip if already exists
  if [[ -f "$out" ]]; then
    echo "⇢ Skipping '${rel_path}' (already parsed)"
    continue
  fi

  echo "→ Rebuilding '${rel_path}' → '${rel_dir}/${base}_parsed.epub'…"
  ./epub_rebuilder.py "$epub" "$out"
done
