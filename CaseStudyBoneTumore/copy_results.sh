#!/bin/bash

# Define your destination directory
DEST_DIR="./resultados"
mkdir -p "$DEST_DIR"

# Read through the list of files
while read -r filepath; do
    # Strip out any hidden whitespace/newlines
    filepath=$(echo "$filepath" | tr -d '\r' | awk '{print $1}')
    
    # Skip empty lines
    [ -z "$filepath" ] && continue

    # Extract the parent directory name (e.g., workers_16_studies_100)
    parent_dir=$(basename "$(dirname "$filepath")")
    
    # Extract the base filename (workflow_timing.log)
    filename=$(basename "$filepath")
    
    # Construct the new filename to prevent overwriting
    new_name="${parent_dir}_${filename}"
    
    # Copy the file
    cp "$filepath" "$DEST_DIR/$new_name"
    echo "Copied to $DEST_DIR/$new_name"
    
done < files.txt