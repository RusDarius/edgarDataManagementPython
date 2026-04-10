"""Script to replace PRESET_SCORING_PROFILES in main analysis file."""

import re

TARGET = "src/data_analysis_scripts/trading_view_move_prediction_analysis.py"
STAGING = "src/data_analysis_scripts/_new_profiles.py"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

with open(STAGING, "r", encoding="utf-8") as f:
    staging = f.read()

# Extract between r''' and '''
start_marker = "PROFILES_BLOCK = r'''\n"
end_marker = "\n'''"
s = staging.find(start_marker)
e = staging.find(end_marker, s + len(start_marker))
if s == -1 or e == -1:
    raise RuntimeError("Could not find PROFILES_BLOCK markers")
new_profiles = staging[s + len(start_marker) : e]

# Find old block boundaries
old_start = content.find("PRESET_SCORING_PROFILES = {")
if old_start == -1:
    raise RuntimeError("Could not find PRESET_SCORING_PROFILES")

old_end_marker = content.find("\ndef _clamp(", old_start)
if old_end_marker == -1:
    raise RuntimeError("Could not find _clamp end marker")

old_block = content[old_start:old_end_marker]
print(f"Old block: {len(old_block)} chars")
print(f"New block: {len(new_profiles)} chars")

new_content = content[:old_start] + new_profiles + content[old_end_marker:]

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(new_content)

print("Replacement done successfully")
